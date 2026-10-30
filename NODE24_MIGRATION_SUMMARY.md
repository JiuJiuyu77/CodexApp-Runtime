# Node.js 24.5.0 / OLLVM 23.1.1 运行时迁移总结

## 本轮状态

- 已核验上游 Node.js 24.5.0 与 Arkari OLLVM 23.1.1 的发行版 metadata、大小和 SHA-256。
- 已更新 `Runtime/runtime-manifest.json`。
- 已新增两个镜像工作流：先验证上游，再发布到 MinisApp-Runtime，最后重新下载校验，成功后才清理旧发行版。
- 当前工作区改动尚未提交或推送。

## Node.js 24.5.0

上游 tag：`runtime-node-24.5.0-e3a6c25fac33`

- Node.js `24.5.0`
- npm / npx `11.5.1`
- Corepack `0.34.0`
- node-gyp `11.2.0`
- 归档大小：`29,495,021` bytes
- SHA-256：`267278d0baafac4ba2ba4a0afee65f213bb5cad6acfb045ccc1a5732a31dca9c`

## OLLVM 23.1.1

上游 tag：`runtime-ollvm-23.1.1-e54f2878240e`

- Arkari LLVM `23.1.1`
- 归档大小：`36,348,135` bytes
- SHA-256：`31667a83685e975dfdcd16578a0a386b7b9acc8e5f519b27f6e5d32fdee096b6`
- arm64 / arm64e
- device-validated pass：`cse`、`cie`、`cfe`、`fla`
- revision：`e54f2878240e8cdc9122450b9c2afd6800fbd64bca05712d512a3fb02a11e958`

## 清理规则

目标仓库目前有 6 个 Node.js 18 releases 和 1 个 OLLVM 16 release。镜像工作流会：

1. 下载并校验上游 bundle 与归档。
2. 校验 manifest 中的版本、revision、大小、哈希、pass 和下载 URL。
3. 创建或刷新目标 release。
4. 重新下载目标 release 并再次校验。
5. 仅在上述步骤全部成功后删除 Node.js 18 或 OLLVM 16 releases。

当前 manifest 仍引用 `runtime-codex-v0.162.1-4`，所以 Codex releases 保留，不会被本轮 Node/OLLVM 清理误删。

## 推送

```bash
./PUSH_NODE24.sh
```

推送后由以下 Actions 自动执行：

- `Mirror Node.js 24.5.0 Runtime`
- `Mirror Arkari OLLVM 23.1.1 Runtime`
- `Validate runtime manifest`

MinisApp 更新后应验证：

```text
node --version       -> v24.5.0
ollvm clang --version -> clang version 23.1.1
```
