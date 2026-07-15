# 分支开发制度

- `main` 只保存已经验证并可供 MoviePilot 安装的插件版本。
- 新功能使用 `feature/<name>`；Codex 开发使用 `codex/<name>`；紧急修复使用 `fix/<name>`。
- 与 UBencode 客户端或授权服务联动的功能，两个仓库使用相同分支名。
- 功能分支完成语法检查、配置清单校验和真实 MoviePilot 联调后才能合并到 `main`。
- 插件版本号、`package.v2.json` 和更新说明必须保持一致。
- 发布前确认插件可以从仓库正常安装、升级和回退。

当前远程互动功能分支：`codex/remote-client-control`。
