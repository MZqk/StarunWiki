# LLM Wiki 匿名公开问答

这是本仓库的只读派生服务。它只摄取 `00-知识库规范` 至 `09-踩坑与复盘` 中通过 LLM Wiki/OKF v0.2 校验的 51 个正式页面，以 WeKnora Wiki 页面、纯 Wiki Agent 和匿名 BFF 提供网页问答。`raw`、`archive`、`research`、`integrations` 和 `services` 均不进入问答语料。当前运行架构固定为单机单副本，WeKnora 与 BFF 分别使用一个持久化 SQLite 文件，不运行 PostgreSQL 或 Redis。

边界：

- 当前目录中的 Markdown 是唯一真相源；发布器不回写正文。
- 51 页当前均为 `stable` 但未完成人工审核，网页回答与引用会显示未审核提示。
- 项目不包含、不启动、不暴露 WeKnora 管理前端；Wiki 发布由本地 `publisher.py` CLI 完成。
- 不修改 WeKnora 上游工作树；SQLite Wiki 兼容改动以固定 hash 的构建补丁生成派生镜像。
- 不使用 Embed、Embedding、Rerank、文档上传或 chunk。
- 浏览器永远不接触模型 API Key、WeKnora API Key、JWT、Agent ID、KB ID 或 model ID。
- 当前只绑定 `127.0.0.1`，不构成公网部署。

## 部署前置依赖

本仓库包含公开网页、BFF、发布器、测试、Compose 覆盖和 WeKnora SQLite 兼容补丁；不复制上游 WeKnora 源码。部署前在仓库根目录准备与补丁匹配的上游源码：

```bash
git clone https://github.com/Tencent/WeKnora.git services/WeKnora
git -C services/WeKnora checkout 3d5d8bfcdfeeea266b292b71cea616847af28d0f
```

然后重建本地检索语料，并审查 `authorization/public-authorization.json` 所固定的公开语料快照。只有当语料 SHA、页面数与人工审批的范围一致时，才可生成 `authorization/public-manifest.json` 并继续发布；该运行时清单、数据库、会话和全部密钥不提交。

## 当前无 Docker 开发实例

当前运行的本机开发端口为：

- 公开问答网页：`http://127.0.0.1:5173`
- BFF 与健康检查：`http://127.0.0.1:8091`
- 内部 Wiki 运行时 API（仅 BFF/发布 CLI 使用）：`http://127.0.0.1:8080`

本地 Wiki 运行时使用 `WEKNORA_NATIVE_STATE_DIR="$HOME/Library/Application Support/llm-wiki-public-dev" ./run-native-weknora.sh`；它以 Go overlay 应用版本锁定的 SQLite 兼容补丁，绝不修改 `services/WeKnora` 上游工作树。BFF 使用 `go run .`，公开问答网页使用 Vite 开发服务器。模型和运行密钥仅由 `.env` 与 `.secrets/*.env` 注入后端进程，不进入浏览器。

## Docker Compose 备用流程

```bash
./manage.sh init
# 编辑 .env：填入随机密码、LLM_BASE_URL 和 LLM_MODEL_NAME
./manage.sh manifest
./manage.sh test
./manage.sh infra
```

`infra` 默认只启动 DocReader 和精简 Wiki 运行时，不启动 WeKnora 管理前端。PostgreSQL、Redis 被放在非默认的 `legacy-databases` profile 中，不会被创建。即使已有 `.env` 仍包含旧数据库变量，Compose 也会显式覆盖为：

```text
DB_DRIVER=sqlite
DB_PATH=/data/weknora/weknora.db
RETRIEVE_DRIVER=sqlite
REDIS_ADDR=
STREAM_MANAGER_TYPE=memory
PUBLIC_DB_PATH=/data/public-bff/public-bff.db
```

该 Compose 使用独立 `llm-wiki-public` project/volume/container 名，新库首个 Tenant ID 固定为 `10000`。首次启动所需的 Owner 凭据通过本地后端初始化流程取得；之后以 Owner JWT 和模型 API Key 从交互式标准输入运行：

```bash
./manage.sh reload-model
./manage.sh bootstrap
./manage.sh publish
./manage.sh check
./manage.sh start
```

容器模式的唯一网页地址为 `http://127.0.0.1:8090`。内部 Wiki 运行时不提供管理网页。

`bootstrap` 创建/校验唯一 KnowledgeQA 模型并配置 signed-token principal；`publish` 新建绿色 Wiki-only KB、51 页、纯 Wiki Agent 和 chat-only API Key。AI API Key 仅经交互式 stdin 进入 WeKnora credentials API。`check` 会真实执行 `wiki_search → wiki_read_page` 流式工具调用，网关未就绪时必然阻断。

## SQLite 数据与限制

- `llm-wiki-public-weknora-sqlite-data` 保存 Wiki 页面、Agent、模型凭据、上游会话和消息。
- `llm-wiki-public-bff-sqlite-data` 保存匿名会话映射、回合、限流、并发租约、幂等结果和清理队列。
- BFF 与 WeKnora 都只允许单副本。已完成的消息和匿名状态可跨重启恢复；WeKnora 的活动流管理器在内存中，重启会中断当时正在生成的回答。
- 备份必须同时覆盖两个 SQLite 卷，并单独安全保存 `SYSTEM_AES_KEY`；缺少该密钥将无法解密 WeKnora 中的模型凭据。
- 旧 PostgreSQL/Redis volume 不会由本项目自动删除，便于需要时人工恢复。
