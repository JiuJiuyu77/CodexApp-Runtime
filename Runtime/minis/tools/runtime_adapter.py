#!/usr/bin/env python3
"""CodexApp-Runtime adapter.

Single implementation shared by both sides of the adaptation:

  * CodexApp-Runtime repo  -> Runtime/minis/tools/runtime_adapter.py
    (CI validation of runtime-manifest.json, capability resolution for docs)
  * minis skill            -> skills/codexapp-runtime/scripts/runtime_adapter.py
    (locate / verify / install / activate a runtime tree inside minis)

Everything is manifest driven: nothing here hardcodes a component version, so a
manifest bump never needs an adapter change.

Subcommands
  doctor            what this host can and cannot execute
  list              runtimes + provided capabilities from a manifest
  plan              resolve wanted capabilities -> runtimes (+requires/recommends)
  status            verify an installed runtime tree against the manifest
  env               emit PATH / CA_* / THEOS exports for a runtime tree
  install           download -> sha256 -> unpack -> verify a runtime bundle
  verify-manifest   structural validation of runtime-manifest.json (CI)

Python 3.8+, standard library only.
"""

import argparse
import glob
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

MANIFEST_SCHEMA = 4
HANDLERS = ("portable-v1", "codex", "python", "theos", "reverse_tools")

# Handlers whose payload is Mach-O / Apple-SDK specific. They can only be
# executed by a native iOS (Darwin, arm64) host such as CodexApp or a minis
# native (Theos) build. A Linux sandbox (iSH/Alpine) can stage them, hash them,
# inspect them, but never exec them.
DARWIN_ONLY_HANDLERS = ("codex", "python", "theos", "reverse_tools")


# --------------------------------------------------------------------------- #
# host detection
# --------------------------------------------------------------------------- #

def platform_tag():
    """Return one of: ios-native, darwin, linux, other."""
    sysname = platform.system()
    if sysname == "Darwin":
        return "ios-native" if _looks_like_ios() else "darwin"
    if sysname == "Linux":
        return "linux"
    return "other"


def _looks_like_ios():
    if os.environ.get("CA_HOST_KIND") == "codexapp":
        return True
    if os.environ.get("MINIS_HOST_KIND") == "ios":
        return True
    # iSH reports Linux; a real iOS userspace does not exist, so a Darwin host
    # that can see the sandboxed data container is treated as iOS.
    return bool(glob.glob("/var/mobile") or os.environ.get("HOME", "").startswith("/var/mobile"))


def can_exec_darwin_binaries(host=None):
    return (host or platform_tag()) in ("ios-native", "darwin")


def handler_runnable(handler, host=None):
    """Can this host exec the payload produced by `handler`?"""
    if can_exec_darwin_binaries(host):
        return True
    return handler not in DARWIN_ONLY_HANDLERS


# --------------------------------------------------------------------------- #
# manifest loading
# --------------------------------------------------------------------------- #

def load_manifest(source, insecure=False, proxies=None):
    """Load a manifest from a local path, file:// URL or http(s) URL."""
    if os.path.exists(source):
        with open(source, "rb") as handle:
            return json.loads(handle.read().decode("utf-8")), source
    if source.startswith("http://") or source.startswith("https://"):
        data, used = _fetch(source, insecure=insecure, proxies=proxies)
        return json.loads(data.decode("utf-8")), used
    raise SystemExit("manifest not found: %s" % source)


def _fetch(url, insecure=False, proxies=None, timeout=300, attempts=2):
    """Fetch `url`, retrying through the manifest download proxy prefixes.

    CodexApp ships gh-proxy style prefixes because raw GitHub release downloads
    are unreliable on some networks; reuse the exact same list instead of
    inventing a second policy.
    """
    context = None
    if insecure:
        import ssl
        context = ssl._create_unverified_context()
    last = None
    candidates = [url] + ["%s%s" % (prefix, url) for prefix in (proxies or [])]
    for candidate in candidates:
        # Proxies occasionally truncate a large asset mid-stream; a second
        # attempt on the same candidate is cheap next to a fresh proxy hop.
        for attempt in range(attempts):
            try:
                request = urllib.request.Request(candidate, headers={"User-Agent": "runtime-adapter/1"})
                with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                    payload = response.read()
                    expected = response.headers.get("Content-Length")
                    if expected and expected.isdigit() and len(payload) != int(expected):
                        raise IOError("short read: %d of %s bytes" % (len(payload), expected))
                    return payload, candidate
            except Exception as error:  # noqa: BLE001 - report the last failure only
                last = "%s: %s (attempt %d/%d)" % (candidate, error, attempt + 1, attempts)
    raise SystemExit("download failed: %s" % last)


