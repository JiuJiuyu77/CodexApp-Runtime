#!/usr/bin/env python3
"""Point runtime-manifest.json's node runtime at a freshly published bundle.

Reads NodeRuntime.bundle.json (the build's own metadata) and rewrites the
`runtimes.node` entry so CodexApp installs the new build. Every field is derived
from the bundle, so the manifest can never drift from what was actually shipped.

Usage:
  update-manifest.py <runtime-manifest.json> <bundle.json> <release-tag> <repo>
"""
import io
import json
import sys

manifest_path, bundle_path, tag, repo = sys.argv[1:5]

with io.open(manifest_path, encoding='utf-8') as f:
    manifest = json.load(f)
with io.open(bundle_path, encoding='utf-8') as f:
    bundle = json.load(f)

node = manifest['runtimes']['node']
version = 'runtime-node-%s-%s' % (bundle['node_version'], bundle['revision'][:12])
base = 'https://github.com/%s/releases/download/%s' % (repo, tag)

node['version'] = version
node['archive_url'] = '%s/NodeRuntime.tar.gz' % base
node['metadata_url'] = '%s/NodeRuntime.bundle.json' % base
node['archive_sha256'] = bundle['archive_sha256']
node['revision'] = bundle['revision']
node['source_revision'] = bundle.get('source_revision', node.get('source_revision', ''))
node['node_version'] = bundle['node_version']
node['npm_version'] = bundle['npm_version']
node['corepack_version'] = bundle['corepack_version']
node['components'] = bundle['components']
node['download_size'] = bundle['archive_bytes']
node['unpacked_size'] = bundle['payload_bytes']
node['minimum_ios'] = bundle.get('minimum_ios', '15.0')
node['install'] = bundle['install']
node['summary'] = (
    '完整 Node.js %s/npm 开发环境：npm、npx、Corepack、pnpm、Yarn、纯 JS global '
    'fetch、child_process 与 iOS arm64 node-gyp/N-API，并支持持久化全局 CLI。'
    % bundle['node_version']
)

# Capability summaries name the Node version in the UI.
caps = node.setdefault('capabilities', {})
if 'node.javascript' in caps:
    caps['node.javascript']['summary'] = (
        'Node.js %s JavaScript runtime.' % bundle['node_version'])

with io.open(manifest_path, 'w', encoding='utf-8') as f:
    json.dump(manifest, f, indent=2, sort_keys=True, ensure_ascii=False)
    f.write('\n')

print('node runtime -> %s' % version)
print('  url: %s' % node['archive_url'])
print('  sha256: %s' % node['archive_sha256'][:16] + '...')
print('  node %s / npm %s / corepack %s'
      % (bundle['node_version'], bundle['npm_version'], bundle['corepack_version']))
