"""Wake-on-LAN, and knowing whether it worked.

A magic packet is six 0xFF bytes followed by the target MAC repeated sixteen
times, broadcast to the local segment. Pigion and archserver are on the same /24
and the same layer-2 domain, so a broadcast reaches -- no directed-broadcast
routing needed.

**This works, and has since 2026-08-27.** It is the primary way archserver comes
back, and it is exercised routinely -- on 2026-09-19 the box was powered off at
14:50 CEST and woken from Pigion at 18:00, with nothing else able to have started
it. `enp4s0` is up with carrier, MAC `a8:a1:59:fd:4d:13`, and `Wake-on: g`.

This paragraph used to say the opposite -- "has never woken anything, by design",
the cable unplugged, the end-to-end wake UNVERIFIED-BY-DESIGN. That was true when
written and stayed in the file for weeks after it stopped being true, which is
the more useful lesson: a doc claiming something is impossible outlives the
impossibility and then misleads whoever reads it next.

**Checking it before a poweroff:** `ethtool enp4s0 | grep Wake-on` must be read
**with sudo**. Unprivileged it prints nothing at all for that field -- not "off",
*nothing* -- so an empty result is a failed check, never a negative one.

Two BIOS settings gate it, and neither can be set remotely on an ASRock
B550M-HVS SE (no IPMI): ErP/ErP Ready **disabled**, and PCIE Devices Power On /
PME Event Wake Up **enabled**. Both are currently set correctly -- worth knowing
if the CMOS is ever cleared, because the symptom is a box that simply never
comes back.
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
