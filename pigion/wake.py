"""Wake-on-LAN, and knowing whether it worked.

A magic packet is six 0xFF bytes followed by the target MAC repeated sixteen
times, broadcast to the local segment. Pigion and archserver are on the same /24
and the same layer-2 domain, so a broadcast reaches -- no directed-broadcast
routing needed.

**This has never woken anything, by design.** archserver's `enp4s0` is
`NO-CARRIER`: the ethernet cable is not plugged in and will not be for a while.
Bogdan's explicit instruction was to build the wake layer as though it works, make
the OS side self-arm when carrier appears, and not block on it. So what is tested
here is that the correct bytes leave Pigion; the end-to-end wake is
**UNVERIFIED-BY-DESIGN** and is marked as such everywhere it is reported.

Two BIOS settings also gate it, and neither can be set remotely on an ASRock
B550M-HVS SE (no IPMI): ErP/ErP Ready **disabled**, and PCIE Devices Power On /
PME Event Wake Up **enabled**.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request

DEFAULT_PORT = 9


def magic_packet(mac: str) -> bytes:
    """Build the packet. Accepts `a8:a1:59:fd:4d:13`, dashes, or bare hex."""
    cleaned = mac.replace(":", "").replace("-", "").replace(".", "").strip()
    if len(cleaned) != 12:
        raise ValueError(f"not a MAC address: {mac!r}")
    raw = bytes.fromhex(cleaned)
    return b"\xff" * 6 + raw * 16


def send(mac: str, broadcast: str = "255.255.255.255", port: int = DEFAULT_PORT) -> int:
    """Broadcast one magic packet. Returns the number of bytes sent.

    Sent to the subnet broadcast *and* the all-ones broadcast, on both port 9 and
    port 7. Which of these a given NIC and switch will honour is not knowable from
    here, they cost a few hundred bytes, and the whole point is that nothing on the
    far side is awake to tell us we guessed wrong.
    """
    packet = magic_packet(mac)
    total = 0
    targets = {(broadcast, port), ("255.255.255.255", port), (broadcast, 7)}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for address, target_port in targets:
            try:
                total += sock.sendto(packet, (address, target_port))
            except OSError:
                continue
    return total


def is_awake(health_url: str, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(health_url, timeout=timeout) as response:
            return bool(json.loads(response.read()).get("ok"))
    except (urllib.error.URLError, OSError, ValueError):
        return False


def wake_and_wait(
    mac: str,
    health_url: str,
    broadcast: str = "255.255.255.255",
    deadline: float = 90.0,
    resend_every: float = 15.0,
) -> bool:
    """Send the packet and wait for the far side to answer /health.

    Resent periodically rather than once: a machine that is still POSTing has no
    network stack to receive anything, and a single packet at the wrong moment is
    indistinguishable from a broken setup.
    """
    if is_awake(health_url):
        return True
    started = time.monotonic()
    last_sent = 0.0
    while time.monotonic() - started < deadline:
        now = time.monotonic()
        if now - last_sent >= resend_every:
            send(mac, broadcast)
            last_sent = now
        time.sleep(2.0)
        if is_awake(health_url):
            return True
    return False
