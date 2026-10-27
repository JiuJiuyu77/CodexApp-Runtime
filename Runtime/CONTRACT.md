# Runtime contract (manifest schema 4)

This directory publishes runtime **assets** for CodexApp and for any other host
that wants to consume them (see `Runtime/minis/`). Nothing here is app source
code, so the manifest and the per-runtime bundle metadata are the contract.
Everything below is derived from `runtime-manifest.json`, the `*.bundle.json`
sidecars it points at, and the runtime sources in this repository.

## Top level

| Field | Meaning |
| --- | --- |
| `schema` | `4`. A consumer must refuse any other value. |
| `source_format`, `source_name` | Provenance of the manifest that produced this file. |
| `download_proxy_prefixes` | Ordered prefixes; a download is `<prefix><absolute-url>`. Keep the trailing `/`. They exist because raw GitHub release downloads are unreliable on some networks. |
| `runtimes` | `id -> runtime object`. |

## Runtime object

| Field | Notes |
| --- | --- |
| `version`, `revision` | Release identity. `revision` is 64-hex `sha256(archive_sha256 | components…)`; an identical rebuild reuses it, any real change bumps it. Optional for app-native handlers (`codex` publishes `release_tag` instead). |
| `archive_url`, `archive_sha256`, `download_size`, `unpacked_size` | Release asset plus digest and disk budget. Verify the digest before unpacking. |
| `metadata_url` | The `*.bundle.json` this entry was generated from; regenerate the entry from it rather than hand-editing numbers. |
| `handler` | Who installs and executes the tree: `portable-v1`, `codex`, `python`, `theos`, `reverse_tools`. |
| `provides`, `capabilities` | Capability ids plus prose. `capabilities.<id>.requires[]` declares cross-runtime needs as `{id, provides, reason}`. |
| `requires`, `recommends`, `conflicts` | Runtime-level graph. `recommends` must carry a `reason` and never blocks an install. |
| `optional`, `order`, `group` | Install ordering (core 5–10, tools 15, compiler 20) and whether the app may skip the runtime. |
| `minimum_ios`, `runtime_format` | OS floor and tree-layout version (`theos` uses `runtime_format: 11`). |
| `ui.symbol`, `ui.tint` | SF Symbol and tint for the install UI. |

### `install` block

Required for `portable-v1`; app-native handlers install themselves.

| Field | Meaning |
| --- | --- |
| `required_paths` | Relative paths that must exist after unpacking — the completeness gate. |
| `path_entries` | Directories prepended to `PATH` (usually `["bin"]`). |
| `persistent_path_entries` | Global CLI prefix preserved across runtime updates (node: `Library/Application Support/CodexApp/Node/prefix/bin`). |
| `environment` | Environment for the tree, with `${RUNTIME_ROOT}` placeholders. |
| `probe` | `{executable, arguments, contains}` — install passes only if the executable runs and its output contains the marker. Offline and version-asserting. |
| `sign_paths`, `sign_paths_without_entitlements` | Mach-O files needing signing; the second list is signed without entitlements (a dylib framework breaks with them). |

## Capability vocabulary

`core.*` (git, search, http, json, file, diff, patch, archive) ·
`node.*` (javascript, packages, fetch, process, native-addon) ·
`build.*` (cmake, ninja, cmake-ios) ·
`ios.*` (toolchain, compile, link, package, package.rootful, package.rootless, package.roothide) ·
`compiler.ollvm.*` · `python.*` · `reverse.*` (radare2, macho, inject) ·
`codex.*` (cli, app-server, code-mode).

## Node runtime (`handler: portable-v1`, `NodeMobile.framework`)

Tree: `bin/node` (small launcher linked against the framework),
`bin/{npm,npx,corepack,pnpm,pnpx,yarn,yarnpkg,node-gyp}` as symlinks to it,
`lib/node_modules/{npm,corepack}`, `lib/codex/*.js` bootstrap shims,
`devkit/node/include/node` for node-gyp,
`Frameworks/NodeMobile.framework/NodeMobile`, and `manifest.json`.

Build contract (`.github/workflows/build-node24.yml`): cross-compile with
`--dest-os=ios --dest-cpu=arm64 --enable-static --openssl-no-asm
--v8-options=--jitless --without-node-code-cache --without-node-snapshot`, then
force-load `libnode.a` into a `-dynamiclib` to produce the framework, and archive
deterministically (sorted entries, zeroed owner/mtime) so a rebuild reproduces the
digest. iOS never grants W+X pages, so V8 runs JIT-less and snapshots/code-cache
blobs (host-generated) are disabled.

Shim contracts (`lib/codex/*.js`, loaded before user code):

- `native-support.js` — `runtimeRoot()` is `CA_TOOL_NODE_RUNTIME_ROOT` or
  `dirname(execPath)/..`; `preferredShell()` is `$SHELL || $CA_THEOS_BASH` and must
  exist; detects Mach-O `LC_CODE_SIGNATURE`; `stageAddonForLoad()` copies an
  unsigned `.node` into `lib/.native-addons/<pid>-<thread>` inside the app bundle
  (iOS refuses executable mmap outside it) and signs it through
  `CodexApp -agent-sign-node-addons <dir> <base64 names>`.
- `ios-bootstrap.js` — swaps in the pure-JS fetch path (JIT-less V8 has no
  WebAssembly), forces `child_process` `shell` to `preferredShell()` and sets
  `npm_config_script_shell`, and only when `CA_THEOS_SDKROOT` **and**
  `CA_THEOS_CLANG` exist exports `SDKROOT`, `CC`, `CXX`, `MAKE`,
  `IPHONEOS_DEPLOYMENT_TARGET=15.0` and
  `CFLAGS/CXXFLAGS/LDFLAGS` (`-arch arm64 -isysroot … -miphoneos-version-min=15.0`,
  `-Wl,-no_fixup_chains`) plus `GYP_DEFINES`.
- `fetch-compat.js` — `install(runtimeRoot)` installs the npm-compatible fetch.
- `node-gyp-ios.js` — points node-gyp at `devkit/node` and the iOS target.

## Codex runtime (`handler: codex`)

Rootless `iphoneos-arm64` deb built from the `owngoal-dev/codex` packaging pinned
in `Codex/packaging-ref.txt`, version from `Codex/package-version.txt`, published
with `SHA256SUMS`. Two iOS patches:

- `Codex/0005-ios-native-posix-spawn.patch` — extends the Apple native
  `posix_spawn` PTY backend to `target_os = "ios"`, so unified exec never falls
  back to `fork`/`pre_exec`, which TrollStore cannot use.
- `Codex/0006-ios-poll-native-child-exit.patch` — SIGCHLD may not arrive through
  Tokio's signal stream on a forkless launch, so iOS polls `waitpid` every 100 ms
  instead of blocking, keeping an exited command from staying "running".

## Theos (`handler: theos`)

`iPhoneOS16.5.sdk`, pinned theos/roothide refs, `runtime_format: 11`, package
schemes `rootful`/`rootless`/`roothide` through `THEOS_PACKAGE_SCHEME`. It provides
the toolchain that `node.native-addon`, `build.cmake-ios`, `compiler.ollvm.*` and
app packaging depend on.

## Consumers

- CodexApp itself (the primary installer).
- minis — see `minis/README.md`: a manifest-driven adapter, a capability router
  and a CI validator live under `minis/tools/`.
