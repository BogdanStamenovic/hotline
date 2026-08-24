"""Speech in, speech out. The only part of hotline that touches the GPU.

Discord hands out 48 kHz stereo 16-bit PCM per speaker; Whisper wants 16 kHz mono;
Piper emits 22.05 kHz mono; Discord wants 48 kHz stereo back. Everything here is
that conversion plus the two models, kept behind interfaces flat enough that the
voice bot never sees a sample rate.

**CUDA lives in the venv, not on the machine.** There is no CUDA toolkit and no
cuDNN installed on this box, and installing one system-wide would need root and
about five gigabytes. faster-whisper only needs the cuBLAS and cuDNN *runtimes*,
which ship as pip wheels -- so they are installed into `.venv` and the dynamic
loader is pointed at them at import time. Nothing outside this directory changes,
and deleting the venv undoes all of it.

Everything is lazy. Loading distil-large-v3 takes real time and about 1.5 GB of
VRAM, and the text and phone paths must not pay for that.
"""

from __future__ import annotations

import ctypes
import glob
import logging
import os
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import numpy as np
import soxr

log = logging.getLogger("hotline.audio")

DISCORD_RATE = 48_000
DISCORD_CHANNELS = 2
MODEL_RATE = 16_000
VAD_FRAME = 512  # silero wants exactly this many samples at 16 kHz (32 ms)

# How much trailing silence ends an utterance. Long enough to survive the pause in
# the middle of a sentence, short enough that the reply does not feel late.
SILENCE_TO_END = 0.7
MIN_UTTERANCE = 0.35  # anything shorter is a cough, a click, or a false trigger
MAX_UTTERANCE = 45.0  # a hard stop, so a stuck-open mic cannot buffer forever


# Exactly what ctranslate2 dlopen()s, and nothing else. Loading the whole nvidia
# wheel tree instead segfaulted the process: it picks up libnvblas, which installs
# itself as a BLAS interposer, finds no CPU BLAS to delegate to, and takes the
# interpreter down on the first matrix multiply. The symptom is a bare exit 139
# after "[NVBLAS] CPU Blas library need to be provided" -- worth writing down,
# because nothing about it points at the preload.
_NEEDED = ("libcublas.so", "libcublasLt.so", "libcudnn", "libcudart.so", "libnvrtc.so")


def _preload_cuda_from_wheels() -> int:
    """Point the loader at the CUDA runtimes installed as pip wheels.

    ctranslate2 resolves libcublas and libcudnn by soname. With no system CUDA
    those exist only inside site-packages/nvidia/*/lib, which is not on the search
    path -- so they are loaded RTLD_GLOBAL here, before anything asks. Setting
    LD_LIBRARY_PATH would not work: the loader reads it at process start, and by
    the time Python is running it is far too late.
    """
    dirs: list[str] = []
    for root in (entry for entry in sys.path if entry.endswith("site-packages")):
        dirs += glob.glob(os.path.join(root, "nvidia", "*", "lib"))
    loaded = 0
    for directory in dirs:
        for lib in sorted(glob.glob(os.path.join(directory, "*.so*"))):
            name = os.path.basename(lib)
            if not name.startswith(_NEEDED):
                continue
            try:
                ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)
                loaded += 1
            except OSError:
                continue
    log.debug("preloaded %d CUDA libraries from %d wheel dirs", loaded, len(dirs))
    return loaded


# ---- conversion ---------------------------------------------------------


def stereo48_to_mono16(pcm: bytes) -> np.ndarray:
    """Discord's wire format to the model's, as float32 in [-1, 1]."""
    if not pcm:
        return np.zeros(0, dtype=np.float32)
    samples = np.frombuffer(pcm, dtype="<i2")
    if samples.size % DISCORD_CHANNELS:
        samples = samples[: samples.size - (samples.size % DISCORD_CHANNELS)]
    mono = samples.reshape(-1, DISCORD_CHANNELS).mean(axis=1)
    scaled = (mono / 32768.0).astype(np.float32)
    return soxr.resample(scaled, DISCORD_RATE, MODEL_RATE).astype(np.float32)


def mono_to_stereo48(audio: np.ndarray, rate: int) -> bytes:
    """A model's output back to what Discord will play."""
    if audio.size == 0:
        return b""
    if audio.dtype != np.float32:
        audio = audio.astype(np.float32)
        if audio.max() > 1.5:  # came in as int16-scaled
            audio = audio / 32768.0
    if rate != DISCORD_RATE:
        audio = soxr.resample(audio, rate, DISCORD_RATE)
    clipped = np.clip(audio, -1.0, 1.0)
    as_int16 = (clipped * 32767.0).astype("<i2")
    return np.repeat(as_int16, DISCORD_CHANNELS).tobytes()


# ---- segmentation -------------------------------------------------------


@dataclass
class Utterance:
    audio: np.ndarray  # float32 mono @ 16 kHz
    seconds: float
    started_at: float


