#!/usr/bin/env python3
"""Apply the iOS adaptations needed to build Node.js as NodeMobile.framework.

Node 24 supports `--dest-os=ios` but only minimally: libuv is already full of
`TARGET_OS_IPHONE` guards, while `common.gypi` has no iOS block at all. The
edits below are applied defensively — when upstream already gates a site the
change is skipped instead of failing the build — and every real change is
printed so the build log records exactly what drifted.

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
#    Without this the generated js2c step links a host binary that the cross
#    toolchain cannot produce.
patch(
    'node.gyp',
    """[ 'OS in "linux mac openharmony"', {
          'defines': ['NODE_JS2C_USE_STRING_LITERALS'],""",
    """[ 'OS in "linux mac ios openharmony"', {
          'defines': ['NODE_JS2C_USE_STRING_LITERALS'],""",
    'node_js2c string literals on ios',
)

# 2. Raise the deployment target where an explicit one is still written down.
#    Node 24 only keeps it in the gypfiles; 15.0 is the floor CodexApp supports
#    and matches minimum_ios in the runtime manifest.
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

# 3. c-ares reuses the darwin config for iOS (cares.gyp: OS=="mac" or OS=="ios"),
#    but iOS has no <sys/random.h>, so that include fails outright. iOS does
#    provide arc4random_buf(), which the darwin config already enables, and
#    HAVE_GETRANDOM is already undefined there, so dropping the header is enough
#    to select the working backend.
patch(
    'deps/cares/config/darwin/ares_config.h',
    '#define HAVE_SYS_RANDOM_H 1',
    '/* #undef HAVE_SYS_RANDOM_H -- iOS has no <sys/random.h>; use arc4random_buf */',
    'cares: drop sys/random.h on ios',
)

# 4. V8's gypfiles pin a macOS target for host generators; the historic 10.10
#    value is rejected by current SDKs.
patch(
    'tools/v8_gypfiles/toolchain.gypi',
    "'MACOSX_DEPLOYMENT_TARGET': '10.10',",
    "'MACOSX_DEPLOYMENT_TARGET': '11.0',",
    'v8 host macos deployment target 11.0',
    required=False,
)

# 5. Node 24's common.gypi has no iOS block: the darwin settings (including the
#    gnu++20 language standard Node 24 requires) live in the OS=="mac" block.
#    Mirror it for iOS, otherwise the target silently compiles as C++17 and
#    against macOS defaults.
path = os.path.join(root, 'common.gypi')
text = read(path)
if 'CodexApp iOS additions' in text:
    skips.append('common.gypi: iOS block already present')
else:
    # Anchor on the darwin target_defaults block specifically; a bare
    # OS=="mac" appears earlier for v8_base/obj_dir and must not be touched.
    anchor = """      ['OS=="mac"', {
        'defines': ['_DARWIN_USE_64_BIT_INODE=1'],"""
    if text.count(anchor) != 1:
        raise SystemExit('apply-ios-patches: expected exactly one darwin '
                         'target_defaults anchor in common.gypi, found %d'
                         % text.count(anchor))
    block = """      # CodexApp iOS additions: mirror the darwin block, which Node does not
      # provide for iOS. gnu++20 is required by Node 24 sources.
      ['OS=="ios"', {
        'defines': ['_DARWIN_USE_64_BIT_INODE=1'],
        # Apple frameworks Node/abseil expect on darwin. These also reach the
        # 'host' toolset, which is how build-time generators such as
        # bytecode_builtins_list_generator resolve CoreFoundation (abseil's
        # cctz calls CFTimeZoneGetName).
        'libraries': [
          '-framework CoreFoundation',
          '-framework Foundation',
          '-framework Security',
        ],
        'xcode_settings': {
          'IPHONEOS_DEPLOYMENT_TARGET': '%s',
          'GCC_VERSION': 'com.apple.compilers.llvm.clang.1_0',
          'CLANG_CXX_LANGUAGE_STANDARD': 'gnu++20',
          'CLANG_CXX_LIBRARY': 'libc++',
          'GCC_ENABLE_CPP_EXCEPTIONS': 'NO',
          'GCC_ENABLE_CPP_RTTI': 'NO',
          'GCC_ENABLE_PASCAL_STRINGS': 'NO',
          'GCC_STRICT_ALIASING': 'NO',
          'PREBINDING': 'NO',
          'USE_HEADERMAP': 'NO',
          'ALWAYS_SEARCH_USER_PATHS': 'NO',
          'WARNING_CFLAGS': [
            '-Wall',
            '-Wendif-labels',
            '-W',
            '-Wno-unused-parameter',
          ],
        },
        'target_conditions': [
          ['_type!="static_library"', {
            'xcode_settings': {
              'OTHER_LDFLAGS': [
                '-Wl,-search_paths_first'
              ],
            },
          }],
        ],
        'conditions': [
          ['target_arch=="arm64"', {
            'xcode_settings': {
              'ARCHS': ['arm64'],
              'OTHER_LDFLAGS!': [
                '-Wl,-no_pie',
              ],
            },
          }],
        ],
      }],
""" % deployment_target
    write(path, text.replace(anchor, block + anchor, 1))
    changes.append('common.gypi: inserted iOS block (deployment target %s, gnu++20)'
                   % deployment_target)

print('apply-ios-patches: applied %d change(s)' % len(changes))
for c in changes:
    print('  + ' + c)
for s in skips:
    print('  . ' + s)
