# Rolling back this box

## What snapshot zero covers

`2026-08-24_17-31-50` — tag `O` (on-demand), 8.7 GB, RSYNC mode, taken before any
hotline change touched the system. Verified faithful, not just "reported success":
`rsync -ain --delete /etc/ <snap>/etc/` returns **zero** content differences, and
400 randomly sampled files across `/etc`, `/usr/local` and `/usr/lib/systemd/system`
match by sha256 with **zero** mismatches.

Config lives at `/etc/timeshift/timeshift.json` (original preserved as
`timeshift.json.orig`). Schedule: daily, keep 3, plus on-demand.

## What it does NOT cover — read this before relying on it

The snapshot is stored on **`/dev/nvme0n1p4`, the same partition it protects.**
There is exactly one Linux partition on this machine (p1 ESP 100M, p3/p5 NTFS,
p4 root 73G) and no second disk, so there is nowhere else to put it.

- Protects against: a bad package, a broken unit file, an `/etc` edit that bricks
  boot, a botched driver install. **This is the entire hotline threat model.**
- Does **not** protect against: disk failure, ext4 corruption, `mkfs`, or anything
  that takes p4 with it. For that you need an external disk, and there isn't one.

`/home` contents are excluded (structure kept, files not). So is `/var/cache`,
`/var/log/journal` and `/opt/claude-code`. Home is backed up per-path by
`hotline-backup` instead — see below. `/opt/claude-code` is excluded deliberately:
a rollback should not drag the Claude CLI backwards, since the whole hotline
depends on it and it is independently reinstallable.

## Restoring

Timeshift has **no `--dry-run` for restore**, so restore itself is untested — it
cannot be exercised on a live root without actually doing it. The snapshot's
*fidelity* is proven; the restore path is trusted on timeshift's reputation.

From a working system:

    sudo timeshift --restore --snapshot '2026-08-24_17-31-50' --skip-grub

`--skip-grub` matters: this box boots via the ESP at `/boot/efi` (vfat, 100M) and
you do not want timeshift reinstalling a bootloader it guessed at.

If the box will not boot, do it from an Arch live USB:

    mount /dev/nvme0n1p4 /mnt
    mount /dev/nvme0n1p1 /mnt/boot/efi
    arch-chroot /mnt
    timeshift --restore --snapshot '2026-08-24_17-31-50' --skip-grub

## Per-path backups (rule 7)

`hotline-backup <path>...` tars each path into
`~/data/hotline/backups/<name>.<timestamp>.tar.zst` and prints the archive path.
Use it before editing anything under `/home` or any single system file, regardless
of the snapshot. Restore with `tar --zstd -xf <archive> -C /`.

## Known cosmetic fault

Every `timeshift --create` ends with:

    /tmp/timeshift-XXXX/<digits>: line 10: status: No such file or directory

This is timeshift's own generated post-snapshot script calling a `status` command
that does not exist on Arch. It fires **after** the snapshot is written and the
control file is saved; the snapshot is complete and valid. Cosmetic. Ignore it.