# --------------------------------------------------------------------------- #
# capability resolution
# --------------------------------------------------------------------------- #

def provider_index(manifest):
    index = {}
    for rid, runtime in manifest["runtimes"].items():
        for capability in runtime.get("provides") or []:
            index.setdefault(capability, []).append(rid)
    return index


def resolve(manifest, wanted, with_recommends=False):
    """Resolve capability ids into runtime ids, honouring requires/recommends.

    `wanted` may also be runtime ids; both vocabularies are useful (a user says
    "I need codex.cli" or "install theos").
    """
    runtimes = manifest["runtimes"]
    index = provider_index(manifest)
    selection = {}
    missing = []

    def visit_capability(capability, chain):
        if capability in chain:
            return
        providers = index.get(capability, [])
        if not providers:
            missing.append(capability)
            return
        runtime_id = _prefer(providers, runtimes)
        visit_runtime(runtime_id, chain + [capability])

    def visit_runtime(runtime_id, chain):
        if runtime_id not in runtimes:
            missing.append(runtime_id)
            return
        if runtime_id in selection:
            return
        selection[runtime_id] = {"runtime": runtime_id, "chain": list(chain)}
        runtime = runtimes[runtime_id]
        for capability, meta in (runtime.get("capabilities") or {}).items():
            for requirement in meta.get("requires") or []:
                if requirement.get("provides"):
                    visit_capability(requirement["provides"], chain + [capability])
        if with_recommends:
            for recommendation in runtime.get("recommends") or []:
                if recommendation.get("id"):
                    visit_runtime(recommendation["id"], chain + [runtime_id])

    for item in wanted:
        if item in runtimes:
            visit_runtime(item, [])
        else:
            visit_capability(item, [])

    return {
        "selected": [selection[k] for k in sorted(selection)],
        "missing": sorted(set(missing)),
    }


def _prefer(providers, runtimes):
    """Deterministic pick: non-optional first, then lowest order, then name."""
    return sorted(providers, key=lambda rid: (bool(runtimes[rid].get("optional")),
                                             runtimes[rid].get("order", 0), rid))[0]


# --------------------------------------------------------------------------- #
# runtime roots
# --------------------------------------------------------------------------- #

def env_runtime_root(manifest_source, runtime_id):
    """Env var CodexApp would expose for a runtime root, when it has one."""
    key = "CA_TOOL_%s_RUNTIME_ROOT" % runtime_id.upper().replace("-", "_")
    return os.environ.get(key)


def candidate_roots(manifest, runtime_id):
    """Places a runtime tree may live, most explicit first.

    minis cannot read the CodexApp data container, so the device layout is only
    a guess unless the user passes --root or exports CA_RUNTIME_ROOT.
    """
    runtime = manifest["runtimes"].get(runtime_id, {})
    roots = []
    for entry in (runtime.get("install") or {}).get("persistent_path_entries") or []:
        roots.append(entry if os.path.isabs(entry) else None)
    roots += [
        env_runtime_root(manifest, runtime_id),
        os.path.join(os.environ["CA_RUNTIME_ROOT"], runtime_id) if os.environ.get("CA_RUNTIME_ROOT") else None,
        os.path.join(os.path.expanduser("~"), ".codexapp", "runtimes", runtime_id),
        os.path.join(os.path.expanduser("~"), "Library", "Application Support", "CodexApp", "Runtimes", runtime_id),
        os.path.join(os.path.expanduser("~"), "Library", "Application Support", "CodexApp", runtime_id),
    ]
    seen, ordered = set(), []
    for root in roots:
        if root and root not in seen:
            seen.add(root)
            ordered.append(root)
    return ordered


