#!/usr/bin/env python3
"""Create a deterministic .tar.gz of a runtime tree.

Shelling out to `tar` is not portable enough here: macOS ships bsdtar, which
does not accept GNU's --owner/--group, and its --mtime does not take an epoch.
Building the archive with tarfile instead lets us pin every field that would
otherwise vary between runs, so an unchanged tree always produces the same
digest and therefore the same release tag.

Pinned per entry: uid/gid 0, empty uname/gname, mtime 0, sorted order. File
mode and symlinks are preserved exactly as staged.

Usage: make-archive.py <runtime-root> <output.tar.gz>
"""
import gzip
import io
import os
import sys
import tarfile

root, out = os.path.abspath(sys.argv[1]), sys.argv[2]

entries = []
for base, dirs, files in os.walk(root):
    dirs.sort()
    for name in sorted(dirs) + sorted(files):
        full = os.path.join(base, name)
        entries.append(os.path.relpath(full, root))
entries.sort()

count = 0
with open(out, 'wb') as raw:
    # mtime=0 keeps the gzip header constant between runs.
    with gzip.GzipFile(fileobj=raw, mode='wb', compresslevel=9, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode='w', format=tarfile.GNU_FORMAT) as tar:
            for rel in entries:
                full = os.path.join(root, rel)
                info = tar.gettarinfo(full, arcname=rel)
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                info.mtime = 0
                if info.isreg():
                    with open(full, 'rb') as f:
                        tar.addfile(info, f)
                else:
                    # Symlinks and directories carry no payload.
                    tar.addfile(info)
                count += 1

print('make-archive: %d entries -> %s' % (count, out))
