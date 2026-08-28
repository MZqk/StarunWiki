# v0.2 兼容入口

此目录只保留 StarunWiki `v0.1` 命令入口的薄 wrapper，不再承载 Web、BFF、publisher、Compose、发布快照或正式运维文档。

- 正式 CLI：[`../../manage.sh`](../../manage.sh)
- 产品说明：[`../../README.md`](../../README.md)
- 运维与迁移：[`../../docs/operations.md`](../../docs/operations.md)
- 默认知识包：[`../../knowledge-packs/deep-sky/README.md`](../../knowledge-packs/deep-sky/README.md)

`manage.sh` 与 `run-native-weknora.sh` 会输出弃用提示并转发到新入口；兼容 wrapper 计划在 `v0.3.0` 删除。本目录内被 Git 忽略的旧 `.env`、`.secrets`、`.runtime`、数据库和备份仍属于现有部署状态，迁移前不得删除或逐文件混用。
