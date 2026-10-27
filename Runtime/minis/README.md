# minis consumer

minis is an on-device agent app with two execution surfaces that matter here:

| minis surface | What it can run |
| --- | --- |
| `shell_execute` (iSH, Alpine/musl, aarch64 Linux) | Unix tools only — **no Mach-O** |
| `native_build` (on-device Theos toolchain) | compile/link/package iOS arm64, rootful/rootless/roothide, OLLVM |

So the adaptation has two halves: **consume** the manifest (paths, probes,
digests, env) and **route** each capability to a backend that actually exists on
the host asking for it.

## Tools

| File | Purpose |
| --- | --- |
| `tools/runtime_adapter.py` | manifest-driven `doctor` / `list` / `plan` / `status` / `env` / `install` / `verify-manifest`. Validates `archive_sha256`, unpacks, flattens a single top-level directory, then runs the same `required_paths` + `probe` check the app does. On a non-Darwin host a Mach-O probe reports `skipped: host cannot execute Mach-O binaries` instead of failing. |
| `tools/capability_router.py` | maps capabilities to `native_build`, a sandbox Unix tool, a bundled shim, a runtime tree, or `unavailable` — using `minis-runtime-map.json` as data. `coverage` fails when the map drifts from the manifest. |
| `minis-runtime-map.json` | the capability → backend table for both hosts (`ios-native`, `ish-linux`), with the reason for every gap. |

Both scripts are Python 3 standard library only, so they run in the sandbox, in
CI, and on the device.

```sh
A="python3 Runtime/minis/tools/runtime_adapter.py --manifest Runtime/runtime-manifest.json"

$A verify-manifest                     # structural gate (see CI)
$A list                                # versions, handlers, runnable_here
$A plan codex.cli --with-recommends    # resolve requires/recommends
$A install core_tools --dest ~/.codexapp/runtimes
$A status core_tools
eval "$($A env node --root ~/.codexapp/runtimes/node --format shell)"

python3 Runtime/minis/tools/capability_router.py --manifest Runtime/runtime-manifest.json matrix
python3 Runtime/minis/tools/capability_router.py --manifest Runtime/runtime-manifest.json coverage
```

`env` emits `PATH` from `install.path_entries`, the `install.environment` map with
`${RUNTIME_ROOT}` expanded, `CA_RUNTIME_*`, `CA_TOOL_<ID>_RUNTIME_ROOT`, and — for
the `theos` handler — `CA_THEOS_SDKROOT/CLANG/CLANGXX/MAKE/BASH` derived from the
tree. Those are the exact variables the Node shims read, so no consumer should
re-implement that logic.

## Routing summary

| Capability group | minis picks |
| --- | --- |
| `core.git/search/http/diff/archive` | the sandbox tool (`git`, `rg`, `curl`, `diff`, `unzip`) |
| `core.json/file/patch` | bundled shims (`jq`, `file`, `patch`) — the sandbox ships none of them |
| `python.*` | sandbox `python3` unless the managed 3.13 is required |
| `ios.*`, `compiler.ollvm.*`, `reverse.inject` | `native_build` |
| `node.*`, `build.*`, `reverse.macho`, `reverse.radare2`, `codex.*` | runtime on device only; impossible in the sandbox |

`node` trees are still worth installing in the sandbox: the digest and
`required_paths` checks prove a release is complete, and the payload (headers,
sdks, shims) is inspectable even though nothing can exec.

## CI

`.github/workflows/validate-runtime.yml` runs on every change under `Runtime/`:

1. `verify-manifest` — schema 4, handler whitelist, 64-hex digests, https
   archive URLs, `install` completeness for `portable-v1`, `sign_paths ⊆
   required_paths`, resolvable `requires`/`recommends`/`conflicts`.
2. `plan` over realistic capability sets (`codex.cli`, `ios.package.rootless`,
   `node.native-addon`, `build.cmake-ios`, `core.*`) — a capability with no
   provider breaks the build.
3. `coverage` — every capability the manifest provides must be described by
   `minis-runtime-map.json`.

Warnings (missing `revision`/`minimum_ios`, absent `install` for app-native
handlers) are tolerated but reported, so a new release can be published first and
pinned afterwards.
