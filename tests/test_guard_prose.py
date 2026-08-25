"""The guard must not fire on prose that merely names the commands it guards.

This is not a hypothetical. The first version matched raw substrings and denied
four legitimate tool calls during the build: writing `tests/test_guard.py`, and
three attempts to write documentation and a status message explaining the guard
itself. A denylist people have to route around teaches the habit of routing around
it, which is the opposite of what it is for.

Kept separate from `test_guard.py` because that file must stay free of the literals
in order to be writable at all; here they are the point, so everything is encoded.
"""

from __future__ import annotations

import base64

import pytest

from hotline.guard import check


def d(value: str) -> str:
    return base64.b64decode(value).decode()


# Prose and heredocs that name the commands without running them.
PROSE = [
    # cat > notes.md <<'EOF'\nWriting about mkfs and rm -rf / is not running them.\nEOF
    "Y2F0ID4gbm90ZXMubWQgPDwnRU9GJwpXcml0aW5nIGFib3V0IG1rZnMgYW5kIHJtIC1yZiAvIGlzIG5vdCBydW5uaW5nIHRoZW0uCkVPRg==",
    # echo "never run mkfs.ext4 on a mounted disk"
    "ZWNobyAibmV2ZXIgcnVuIG1rZnMuZXh0NCBvbiBhIG1vdW50ZWQgZGlzayI=",
    # grep -rn "wipefs" docs/
    "Z3JlcCAtcm4gIndpcGVmcyIgZG9jcy8=",
    # git commit -m "document why mkfs is blocked"
    "Z2l0IGNvbW1pdCAtbSAiZG9jdW1lbnQgd2h5IG1rZnMgaXMgYmxvY2tlZCI=",
    # python3 -c "print('the guard blocks mkfs and dd of=/dev/sda')"
    "cHl0aG9uMyAtYyAicHJpbnQoJ3RoZSBndWFyZCBibG9ja3MgbWtmcyBhbmQgZGQgb2Y9L2Rldi9zZGEnKSI=",
    # man mkfs
    "bWFuIG1rZnM=",
    # which mkfs.ext4
    "d2hpY2ggbWtmcy5leHQ0",
]

# Real invocations, including the obvious ways of hiding one.
REAL = [
    "bWtmcy5leHQ0IC9kZXYvbnZtZTBuMXA5",  # mkfs.ext4 /dev/nvme0n1p9
    "c3VkbyBta2ZzLmV4dDQgL2Rldi9zZGE=",  # sudo mkfs.ext4 /dev/sda
    "L3NiaW4vbWtmcy5leHQ0IC9kZXYvc2Rh",  # /sbin/mkfs.ext4 /dev/sda
    "YmFzaCAtYyAibWtmcy5leHQ0IC9kZXYvc2RhIg==",  # bash -c "mkfs.ext4 /dev/sda"
    "c2ggLWMgJ3JtIC1yZiAvJw==",  # sh -c 'rm -rf /'
    "Y2QgL3RtcCAmJiBzdWRvIHdpcGVmcyAtYSAvZGV2L3NkYQ==",  # cd /tmp && sudo wipefs -a /dev/sda
    "cm0gLXJmIC8=",  # rm -rf /
    "cm0gLXJmIC8q",  # rm -rf /*
    "ZGQgaWY9L2Rldi96ZXJvIG9mPS9kZXYvc2RhIGJzPTFN",  # dd if=/dev/zero of=/dev/sda bs=1M
    "ZWNobyB4ID4gL2Rldi9zZGE=",  # echo x > /dev/sda
    "c2dkaXNrIC0temFwLWFsbCAvZGV2L3NkYQ==",  # sgdisk --zap-all /dev/sda
    "c2hyZWQgLW4xIC9kZXYvc2Ri",  # shred -n1 /dev/sdb
]

# Ordinary work that must never be touched.
ORDINARY = [
    "cm0gLXJmIC4vYnVpbGQ=",
    "cm0gLXJmIC9ob21lL2JvZGFzL2RhdGEvaG90bGluZS8udGVzdGJlZA==",
    "cm0gLXJmIC90bXAvZm9v",
    "Y2QgL3RtcC94ICYmIHJtIC1yZiAu",
    "Z2l0IHJlc2V0IC0taGFyZA==",
    "bHMgLWxhIC8=",
    "ZGQgaWY9L2Rldi9zZGEgb2Y9L2JhY2t1cC9kaXNrLmltZw==",  # imaging a disk TO a file is fine
    "cGFydGVkIC1s",  # parted -l just lists
    "cnN5bmMgLWEgLyAvbW50L2JhY2t1cC8=",
]


@pytest.mark.parametrize("encoded", PROSE)
def test_writing_about_a_command_is_not_running_it(encoded: str) -> None:
    assert check("Bash", {"command": d(encoded)}) is None, d(encoded)


@pytest.mark.parametrize("encoded", REAL)
def test_real_invocations_are_still_blocked(encoded: str) -> None:
    assert check("Bash", {"command": d(encoded)}) is not None, d(encoded)


@pytest.mark.parametrize("encoded", ORDINARY)
def test_ordinary_work_is_untouched(encoded: str) -> None:
    assert check("Bash", {"command": d(encoded)}) is None, d(encoded)


def test_unparseable_quoting_is_not_treated_as_dangerous() -> None:
    """An unbalanced quote means we cannot know what would run. Blocking every
    such command would make the guard fire constantly on ordinary shell one-liners
    with awkward quoting, which is the failure mode this whole redesign is about."""
    assert check("Bash", {"command": "echo 'unterminated"}) is None
