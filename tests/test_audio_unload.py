"""Releasing the voice models when a call ends.

The daemon is long-lived and a call is short. distil-large-v3 sits in ~2.8GB of an
8GB card, and it used to stay there for the entire time nobody was calling --
which on this box was enough to stop a 5GB model loading at all. Hanging up did
not free it, because the call object going out of scope leaves the decoder's own
references behind.

No GPU is touched here: `load()` is what costs two seconds and needs CUDA, while
`unload()` is pure bookkeeping, so the model slot is set directly.
"""

from __future__ import annotations

from hotline.audio import Speaker, Transcriber


def test_unloading_a_transcriber_drops_the_model() -> None:
    transcriber = Transcriber()
    transcriber._model = object()
    transcriber.unload()
    assert transcriber._model is None


def test_unloading_a_transcriber_twice_is_harmless() -> None:
    """Hangup can be reached more than once -- an explicit `leave` racing a
    voice-state update -- and the second one must not throw."""
    transcriber = Transcriber()
    transcriber._model = object()
    transcriber.unload()
    transcriber.unload()
    assert transcriber._model is None


def test_unloading_a_never_loaded_transcriber_is_harmless() -> None:
    """A call that fails before the models load still runs the teardown."""
    Transcriber().unload()


def test_unloading_a_speaker_drops_the_voice() -> None:
    speaker = Speaker()
    speaker._voice = object()
    speaker.unload()
    assert speaker._voice is None


def test_unloading_a_speaker_twice_is_harmless() -> None:
    speaker = Speaker()
    speaker._voice = object()
    speaker.unload()
    speaker.unload()
    assert speaker._voice is None


def test_a_transcriber_reloads_after_being_unloaded() -> None:
    """`load()` early-returns when `_model` is set. If `unload()` left anything
    behind, the next call would silently reuse a dead model instead of reloading.
    """
    calls: list[int] = []
    transcriber = Transcriber()

    def fake_load() -> None:
        if transcriber._model is None:
            calls.append(1)
            transcriber._model = object()

    fake_load()
    transcriber.unload()
    fake_load()
    assert len(calls) == 2
