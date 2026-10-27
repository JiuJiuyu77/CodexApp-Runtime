// Forces the linker to retain Node's entry point in NodeMobile.framework.
//
// The framework is a -dynamiclib built over libnode.a. Without something that
// references node::Start(), the archive members holding it would be dropped and
// bin/node would fail to resolve node_start() at launch. This translation unit
// pulls the symbol in and exports the C wrapper the launcher calls.

namespace node {
int Start(int argc, char* argv[]);
}

extern "C" int node_start(int argc, char* argv[]) {
  return node::Start(argc, argv);
}