def find_root(manifest, runtime_id, explicit=None):
    if explicit:
        return explicit, "explicit"
    for root in candidate_roots(manifest, runtime_id):
        if os.path.isdir(root):
            return root, "discovered"
    return None, "missing"


# --------------------------------------------------------------------------- #
# status / verification
# --------------------------------------------------------------------------- #

def substitute(value, root):
    return value.replace("${RUNTIME_ROOT}", root).replace("$RUNTIME_ROOT", root)


def verify_tree(manifest, runtime_id, root, run_probe=True, timeout=120):
    """Verify an installed tree against install.required_paths + install.probe."""
    runtime = manifest["runtimes"].get(runtime_id)
    if runtime is None:
        raise SystemExit("unknown runtime: %s" % runtime_id)
    install = runtime.get("install") or {}
    report = {
        "id": runtime_id,
        "root": root,
        "version": runtime.get("version"),
        "handler": runtime.get("handler"),
        "missing_paths": [],
        "probe": None,
        "ok": False,
    }
    for relative in install.get("required_paths") or []:
        if not os.path.exists(os.path.join(root, relative)):
            report["missing_paths"].append(relative)
    if report["missing_paths"]:
        return report

    probe = install.get("probe") or {}
    if run_probe and probe.get("executable"):
        executable = os.path.join(root, probe["executable"])
        if not can_exec_darwin_binaries() and _is_macho(executable):
            report["probe"] = {"skipped": "host cannot execute Mach-O binaries"}
            report["ok"] = not report["missing_paths"]
            return report
        environment = dict(os.environ)
        for key, value in (install.get("environment") or {}).items():
            environment[key] = substitute(value, root)
        try:
            completed = subprocess.run(
                [executable] + list(probe.get("arguments") or []),
                cwd=root, env=environment, timeout=timeout,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            output = completed.stdout.decode("utf-8", "replace")
            expected = probe.get("contains")
            passed = completed.returncode == 0 and (expected is None or expected in output)
            report["probe"] = {"exit": completed.returncode, "output": output.strip()[:4096], "ok": passed}
            report["ok"] = passed
        except Exception as error:  # noqa: BLE001
            report["probe"] = {"error": str(error), "ok": False}
            report["ok"] = False
    else:
        report["ok"] = True
    return report


def _is_macho(path):
    try:
        with open(path, "rb") as handle:
            magic = handle.read(4)
    except OSError:
        return False
    return magic in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe",
                     b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce",
                     b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf")


# --------------------------------------------------------------------------- #
# env emission
# --------------------------------------------------------------------------- #

def build_env(manifest, runtime_id, root, shell=None, theos_autodetect=True):
    """Exports that make a runtime tree behave like it does inside CodexApp."""
    runtime = manifest["runtimes"][runtime_id]
    install = runtime.get("install") or {}
    exports, path_entries = {}, []

    for entry in install.get("path_entries") or []:
        path_entries.append(os.path.join(root, entry))
    for key, value in (install.get("environment") or {}).items():
        exports[key] = substitute(value, root)

    exports["CA_RUNTIME_ID"] = runtime_id
    exports["CA_RUNTIME_VERSION"] = str(runtime.get("version"))
    exports["CA_RUNTIME_HANDLER"] = str(runtime.get("handler"))
    exports["CA_RUNTIME_ROOT"] = os.path.dirname(root)
    exports["CA_TOOL_%s_RUNTIME_ROOT" % runtime_id.upper().replace("-", "_")] = root

    if shell and os.path.exists(shell):
        exports["SHELL"] = shell

    if theos_autodetect and runtime.get("handler") == "theos":
        exports.update(_theos_exports(root))

    return {"exports": exports,
            "path": os.pathsep.join(path_entries + ["$PATH"]),
            "path_entries": path_entries}


def _theos_exports(theos_root):
    """Map a Theos tree onto the CA_THEOS_* variables the Node shims expect.

    ios-bootstrap.js reads CA_THEOS_SDKROOT / CA_THEOS_CLANG / CA_THEOS_CLANGXX /
    CA_THEOS_MAKE / CA_THEOS_BASH; deriving them here keeps the adapter from
    duplicating that logic in every caller.
    """
    exports = {}
    sdks = sorted(glob.glob(os.path.join(theos_root, "**", "sdks", "iPhoneOS*.sdk"), recursive=True))
    if sdks:
        exports["CA_THEOS_SDKROOT"] = sdks[-1]
    for pattern, key in (("**/toolchain/**/bin/clang", "CA_THEOS_CLANG"),
                         ("**/toolchain/**/bin/clang++", "CA_THEOS_CLANGXX")):
        hits = sorted(glob.glob(os.path.join(theos_root, pattern), recursive=True))
        if hits:
            exports[key] = hits[-1]
    for name, key in (("make", "CA_THEOS_MAKE"), ("bash", "CA_THEOS_BASH")):
        for candidate in (os.path.join(theos_root, "bin", name),
                          os.path.join(theos_root, "toolchain", "linux", "iphone", "bin", name)):
            if os.path.exists(candidate):
                exports[key] = candidate
                break
    return exports


def render_shell(env):
    lines = []
    for key in sorted(env["exports"]):
        lines.append('export %s=%s' % (key, _shquote(env["exports"][key])))
    lines.append('export PATH="%s"' % env["path"].replace('"', '\\"'))
    return "\n".join(lines) + "\n"


def _shquote(value):
    return "'" + str(value).replace("'", "'\\''") + "'"


# --------------------------------------------------------------------------- #
# install
# --------------------------------------------------------------------------- #

def install(manifest, runtime_id, dest, insecure=False, keep_archive=False, run_probe=True):
    runtime = manifest["runtimes"][runtime_id]
    url = runtime.get("archive_url")
    wanted = runtime.get("archive_sha256")
    os.makedirs(dest, exist_ok=True)
    data, used = _fetch(url, insecure=insecure, proxies=manifest.get("download_proxy_prefixes"))
    digest = hashlib.sha256(data).hexdigest()
    if wanted and digest != wanted:
        raise SystemExit("sha256 mismatch for %s\n  want %s\n  got  %s" % (used, wanted, digest))
    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(url)[1] or ".tar.gz") as handle:
        handle.write(data)
        archive = handle.name
    target = os.path.join(dest, runtime_id)
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target, exist_ok=True)
    if archive.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(target)
    else:
        with tarfile.open(archive) as tf:
            tf.extractall(target)
    if not keep_archive:
        os.unlink(archive)
    # Bundles may be packed with a single top-level directory.
    entries = [os.path.join(target, name) for name in os.listdir(target)]
    if len(entries) == 1 and os.path.isdir(entries[0]) and \
            not os.path.exists(os.path.join(target, "bin")):
        flat = entries[0] + ".flatten"
        os.rename(entries[0], flat)
        shutil.rmtree(target)
        os.rename(flat, target)
    return {"runtime": runtime_id, "url": used, "sha256": digest, "root": target,
            "verify": verify_tree(manifest, runtime_id, target, run_probe=run_probe)}