class Segmenter:
    """Turns a stream of frames into finished utterances using silero VAD.

    One of these per speaker. Discord gives per-user streams, and mixing them
    before segmentation would produce transcripts of two people at once.
    """

    def __init__(self, threshold: float = 0.5) -> None:
        self.threshold = threshold
        self._model: object | None = None
        self._buffer = np.zeros(0, dtype=np.float32)
        self._voiced: list[np.ndarray] = []
        self._silence = 0.0
        self._speaking = False
        self._started = 0.0

    def _vad(self) -> object:
        if self._model is None:
            from silero_vad import load_silero_vad

            self._model = load_silero_vad()
        return self._model

    def _speech_probability(self, frame: np.ndarray) -> float:
        import torch

        model = self._vad()
        with torch.no_grad():
            return float(model(torch.from_numpy(frame), MODEL_RATE).item())  # type: ignore[operator]

    def feed(self, audio: np.ndarray, now: float | None = None) -> Iterator[Utterance]:
        """Push 16 kHz mono float32; yields whenever an utterance completes."""
        now = time.monotonic() if now is None else now
        self._buffer = np.concatenate([self._buffer, audio])

        while self._buffer.size >= VAD_FRAME:
            frame, self._buffer = self._buffer[:VAD_FRAME], self._buffer[VAD_FRAME:]
            probability = self._speech_probability(frame)
            duration = VAD_FRAME / MODEL_RATE

            if probability >= self.threshold:
                if not self._speaking:
                    self._speaking = True
                    self._started = now
                self._silence = 0.0
                self._voiced.append(frame)
                if self._length() >= MAX_UTTERANCE:
                    done = self._finish()
                    if done:
                        yield done
                continue

            if self._speaking:
                # Keep the trailing silence: cutting at the exact moment speech
                # stops clips the last consonant, and Whisper hears "buil" for
                # "build".
                self._voiced.append(frame)
                self._silence += duration
                if self._silence >= SILENCE_TO_END:
                    done = self._finish()
                    if done:
                        yield done

    def _length(self) -> float:
        return sum(f.size for f in self._voiced) / MODEL_RATE

    def _finish(self) -> Utterance | None:
        audio = np.concatenate(self._voiced) if self._voiced else np.zeros(0, dtype=np.float32)
        seconds = audio.size / MODEL_RATE
        started = self._started
        self._voiced, self._silence, self._speaking = [], 0.0, False
        if seconds < MIN_UTTERANCE:
            return None
        return Utterance(audio=audio, seconds=seconds, started_at=started)

    def flush(self) -> Utterance | None:
        """Called when the speaker leaves; do not strand a half-finished sentence."""
        return self._finish() if self._speaking else None

    @property
    def speaking(self) -> bool:
        return self._speaking


# ---- the models ---------------------------------------------------------


class Transcriber:
    def __init__(
        self,
        model: str = "distil-large-v3",
        device: str = "cuda",
        compute_type: str = "int8_float16",
        language: str = "en",
    ) -> None:
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self._model: object | None = None

    def load(self) -> None:
        if self._model is not None:
            return
        if self.device == "cuda":
            _preload_cuda_from_wheels()
        from faster_whisper import WhisperModel

        began = time.monotonic()
        try:
            self._model = WhisperModel(
                self.model_name, device=self.device, compute_type=self.compute_type
            )
        except Exception as exc:
            # A missing runtime or a full GPU should degrade to a slow call, not a
            # dead one. CPU int8 on this Ryzen is roughly realtime for distil.
            if self.device != "cuda":
                raise
            log.warning("GPU transcriber unavailable (%s); falling back to CPU", exc)
            self.device, self.compute_type = "cpu", "int8"
            self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
        log.info(
            "transcriber %s ready on %s in %.1fs",
            self.model_name, self.device, time.monotonic() - began,
        )

    def transcribe(self, audio: np.ndarray) -> str:
        self.load()
        assert self._model is not None
        segments, _info = self._model.transcribe(  # type: ignore[attr-defined]
            audio,
            language=self.language,
            beam_size=1,          # a spoken command is not worth a beam search
            vad_filter=False,     # already segmented; doing it twice clips words
            condition_on_previous_text=False,  # stops one bad turn poisoning the next
        )
        return " ".join(segment.text for segment in segments).strip()


class Speaker:
    """Piper. CPU-only and roughly thirty times realtime, so it never queues."""

    def __init__(self, voice: str | None = None) -> None:
        self.voice_path = voice or os.environ.get(
            "HOTLINE_PIPER_VOICE",
            os.path.expanduser("~/.local/share/piper-voices/en_GB-alba-medium.onnx"),
        )
        self._voice: object | None = None
        self.rate = 22_050

    def load(self) -> None:
        if self._voice is not None:
            return
        from piper import PiperVoice

        self._voice = PiperVoice.load(self.voice_path)
        config = getattr(self._voice, "config", None)
        self.rate = int(getattr(config, "sample_rate", self.rate) or self.rate)
        log.info("piper voice %s ready at %d Hz", os.path.basename(self.voice_path), self.rate)

    def synthesize(self, text: str) -> np.ndarray:
        """Float32 mono at `self.rate`."""
        self.load()
        assert self._voice is not None
        chunks: list[np.ndarray] = []
        for chunk in self._voice.synthesize(text):  # type: ignore[attr-defined]
            raw = getattr(chunk, "audio_int16_bytes", None)
            if raw is None:
                raw = getattr(chunk, "audio_int16_array", np.zeros(0, dtype="<i2")).tobytes()
            chunks.append(np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0)
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)

    def to_discord(self, text: str) -> bytes:
        return mono_to_stereo48(self.synthesize(text), self.rate)


def warm(transcriber: Transcriber, speaker: Speaker, progress: Callable[[str], None]) -> None:
    """Pay the model-loading cost before a call, not during one."""
    progress("loading the transcriber…")
    transcriber.load()
    progress("loading the voice…")
    speaker.load()
    progress("ready")
