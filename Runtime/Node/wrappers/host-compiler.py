#!/usr/bin/env python3
"""Host-toolset compiler wrapper for the iOS cross build.

Node's build-time code generators (node_js2c, torque, gen_node_def, ...) are
declared with 'toolsets': ['host'] because they must *run* on the build machine.
They therefore have to be compiled for the host (x86_64 on an Intel runner),
not for iOS.

gyp, however, derives many flags from target_arch, which is arm64 here, so the
host compile inherits target-only options. Two break it outright:

    clang++: error: unsupported option '-mbranch-protection=' for target
             'x86_64-apple-darwin25.6.0'
    (and an inherited -arch arm64 / -isysroot iPhoneOS would produce an arm64
     iOS object the x86_64 runner cannot load)

So the host toolset gets a wrapper that drops target-only options and compiles
against the macOS SDK for the host architecture. Control-flow integrity stays
enabled for the shipped arm64 code, which still uses the iOS wrapper.

Installed as both `clang-host` and `clang++-host`; the driver is chosen from the
invoked name, and C++ always gets the C++20 standard Node 24 requires.
"""
import os
import sys

# Options that take a separate value and are meaningless for the host toolset.
DROP_WITH_VALUE = ('-arch', '-isysroot', '-target', '--target')

# Options that are only meaningful for the arm64 iOS target.
DROP_PREFIXES = (
    '-mbranch-protection=',
    '-miphoneos-version-min=',
    '-mios-simulator-version-min=',
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

os.execvp(cmd[0], cmd)
