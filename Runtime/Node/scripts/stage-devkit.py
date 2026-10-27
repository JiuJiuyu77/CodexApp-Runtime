#!/usr/bin/env python3
"""Stage devkit/node/include/node from a Node source tree.

node-gyp needs the public Node headers to compile native addons, and the Node 18
runtime ships them under devkit/node/include/node. This mirrors the header set
Node's own tools/install.py installs, without invoking `make install` (which
would rebuild and try to link a host executable during a cross build).

Usage: stage-devkit.py <node-src> <runtime-root>
"""
import os
import shutil
import sys

src, root = sys.argv[1], sys.argv[2]
dst = os.path.join(root, 'devkit/node/include/node')
if os.path.isdir(dst):
    shutil.rmtree(dst)
os.makedirs(dst)

copied = 0


def copy_tree(from_path, to_path, suffix=None):
    """Copy files (optionally filtered by suffix), preserving subdirectories."""
    global copied
    if not os.path.isdir(from_path):
        return
    for entry in sorted(os.listdir(from_path)):
        source = os.path.join(from_path, entry)
        target = os.path.join(to_path, entry)
        if os.path.isdir(source):
            copy_tree(source, target, suffix)
            continue
        if suffix and not entry.endswith(suffix):
            continue
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1


# Node's public API headers (src/*.h, plus src/cppgc and src/libplatform).
src_inc = os.path.join(src, 'src')
for entry in sorted(os.listdir(src_inc)):
    source = os.path.join(src_inc, entry)
    if os.path.isfile(source) and entry.endswith('.h'):
        shutil.copy2(source, os.path.join(dst, entry))
        copied += 1
copy_tree(os.path.join(src_inc, 'cppgc'), os.path.join(dst, 'cppgc'), '.h')
copy_tree(os.path.join(src_inc, 'libplatform'), os.path.join(dst, 'libplatform'), '.h')

# V8, libuv and OpenSSL headers, which addon authors include directly.
copy_tree(os.path.join(src, 'deps/v8/include'), dst, '.h')
copy_tree(os.path.join(src, 'deps/uv/include'), dst, '.h')
copy_tree(os.path.join(src, 'deps/openssl/openssl/include/openssl'),
          os.path.join(dst, 'openssl'), '.h')
zconf = os.path.join(src, 'deps/zlib/zconf.h')
if os.path.isfile(zconf):
    shutil.copy2(zconf, os.path.join(dst, 'zconf.h'))
    copied += 1

# gyp configuration node-gyp reads to discover build flags for the target.
for name in ('common.gypi', 'config.gypi'):
    path = os.path.join(src, name)
    if not os.path.isfile(path):
        raise SystemExit('stage-devkit: missing %s in %s' % (name, src))
    shutil.copy2(path, os.path.join(dst, name))

for required in ('node.h', 'node_api.h', 'common.gypi', 'config.gypi', 'uv.h'):
    if not os.path.isfile(os.path.join(dst, required)):
        raise SystemExit('stage-devkit: %s missing from devkit' % required)

print('stage-devkit: %d headers staged into %s' % (copied, dst))
