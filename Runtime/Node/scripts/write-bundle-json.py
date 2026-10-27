#!/usr/bin/env python3
"""Emit NodeRuntime.bundle.json, matching the Node 18 bundle metadata contract.

`revision` is a content hash over the archive digest plus the version triple, so
an identical rebuild yields the identical revision (and therefore the same
release tag), while any real change yields a new one. That is how the existing
`runtime-node-<version>-<revision>` tags are formed.

Usage:
  write-bundle-json.py --runtime-root R --node-version 24.22.1 \
      --corepack-version 0.28.0 --archive R.tar.gz --archive-sha <sha> \
      --unpacked-bytes N --file-count N --out bundle.json
"""
import argparse
import hashlib
import io
import json
import os

p = argparse.ArgumentParser()
p.add_argument('--runtime-root', required=True)
p.add_argument('--node-version', required=True)
p.add_argument('--corepack-version', required=True)
p.add_argument('--archive', required=True)
p.add_argument('--archive-sha', required=True)
p.add_argument('--unpacked-bytes', required=True, type=int)
p.add_argument('--file-count', required=True, type=int)
p.add_argument('--out', required=True)
a = p.parse_args()

root = os.path.abspath(a.runtime_root)


def read_version(relpath):
    with io.open(os.path.join(root, relpath), encoding='utf-8') as f:
        return json.load(f)['version']


npm_version = read_version('lib/node_modules/npm/package.json')
corepack_version = read_version('lib/node_modules/corepack/package.json')

# Revision = sha256(archive_sha + node + npm + corepack); stable across rebuilds.
seed = '|'.join([a.archive_sha, a.node_version, npm_version, corepack_version])
revision = hashlib.sha256(seed.encode('utf-8')).hexdigest()

with io.open(os.path.join(root, 'manifest.json'), encoding='utf-8') as f:
    install = json.load(f)['install']

bundle = {
    'archive_bytes': os.path.getsize(a.archive),
    'archive_sha256': a.archive_sha,
    'components': {
        'corepack': corepack_version,
        'fetch': 'minipass-fetch-global',
        'native_addons': 'ios-arm64-napi',
        'node': a.node_version,
        'node-gyp': '10.1.0',
        'npm': npm_version,
        'npx': npm_version,
        'pnpm': 'via-corepack',
        'yarn': 'via-corepack',
    },
    'corepack_version': corepack_version,
    'file_count': a.file_count,
    'format': 1,
    'install': install,
    'minimum_ios': '15.0',
    'node_version': a.node_version,
    'npm_version': npm_version,
    'payload_bytes': a.unpacked_bytes,
    'revision': revision,
    'runtime_id': 'node',
    'source_revision': os.environ.get('GITHUB_SHA', ''),
    'tar_bytes': a.unpacked_bytes,
}

with io.open(a.out, 'w', encoding='utf-8') as f:
    json.dump(bundle, f, indent=2, sort_keys=True, ensure_ascii=False)
    f.write('\n')

print('wrote %s' % a.out)
print('  node=%s npm=%s corepack=%s' % (a.node_version, npm_version, corepack_version))
print('  revision=%s' % revision)
