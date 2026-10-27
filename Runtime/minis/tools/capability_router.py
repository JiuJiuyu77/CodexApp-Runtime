#!/usr/bin/env python3
"""Route CodexApp-Runtime capabilities to the backend that can serve them here.

The runtime manifest says *what* a capability is; this router says *which*
implementation minis should use for it: a runtime tree on device, a minis
native tool (native_build), a host Unix tool inside the sandbox, or nothing.

Inputs
  minis-runtime-map.json     capability -> ordered backends per host (data)
  runtime-manifest.json      optional: knows which runtimes provide what
  installed runtime roots    optional: a runtime backend is only chosen when
                             that runtime is actually present and executable

Usage
  capability_router.py matrix                    # full table for this host
  capability_router.py resolve codex.cli core.git
  capability_router.py route --manifest M --root DIR --json codex.cli
"""

import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import runtime_adapter as ra  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def _default_map():
    for candidate in (os.path.join(HERE, "minis-runtime-map.json"),
                      os.path.join(os.path.dirname(HERE), "minis-runtime-map.json")):
        if os.path.exists(candidate):
            return candidate
    return os.path.join(HERE, "minis-runtime-map.json")


DEFAULT_MAP = _default_map()
SHIM_NAMES = ("jq", "file", "patch")


def load_json(path):
    with open(path, "rb") as handle:
        return json.loads(handle.read().decode("utf-8"))


def host_id():
    """Which backend host table applies: ios-native or ish-linux."""
    return "ios-native" if ra.can_exec_darwin_binaries() else "ish-linux"


def tool_available(tool):
    return shutil.which(tool) is not None


def choose(capability, mapping, host, manifest=None, installed=None, prefer_minis=True):
    """Pick the first backend that is actually usable on `host`."""
    entry = (mapping.get("capabilities") or {}).get(capability)
    if entry is None:
        return {"capability": capability, "backend": None,
                "reason": "not described by minis-runtime-map.json"}
    candidates = [b for b in entry.get("backends") or [] if b.get("host") in (None, host)]
    if prefer_minis:
        candidates.sort(key=lambda b: 0 if b["kind"] == "minis-tool" else 1)

    rejected = []
    for backend in candidates:
        kind = backend["kind"]
        if kind == "minis-tool":
            return {"capability": capability, "backend": backend, "status": "ok"}
        if kind == "host-tool":
            tool = backend["tool"]
            if tool_available(tool):
                return {"capability": capability, "backend": backend, "status": "ok"}
            shim = os.path.join(HERE, tool)
            if tool in SHIM_NAMES and os.path.exists(shim):
                return {"capability": capability, "status": "ok",
                        "backend": dict(backend, shim=shim)}
            rejected.append("%s missing in sandbox" % tool)
            continue
        if kind == "runtime":
            runtime_id = _provider(manifest, backend.get("capability", capability), installed)
            if runtime_id:
                return {"capability": capability, "status": "ok",
                        "backend": dict(backend, runtime=runtime_id)}
            rejected.append("runtime %r not installed/executable here"
                            % (backend.get("capability", capability)))
            continue
        if kind == "unavailable":
            rejected.append(backend.get("reason", "unavailable"))
    else_where = sorted({b["host"] for b in entry.get("backends") or [] if b.get("host")} - {host})
    minis_tools = sorted({b["tool"] for b in entry.get("backends") or []
                          if b["kind"] == "minis-tool" and b.get("tool")})
    if not rejected and else_where:
        hint = " (use minis tool %s)" % "/".join(minis_tools) if minis_tools else ""
        rejected.append("requires host %s%s" % (" or ".join(else_where), hint))
    return {"capability": capability, "backend": None, "status": "unavailable",
            "reason": "; ".join(rejected) or "no backend for host %s" % host}


def _provider(manifest, capability, installed):
    if not manifest:
        return None
    for runtime_id, runtime in manifest.get("runtimes", {}).items():
        if capability not in (runtime.get("provides") or []):
            continue
        if not ra.handler_runnable(runtime.get("handler")):
            continue
        if installed is not None and runtime_id not in installed:
            continue
        return runtime_id
    return None