# --------------------------------------------------------------------------- #
# manifest validation (CI)
# --------------------------------------------------------------------------- #

def verify_manifest(manifest):
    errors, warnings = [], []

    def error(message):
        errors.append(message)

    def warn(message):
        warnings.append(message)

    if not isinstance(manifest, dict):
        return ["manifest is not a JSON object"], []
    schema = manifest.get("schema")
    if schema != MANIFEST_SCHEMA:
        error("schema: expected %d, found %r" % (MANIFEST_SCHEMA, schema))
    for key in ("source_format", "source_name"):
        if not isinstance(manifest.get(key), (int, str)):
            error("top-level %s missing or wrong type" % key)
    for prefix in manifest.get("download_proxy_prefixes") or []:
        if not prefix.endswith("/"):
            error("download_proxy_prefixes entry must end with '/': %s" % prefix)
    proxies = manifest.get("download_proxy_prefixes")
    if not isinstance(proxies, list) or not proxies:
        warn("download_proxy_prefixes is empty; releases download directly")

    runtimes = manifest.get("runtimes")
    if not isinstance(runtimes, dict) or not runtimes:
        return errors + ["runtimes: missing or empty"], warnings

    provided = {}
    for runtime_id, runtime in sorted(runtimes.items()):
        where = "runtimes.%s" % runtime_id
        if not isinstance(runtime, dict):
            error("%s: not an object" % where)
            continue
        for key in ("display_name", "version", "summary", "archive_url",
                    "archive_sha256", "handler", "order"):
            if key not in runtime:
                error("%s: missing %s" % (where, key))
        # release_tag/revision are optional (app-native handlers such as codex
        # publish a release tag only), but anything present must be well formed.
        if "revision" not in runtime:
            warn("%s: no revision; reproducible-install pinning unavailable" % where)
        if "minimum_ios" not in runtime:
            warn("%s: no minimum_ios; installer must assume the app default" % where)
        handler = runtime.get("handler")
        if handler not in HANDLERS:
            error("%s: unknown handler %r (known: %s)" % (where, handler, ", ".join(HANDLERS)))
        for key in ("archive_sha256", "revision"):
            if key not in runtime:
                continue
            value = runtime.get(key)
            if isinstance(value, str) and len(value) == 64 and \
                    all(c in "0123456789abcdef" for c in value):
                continue
            error("%s.%s: expected 64 lowercase hex chars" % (where, key))
        url = runtime.get("archive_url") or ""
        if not url.startswith("https://"):
            error("%s.archive_url: must be https" % where)
        identity = runtime.get("release_tag") or runtime.get("version")
        if identity and isinstance(identity, str) and identity not in url:
            warn("%s.archive_url does not contain its release identity %r" % (where, identity))
        for key in ("provides",):
            provides = runtime.get(key)
            if not isinstance(provides, list) or not provides:
                error("%s.%s: expected non-empty list" % (where, key))
                continue
            for capability in provides:
                provided.setdefault(capability, []).append(runtime_id)
        for capability, meta in (runtime.get("capabilities") or {}).items():
            if capability not in (runtime.get("provides") or []):
                warn("%s.capabilities.%s: capability is documented but not provided" % (where, capability))
            for requirement in (meta or {}).get("requires") or []:
                target = requirement.get("provides")
                if target and target not in provided and target not in _all_capabilities(runtimes):
                    error("%s.capabilities.%s requires unknown capability %r" % (where, capability, target))
        for reference in runtime.get("requires") or []:
            if isinstance(reference, str) and reference not in runtimes:
                error("%s.requires: unknown runtime %r" % (where, reference))
            if isinstance(reference, dict) and reference.get("id") not in runtimes:
                error("%s.requires: unknown runtime %r" % (where, (reference or {}).get("id")))
        for reference in runtime.get("recommends") or []:
            if isinstance(reference, dict) and reference.get("id") not in runtimes:
                error("%s.recommends: unknown runtime %r" % (where, reference.get("id")))
        for reference in runtime.get("conflicts") or []:
            if reference not in runtimes:
                warn("%s.conflicts: unknown runtime %r" % (where, reference))
        ui = runtime.get("ui") or {}
        if not ui.get("symbol"):
            warn("%s.ui.symbol missing (install UI falls back to a default)" % where)

        install_block = runtime.get("install")
        if not isinstance(install_block, dict):
            # portable-v1 trees are unpacked and probed by the installer, so
            # they must describe themselves. App-native handlers (codex,
            # python, theos, reverse_tools) are installed by their own code.
            if handler == "portable-v1" or install_block is not None:
                error("%s.install: required for handler %r" % (where, handler))
            else:
                warn("%s: handler %r installs itself; no install block to validate" % (where, handler))
            continue
        if True:
            required = install_block.get("required_paths") or []
            if not required:
                error("%s.install.required_paths: expected non-empty list" % where)
            for entry in required:
                if os.path.isabs(entry) or ".." in entry.split("/"):
                    error("%s.install.required_paths: must be a relative path inside the tree: %s" % (where, entry))
            for entry in install_block.get("path_entries") or []:
                if os.path.isabs(entry):
                    error("%s.install.path_entries: must be relative: %s" % (where, entry))
            probe = install_block.get("probe")
            if not probe:
                warn("%s.install.probe missing; install cannot self-verify" % where)
            else:
                if not probe.get("executable"):
                    error("%s.install.probe.executable: missing" % where)
                elif probe["executable"] not in required:
                    warn("%s.install.probe.executable %r is not in required_paths" % (where, probe["executable"]))
                if "contains" not in probe:
                    warn("%s.install.probe.contains: missing output assertion" % where)
                if not isinstance(probe.get("arguments", []), list):
                    error("%s.install.probe.arguments: expected a list" % where)
            for entry in install_block.get("sign_paths") or []:
                if entry not in required:
                    warn("%s.install.sign_paths %r not listed in required_paths" % (where, entry))
            for key, value in (install_block.get("environment") or {}).items():
                if "${RUNTIME_ROOT}" not in str(value):
                    warn("%s.install.environment.%s: no ${RUNTIME_ROOT} placeholder" % (where, key))

    for capability, owners in sorted(provided.items()):
        if len(owners) > 1:
            warn("capability %r is provided by %s; resolution will prefer %r"
                 % (capability, ", ".join(sorted(owners)), _prefer(owners, runtimes)))

    return errors, warnings


