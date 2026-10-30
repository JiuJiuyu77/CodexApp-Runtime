# MinisApp-Runtime 运行时迁移状态

## 已完成（本地）

- Node.js 24.5.0 manifest 更新：npm 11.5.1、Corepack 0.34.0。
- OLLVM 23.1.1 manifest 更新：arm64/arm64e，记录上游声明的设备验证 pass。
- 新增 Node 24 与 OLLVM 23 上游镜像 workflow。
- 两个 workflow 都会校验下载来源和发布后的资产，验证通过后才清理各自过时的发行版。
- 当前 manifest 仍使用 Codex v0.162.1-4，因此 Codex release/tag 不在本轮清理范围内。

## 验证

- Node archive：29,495,021 bytes，SHA-256 `267278d0baafac4ba2ba4a0afee65f213bb5cad6acfb045ccc1a5732a31dca9c`
- OLLVM archive：36,348,135 bytes，SHA-256 `31667a83685e975dfdcd16578a0a386b7b9acc8e5f519b27f6e5d32fdee096b6`
- `runtime_adapter.py verify-manifest`：0 errors；Codex 项仍有 2 条既有 warning。
- `git diff --check`：通过。

## 待完成

本地工作区尚未提交或推送。推送 `main` 后，Node.js 24、OLLVM 23 镜像工作流会分别发布资产并校验；只有校验成功，才会删除旧 Node.js 18 和 OLLVM 16 releases。
