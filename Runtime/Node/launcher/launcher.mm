// bin/node launcher for the CodexApp Node runtime.
//
// The iOS Node runtime ships Node itself inside NodeMobile.framework. This tiny
// executable links against that framework and forwards argv to node::Start()
// (exposed as node_start()), giving the runtime the usual Node.js CLI layout:
// bin/node, with bin/npm and friends symlinked to it.
//
// Kept deliberately free of any pre-main work: nothing here may allocate, open
// files, or touch the environment before Node initialises itself. The framework
// import is intentional: it proves the framework's public headers are wired up
// correctly at build time instead of failing at launch on device.

#include <NodeMobile/NodeMobile.h>

extern "C" int node_start(int argc, char *argv[]);

int main(int argc, char *argv[]) {
  return node_start(argc, argv);
}
