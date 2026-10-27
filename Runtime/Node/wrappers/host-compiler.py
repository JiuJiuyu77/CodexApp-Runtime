#!/usr/bin/env python3
"""Host-toolset compiler wrapper for the iOS cross build.

Node's build-time code generators (node_js2c, torque, gen_node_def, ...) are
declared with 'toolsets': ['host'] because they must *run* on the build machine,
so they have to be compiled for the host (x86_64 on an Intel runner), not iOS.

gyp derives many flags from target_arch (arm64 here), and the build exports
SDKROOT pointing at the iPhoneOS SDK, which clang honours as an environment
variable. Left alone the generators become arm64 iOS binaries that the runner
cannot execute:

    /bin/sh: out/Release/node_js2c: Bad CPU type in executable
    ld: warning: building for 'iOS', but linking in dylib ... built for 'macOS'

This wrapper drops target-only flags, clears the inherited iOS toolchain
environment, and compiles against the macOS SDK for the host architecture.
Control-flow integrity and the iOS SDK stay in effect for the shipped arm64
code, which still goes through the iOS wrapper.

Installed as both `clang-host` and `clang++-host`; the driver comes from the
invoked name, and C++ always gets the C++20 standard Node 24 requires.
"""
import os
import sys

# Options that take a separate value and are meaningless for the host toolset.
DROP_WITH_VALUE = ('-arch', '-isysroot', '-target', '--target')

# Options that only make sense for the arm64 iOS target.
DROP_PREFIXES = (
    '-mbranch-protection=',
    '-miphoneos-version-min=',
    '-mios-simulator-version-min=',
)

# Environment that would re-introduce the iOS toolchain: clang reads SDKROOT
# itself, and the rest carry CFLAGS-style overrides from the target build.
SCRUB_ENV = (
    'SDKROOT',
    'IPHONEOS_DEPLOYMENT_TARGET',
    'CFLAGS',
    'CXXFLAGS',
    'CPPFLAGS',
    'LDFLAGS',
    'GYP_DEFINES',
)

args = []
skip_next = False
for arg in sys.argv[1:]:
    if skip_next:
        skip_next = False
        continue
    if arg in DROP_WITH_VALUE:
        skip_next = True
        continue
    if arg.startswith(DROP_PREFIXES):
        continue
    args.append(arg)

name = os.path.basename(sys.argv[0])
driver = 'clang++' if '++' in name else 'clang'

cmd = ['xcrun', '--sdk', 'macosx', driver]
if driver == 'clang++':
    # common.gypi only sets C++20 for the non-Apple OS list, and gyp's make
    # generator does not translate CLANG_CXX_LANGUAGE_STANDARD into -std=.
    cmd.append('-std=gnu++20')
cmd.extend(args)

# When linking (no -c/-E/-M), supply the Apple frameworks the generators need.
# abseil's cctz calls CFTimeZoneGetName, which lives in CoreFoundation; without
# this, host tools fail to link with "symbol(s) not found for architecture".
if not any(a in ('-c', '-E', '-M', '-MM') for a in args):
    cmd.extend(['-framework', 'CoreFoundation', '-framework', 'Foundation'])

env = dict(os.environ)
for key in SCRUB_ENV:
    env.pop(key, None)

os.execvpe(cmd[0], cmd, env)
