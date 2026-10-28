# MinisApp-Runtime

Public runtime assets for the Minis app (formerly `CodexApp-Runtime`). No application
source code is stored here — the manifest plus the release assets are the contract.

- `Runtime/runtime-manifest.json` — the contract (schema 4): runtimes, handlers,
  digests, capabilities and dependency graph.
- `Runtime/CONTRACT.md` — field semantics, handler behaviour and the env / spawn /
  shim rules each runtime payload obeys.
- `Runtime/Node/` — Node.js (`NodeMobile.framework`) bundle source, iOS patches
  and the CodexApp bootstrap shims.
- `Runtime/Codex/` — Codex CLI / app-server packaging refs and the iOS
  `posix_spawn` + child-reap patches.
- `Runtime/minis/` — Minis consumer: manifest-driven adapter, capability router
  and `minis-runtime-map.json` (which capability a given host can really run).

Consumers pick runtimes from the manifest, verify `archive_sha256`, unpack, then
check `install.required_paths` and run `install.probe`. `.github/workflows/validate-runtime.yml`
enforces that contract on every change under `Runtime/`.
