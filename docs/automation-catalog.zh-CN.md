# Maestro 用例包管理

「用例中心 → Maestro 用例」管理独立的 Case / Test Plan 版本，可上传、查看 YAML、查看附带截图、下载和删除。所有 API 需要登录，不同账号的同名资源相互隔离。上传只校验格式与包结构，不执行 YAML，也不代表真实测试已通过。

## 从目录上传

上传包含 `flow.yaml` 的 Maestro 工作目录，选择入口 flow，填写唯一 Case ID 和名称即可。依赖 YAML 及资源会保留在包内；截图统一为 `flow.assets/<文件名>`，同名截图会拒绝。可使用仓库中的 `docs/examples/maestro` 目录练习导入；它仅为界面验证示例，没有可运行的实际业务 App。

## ZIP 包格式

- Case 扩展名为 `.sabcase`，ZIP 至少包含 `manifest.json` 与 `flow.yaml`。manifest 的 `caseID`、`name` 必须与上传表单一致。
- Test Plan 扩展名为 `.sabplan`，ZIP 至少包含 `manifest.json` 与 `plan.yaml`。manifest 包含 `planID`、`name` 和 `dependencies`；每项依赖含 `caseID`、正整数 `version` 和 64 位 `sha256`。
- 可选的 `# sab-recorded-step:`、`# sab-before-screenshot:`、`# sab-result-screenshot:` 注释用于步骤与证据展示；普通 Maestro flow 没有这些注释时仍可保存并查看 YAML。
- 此格式属于公开 SAB，不承诺与其他客户端的私有包协议兼容。没有私有商店、自动执行器或外部发布依赖。

目录保存在 `SAB_DATA_DIR/automation_catalog`（默认 `backend/data/automation_catalog`）。包上传、解压总量、文件数量及路径均有限制；不要上传真实账号、密钥、业务源码或未经检查的截图。下载后执行 Maestro 是单独操作，应先检查 YAML 中的命令及资源。
