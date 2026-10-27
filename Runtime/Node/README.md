# Node.js Runtime (iOS)

Builds the `NodeRuntime` bundle served by `runtime-manifest.json` (`runtimes.node`).

## What ships

| Path | Purpose |
| --- | --- |
| `bin/node` | Small launcher linked against `NodeMobile.framework`. |
| `bin/{npm,npx,corepack,pnpm,pnpx,yarn,yarnpkg,node-gyp}` | Symlinks to `bin/node`. |
| `lib/node_modules/npm` | npm, copied verbatim from the Node tarball. |
| `lib/node_modules/corepack` | Corepack, from the npm registry. |
| `lib/codex/*.js` | CodexApp bootstrap shims (fetch, spawn, `dlopen`, node-gyp). |
| `devkit/node/include/node` | Public headers + `common.gypi`/`config.gypi` for node-gyp. |
| `Frameworks/NodeMobile.framework` | Node itself, as a framework. |
| `manifest.json` | Component versions, install probe, required paths. |

## How it is built

`.github/workflows/build-node24.yml` runs on a macOS runner with a real
iPhoneOS SDK:

1. Download the pinned Node release tarball and verify it against
   `SHASUMS256.txt`.
2. Apply `patches/apply-ios-patches.py` — the small set of edits Node needs to
   cross-compile for iOS.
3. `./configure --dest-os=ios --dest-cpu=arm64 --with-intl=none
   --cross-compiling --enable-static --openssl-no-asm --v8-options=--jitless
   --without-node-code-cache --without-node-snapshot`.

   iOS never grants W+X pages, so V8 must run JIT-less; snapshots and code-cache
   blobs are pre-generated with a host binary, so they are disabled too.
4. `make` produces `out/Release/libnode.a` plus the V8/uv/openssl archives.
5. `libnode.a` is force-loaded into a `-dynamiclib` together with the dependency
   archives, yielding `NodeMobile.framework`.
6. `bin/node` is linked against that framework, npm and corepack are installed,
   the CodexApp shims and node-gyp headers are staged, `manifest.json` is
   emitted, and the tree is archived deterministically (sorted entries, zeroed
   owner/mtime) so an unchanged rebuild reproduces the same digest.

## Release naming

Tags are `runtime-node-<version>-<revision>`, where `revision` is
`sha256(archive_sha256 | node | npm | corepack)`. An identical rebuild reuses the
tag; any real change produces a new one rather than overwriting an existing
release.

## Running it

```
gh workflow run build-node24.yml --repo <owner>/CodexApp-Runtime \
  -f node_version=24.22.1 -f publish=true
```

Then point `runtimes.node` in `Runtime/runtime-manifest.json` at the new
`archive_url`, `metadata_url`, `archive_sha256` and `revision` from
`NodeRuntime.bundle.json`.