def coverage(mapping, manifest, as_json=False):
    """Every capability a manifest provides must be described by the map."""
    mapped = set((mapping.get("capabilities") or {}).keys())
    declared = set()
    for runtime in (manifest or {}).get("runtimes", {}).values():
        declared.update(runtime.get("provides") or [])
        declared.update((runtime.get("capabilities") or {}).keys())
    unknown_hosts = sorted({b.get("host") for entry in (mapping.get("capabilities") or {}).values()
                            for b in entry.get("backends") or []
                            if b.get("host") and b["host"] not in (mapping.get("hosts") or {})})
    missing = sorted(declared - mapped)
    stale = sorted(mapped - declared) if manifest else []
    result = {"mapped": len(mapped), "declared": len(declared),
              "missing": missing, "stale": stale, "unknown_hosts": unknown_hosts}
    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for capability in missing:
            print("missing %s (provided by the manifest, absent from the map)" % capability)
        for capability in stale:
            print("stale   %s (in the map, no longer provided)" % capability)
        for host in unknown_hosts:
            print("unknown host %r in backends" % host)
        print("%d mapped / %d declared: %d missing, %d stale, %d unknown host(s)"
              % (result["mapped"], result["declared"], len(missing), len(stale), len(unknown_hosts)))
    return 1 if (missing or unknown_hosts) else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="route runtime capabilities to minis backends")
    parser.add_argument("--map", default=DEFAULT_MAP)
    parser.add_argument("--manifest")
    parser.add_argument("--root", action="append", default=[],
                        help="installed runtime root (repeatable); enables runtime backends")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("matrix", help="print the whole routing table for this host")
    resolve = sub.add_parser("resolve", help="route specific capabilities")
    resolve.add_argument("capability", nargs="+")
    sub.add_parser("coverage", help="show manifest capabilities the map does not describe")
    args = parser.parse_args(argv)

    mapping = load_json(args.map)
    manifest = load_json(args.manifest) if args.manifest else None
    if args.command == "coverage" and manifest is None:
        sys.stderr.write("coverage needs --manifest\n")
        return 2
    installed = set()
    for root in args.root:
        if manifest and os.path.isdir(root):
            for runtime_id in manifest["runtimes"]:
                if os.path.isdir(os.path.join(root, runtime_id)):
                    installed.add(runtime_id)
                elif os.path.exists(os.path.join(root, "bin")):
                    for candidate in manifest["runtimes"]:
                        if os.path.isdir(os.path.join(root, "bin")) and runtime_id == candidate:
                            pass
            if os.path.exists(os.path.join(root, "bin")):
                # --root pointing straight at one tree
                for runtime_id in manifest["runtimes"]:
                    if os.path.exists(os.path.join(root, "bin")) and runtime_id not in installed:
                        installed.add(runtime_id)
                        break
    host = host_id()

    if args.command == "coverage":
        return coverage(mapping, manifest, args.json)

    capabilities = sorted((mapping.get("capabilities") or {}).keys()) \
        if args.command == "matrix" else args.capability

    rows = [choose(cap, mapping, host, manifest, installed) for cap in capabilities]

    if args.json:
        print(json.dumps({"host": host, "rows": rows}, indent=2, ensure_ascii=False))
        return 0
    width = max(len(row["capability"]) for row in rows)
    for row in rows:
        backend = row.get("backend") or {}
        if row["status"] == "ok":
            if backend["kind"] == "minis-tool":
                detail = "native_build"
            elif backend["kind"] == "host-tool":
                detail = ("shim:%s" if backend.get("shim") else "sandbox:%s") % backend["tool"]
            else:
                detail = "runtime:%s" % backend.get("runtime", backend.get("capability"))
        else:
            detail = "unavailable - %s" % row.get("reason", "")
        print("%-*s  %-11s %s" % (width, row["capability"], row["status"], detail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
