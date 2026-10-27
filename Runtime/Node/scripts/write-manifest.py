#!/usr/bin/env python3
"""Emit <runtime-root>/manifest.json, matching the Node 18 runtime contract.

CodexApp reads this file after unpacking to learn component versions, the probe
used to validate the install, and the paths that must exist. The probe is
version-agnostic: it only asserts npm/corepack match the recorded versions, so
it keeps working across Node upgrades.

Usage:
  write-manifest.py --root <runtime-root> --node-version 24.22.1 \
                    --corepack-version 0.28.0
"""
import argparse
import io
import json
import os

arg = argparse.ArgumentParser()
arg.add_argument('--root', required=True)
arg.add_argument('--node-version', required=True)
arg.add_argument('--corepack-version', required=True)
arg.add_argument('--out', default=None)
a = arg.parse_args()

root = os.path.abspath(a.root)


def read_version(relpath):
    with io.open(os.path.join(root, relpath), encoding='utf-8') as f:
        return json.load(f)['version']


# Versions come from the tree itself; the build pins them during install.
npm_version = read_version('lib/node_modules/npm/package.json')
corepack_version = read_version('lib/node_modules/corepack/package.json')

if corepack_version != a.corepack_version:
    raise SystemExit('corepack mismatch: tree has %s, expected %s'
                     % (corepack_version, a.corepack_version))

probe = (
    "const fs=require('fs');"
    "const p=require('./lib/node_modules/npm/package.json');"
    "const c=require('./lib/node_modules/corepack/package.json');"
    "if(p.version!=='%s'||c.version!=='%s')process.exit(43);"
    "console.log('codex-node-runtime-ok '+process.version+' npm='+p.version+' corepack='+c.version)"
    % (npm_version, corepack_version)
)

manifest = {
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
    'format': 1,
    'install': {
        'environment': {'CA_TOOL_NODE_RUNTIME_ROOT': '${RUNTIME_ROOT}'},
        'path_entries': ['bin'],
        'persistent_path_entries': [
            'Library/Application Support/CodexApp/Node/prefix/bin',
        ],
        'probe': {
            'arguments': ['-e', probe],
            'contains': 'codex-node-runtime-ok',
            'executable': 'bin/node',
        },
        'required_paths': [
            'bin/node', 'bin/npm', 'bin/npx', 'bin/corepack', 'bin/pnpm',
            'bin/pnpx', 'bin/yarn', 'bin/yarnpkg', 'bin/node-gyp',
            'lib/node_modules/npm/bin/npm-cli.js',
            'lib/node_modules/npm/bin/npx-cli.js',
            'lib/node_modules/corepack/dist/corepack.js',
            'lib/node_modules/corepack/dist/pnpm.js',
            'lib/node_modules/corepack/dist/yarn.js',
            'lib/codex/ios-bootstrap.js',
            'lib/codex/native-support.js',
            'lib/codex/fetch-compat.js',
            'lib/codex/node-gyp-ios.js',
            'devkit/node/include/node/node.h',
            'devkit/node/include/node/node_api.h',
            'devkit/node/include/node/common.gypi',
            'devkit/node/include/node/config.gypi',
            'Frameworks/NodeMobile.framework/NodeMobile',
        ],
        # Signing contract: bin/node takes the app's entitlements, the framework
        # must be signed without them (it is only ever dlopen'd).
        'sign_paths': ['bin/node', 'Frameworks/NodeMobile.framework/NodeMobile'],
        'sign_paths_without_entitlements': [
            'Frameworks/NodeMobile.framework/NodeMobile',
        ],
    },
    'minimum_ios': '15.0',
    'runtime_id': 'node',
    'source_revision': os.environ.get('GITHUB_SHA', ''),
}

out = a.out or os.path.join(root, 'manifest.json')
with io.open(out, 'w', encoding='utf-8') as f:
    json.dump(manifest, f, indent=2, sort_keys=True, ensure_ascii=False)
    f.write('\n')
print('wrote %s (node %s, npm %s, corepack %s)'
      % (out, a.node_version, npm_version, corepack_version))