def _all_capabilities(runtimes):
    result = set()
    for runtime in runtimes.values():
        result.update(runtime.get("provides") or [])
    return result


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #

def doctor():
    host = platform_tag()
    return {
        "host": host,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "shell": os.environ.get("SHELL"),
        "can_exec_darwin_binaries": can_exec_darwin_binaries(host),
        "runnable_handlers": [h for h in HANDLERS if handler_runnable(h, host)],
        "blocked_handlers": [h for h in HANDLERS if not handler_runnable(h, host)],
        "tools": {name: shutil.which(name) for name in
                  ("git", "curl", "python3", "ldid", "clang", "make", "node", "codex")},
        "env": {key: value for key, value in os.environ.items() if key.startswith(("CA_", "MINIS_"))},
    }


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

def main(argv=None):
    parser = argparse.ArgumentParser(description="CodexApp-Runtime adapter")
    parser.add_argument("--manifest", default=os.environ.get("CA_RUNTIME_MANIFEST", "runtime-manifest.json"),
                        help="path or URL of runtime-manifest.json")
    parser.add_argument("--insecure", action="store_true",
                        help="skip TLS verification (device images without a CA bundle)")
    parser.add_argument("--json", action="store_true", help="machine readable output")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_json(target):
        target.add_argument("--json", dest="json_sub", action="store_true", default=None,
                            help="machine readable output (same as the global flag)")

    _p = sub.add_parser("doctor", help="report host execution capabilities")
    add_json(_p)
    _p = sub.add_parser("list", help="list runtimes and capabilities")
    add_json(_p)
    _p = sub.add_parser("verify-manifest", help="validate manifest structure (CI)")
    add_json(_p)

    plan = sub.add_parser("plan", help="resolve capabilities to runtimes")
    add_json(plan)
    plan.add_argument("want", nargs="+")
    plan.add_argument("--with-recommends", action="store_true")
    plan.add_argument("--root", help="verify against an installed tree as well")

    status = sub.add_parser("status", help="verify an installed runtime tree")
    add_json(status)
    status.add_argument("runtime_id", nargs="+")
    status.add_argument("--root", help="explicit runtime root")
    status.add_argument("--no-probe", action="store_true")

    env = sub.add_parser("env", help="emit environment exports for a runtime tree")
    add_json(env)
    env.add_argument("runtime_id")
    env.add_argument("--root")
    env.add_argument("--shell", help="shell to advertise as $SHELL")
    env.add_argument("--format", choices=("shell", "json", "dotenv"), default="shell")

    inst = sub.add_parser("install", help="download, verify and unpack a runtime")
    add_json(inst)
    inst.add_argument("runtime_id")
    inst.add_argument("--dest", default=os.path.join(os.path.expanduser("~"), ".codexapp", "runtimes"))
    inst.add_argument("--keep-archive", action="store_true")
    inst.add_argument("--no-probe", action="store_true")

    args = parser.parse_args(argv)
    if getattr(args, "json_sub", None) is not None:
        args.json = args.json_sub

    if args.command == "doctor":
        report = doctor()
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    manifest, source = load_manifest(args.manifest, insecure=args.insecure)
    proxies = manifest.get("download_proxy_prefixes")

    if args.command == "verify-manifest":
        errors, warnings = verify_manifest(manifest)
        if args.json:
            print(json.dumps({"source": source, "errors": errors, "warnings": warnings}, indent=2))
        else:
            for message in errors:
                print("error   %s" % message)
            for message in warnings:
                print("warning %s" % message)
            print("%s: %d error(s), %d warning(s)" % (source, len(errors), len(warnings)))
        return 1 if errors else 0

    if args.command == "list":
        rows = []
        for runtime_id, runtime in sorted(manifest["runtimes"].items(),
                                          key=lambda kv: kv[1].get("order", 0)):
            rows.append({
                "id": runtime_id,
                "display_name": runtime.get("display_name"),
                "version": runtime.get("version"),
                "handler": runtime.get("handler"),
                "optional": bool(runtime.get("optional")),
                "runnable_here": handler_runnable(runtime.get("handler")),
                "provides": runtime.get("provides") or [],
            })
        if args.json:
            print(json.dumps(rows, indent=2, ensure_ascii=False))
        else:
            for row in rows:
                flag = "ok " if row["runnable_here"] else "n/a"
                print("%-3s %-14s %-22s %-14s %s" % (flag, row["id"], row["version"],
                                                     row["handler"], ",".join(row["provides"])))
        return 0

    if args.command == "plan":
        result = resolve(manifest, args.want, with_recommends=args.with_recommends)
        for item in result["selected"]:
            runtime = manifest["runtimes"][item["runtime"]]
            item["handler"] = runtime.get("handler")
            item["runnable_here"] = handler_runnable(runtime.get("handler"))
            if args.root:
                root = os.path.join(args.root, item["runtime"]) \
                    if os.path.isdir(os.path.join(args.root, item["runtime"])) else args.root
                item["status"] = verify_tree(manifest, item["runtime"], root, run_probe=False)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            for item in result["selected"]:
                flag = "ok " if item["runnable_here"] else "n/a"
                print("%s %-14s <- %s" % (flag, item["runtime"], " <- ".join(item["chain"]) or "(direct)"))
            if result["missing"]:
                print("missing: %s" % ", ".join(result["missing"]))
        return 1 if result["missing"] else 0

    if args.command == "status":
        reports = []
        for runtime_id in args.runtime_id:
            root, origin = find_root(manifest, runtime_id, args.root)
            if not root:
                reports.append({"id": runtime_id, "ok": False, "error": "not installed",
                                "searched": candidate_roots(manifest, runtime_id)})
                continue
            report = verify_tree(manifest, runtime_id, root, run_probe=not args.no_probe)
            report["root_origin"] = origin
            reports.append(report)
        if args.json:
            print(json.dumps(reports, indent=2, ensure_ascii=False))
        else:
            for report in reports:
                print("%s %s" % ("ok  " if report.get("ok") else "FAIL", report["id"]))
                if report.get("error"):
                    print("     %s" % report["error"])
                for path in report.get("missing_paths") or []:
                    print("     missing %s" % path)
                if report.get("root"):
                    print("     root %s" % report["root"])
                probe = report.get("probe") or {}
                if probe.get("output"):
                    print("     probe %s" % probe["output"].splitlines()[0][:160])
                elif probe.get("skipped"):
                    print("     probe skipped: %s" % probe["skipped"])
                elif probe.get("error"):
                    print("     probe error: %s" % probe["error"])
        return 0 if all(report.get("ok") for report in reports) else 1

    if args.command == "env":
        root, origin = find_root(manifest, args.runtime_id, args.root)
        if not root:
            raise SystemExit("runtime %s is not installed; searched:\n  %s"
                             % (args.runtime_id, "\n  ".join(candidate_roots(manifest, args.runtime_id))))
        env = build_env(manifest, args.runtime_id, root, shell=args.shell or os.environ.get("SHELL"))
        env["root_origin"] = origin
        if args.format == "json":
            print(json.dumps(env, indent=2, ensure_ascii=False))
        elif args.format == "dotenv":
            for key, value in sorted(env["exports"].items()):
                print("%s=%s" % (key, value))
        else:
            sys.stdout.write(render_shell(env))
        return 0

    if args.command == "install":
        result = install(manifest, args.runtime_id, args.dest,
                         insecure=args.insecure, keep_archive=args.keep_archive,
                         run_probe=not args.no_probe)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["verify"].get("ok") else 1

    return 2


if __name__ == "__main__":
    sys.exit(main())
