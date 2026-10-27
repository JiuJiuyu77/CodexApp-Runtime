#!/usr/bin/env python3
"""Apply the iOS adaptations needed to build Node.js as NodeMobile.framework.

Node 24 ships most of the iOS plumbing already (libuv is full of
`TARGET_OS_IPHONE` guards), so the remaining work is small and is done
defensively here: if upstream already gates a site, the edit is skipped instead
of failing the build, and every real change is printed so the build log records
exactly what drifted.

Usage: apply-ios-patches.py <node-source-root>
"""
import io
import os
import sys

changes = []
skips = []


def read(path):
    return io.open(path, encoding='utf-8', newline='').read()


def write(path, text):
    io.open(path, 'w', encoding='utf-8', newline='').write(text)


def patch(relpath, old, new, note, required=True):
    """Replace `old` with `new` in relpath. Idempotent: skips if already applied."""
    path = os.path.join(root, relpath)
    text = read(path)
    if new in text and old not in text:
        skips.append('%s: already patched (%s)' % (relpath, note))
        return
    count = text.count(old)
    if count == 0:
        msg = '%s: anchor not found (%s)' % (relpath, note)
        if required:
            raise SystemExit('apply-ios-patches: %s\n--- anchor ---\n%s' % (msg, old))
        skips.append(msg)
        return
    if count > 1:
        raise SystemExit('apply-ios-patches: anchor ambiguous (%d hits) in %s (%s)'
                         % (count, relpath, note))
    write(path, text.replace(old, new))
    changes.append('%s: %s' % (relpath, note))


root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else '.')
deployment_target = os.environ.get('IPHONEOS_DEPLOYMENT_TARGET', '15.0')

# 1. node_js2c must emit string literals on iOS, as it does for linux/mac.
#    Without this the iOS build produces a js2c step that links a host binary
#    the cross toolchain cannot run.
patch(
    'node.gyp',
    """[ 'OS in "linux mac openharmony"', {
          'defines': ['NODE_JS2C_USE_STRING_LITERALS'],""",
    """[ 'OS in "linux mac ios openharmony"', {
          'defines': ['NODE_JS2C_USE_STRING_LITERALS'],""",
    'node_js2c string literals on ios',
    required=False,
)

# 2. Raise the deployment target Node builds against. Node 24 needs a modern
#    libc++, and 15.0 is the floor CodexApp supports (matches the manifest).
patch(
    'common.gypi',
    "'IPHONEOS_DEPLOYMENT_TARGET': '13.0',     # -miphoneos-version-min=13.0",
    "'IPHONEOS_DEPLOYMENT_TARGET': '%s',     # -miphoneos-version-min=%s"
    % (deployment_target, deployment_target),
    'ios deployment target %s' % deployment_target,
    required=False,
)
patch(
    'common.gypi',
    "'IPHONEOS_DEPLOYMENT_TARGET': '14.0',     # -miphoneos-version-min=14.0",
    "'IPHONEOS_DEPLOYMENT_TARGET': '%s',     # -miphoneos-version-min=%s"
    % (deployment_target, deployment_target),
    'ios deployment target %s (from 14.0)' % deployment_target,
    required=False,
)

# 3. V8's gypfiles pin a macOS target for the host generators; the old 10.10
#    value is rejected by current SDKs and breaks the cross build.
patch(
    'tools/v8_gypfiles/toolchain.gypi',
    "'MACOSX_DEPLOYMENT_TARGET': '10.10',",
    "'MACOSX_DEPLOYMENT_TARGET': '11.0',",
    'v8 host macos deployment target 11.0',
    required=False,
)

# 4. Node 24's common.gypi has no iOS block at all (the mac block supplies the
#    deployment target on darwin). Add one so the device build is configured for
#    iOS rather than inheriting macOS defaults.
path = os.path.join(root, 'common.gypi')
text = read(path)
if 'CodexApp iOS additions' in text:
    skips.append('common.gypi: iOS block already present')
else:
    marker = "      ['OS==\"mac\"', {"
    if marker not in text:
        raise SystemExit('apply-ios-patches: common.gypi missing mac block anchor')
    block = """      # CodexApp iOS additions
      ['OS=="ios"', {
        'xcode_settings': {
          'IPHONEOS_DEPLOYMENT_TARGET': '%s',
          'GCC_ENABLE_CPP_EXCEPTIONS': 'NO',
          'GCC_ENABLE_CPP_RTTI': 'NO',
          'GCC_STRICT_ALIASING': 'NO',
          'USE_HEADERMAP': 'NO',
          'CLANG_CXX_LIBRARY': 'libc++',
          'ALWAYS_SEARCH_USER_PATHS': 'NO',
        },
        'defines': ['_DARWIN_USE_64_BIT_INODE=1'],
      }],
""" % deployment_target
    write(path, text.replace(marker, block + marker, 1))
    changes.append('common.gypi: inserted iOS block (deployment target %s)'
                   % deployment_target)

print('apply-ios-patches: applied %d change(s)' % len(changes))
for c in changes:
    print('  + ' + c)
for s in skips:
    print('  . ' + s)
