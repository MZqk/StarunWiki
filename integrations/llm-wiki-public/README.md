# LLM Wiki 匿名公开问答

这是本仓库的只读派生服务。它只摄取 `00-知识库规范` 至 `09-踩坑与复盘` 中通过 LLM Wiki/OKF v0.2 校验并进入固定授权快照的 51 个正式页面，以 WeKnora Wiki 页面、纯 Wiki Agent 和匿名 BFF 提供网页问答。`raw`、`archive`、`research`、`integrations` 和 `services` 均不进入问答语料。

当前运行架构固定为单机单副本：WeKnora 与 BFF 分别使用一个持久化 SQLite 文件，不运行 PostgreSQL 或 Redis。

数据流如下：

```text
正式 Markdown 页面
  -> .knowledge-catalog/retrieval-corpus.jsonl
  -> authorization/public-authorization.json 固定 SHA/页数/审核合同
  -> authorization/public-manifest.json 确定性派生清单
  -> WeKnora Wiki-only 知识库和 Agent
  -> 匿名 BFF
  -> 公开网页
```

## 安全与发布边界

- 仓库根目录 `00-` 至 `09-` 的正式 Markdown/Git 是唯一真相源；发布器只创建派生数据，不回写正文。
- 当前固定快照包含 51 个 `stable` 页面，但 51 页均仍是 `needs-human-review`；网页回答和引用会保留未审核提示。
- 项目不包含、不启动、不暴露 WeKnora 管理前端；Wiki 发布由本地 `publisher.py` CLI 完成。
- 不修改 WeKnora 上游工作树；SQLite Wiki 兼容改动由固定 hash 的补丁生成派生镜像或 Go overlay。
- 不使用 Embed、Embedding、Rerank、文档上传或 chunk，只允许 Agent 调用 `wiki_search` 和 `wiki_read_page`。
- 浏览器永远不接触模型 API Key、WeKnora API Key、Owner JWT、Agent ID、KB ID 或 model ID。
- 默认只绑定 `127.0.0.1`。这是一套本机部署配置，不等于已经完成公网 TLS、WAF、外部限流或安全审计。
- `authorization/public-authorization.json` 是固定授权合同。Markdown 或候选语料发生漂移时，发布必须失败；不要通过跳过 SHA、页数或审核门禁强行发布。

> [!WARNING]
> 根目录 README 当前记录了“正式 Markdown 与固定授权快照存在预期漂移”。在发布负责人完成审核并明确更新固定授权前，`test_current_wiki`、新候选 `manifest` 或 `publish` 的 fail-closed 失败是预期结果，不是应绕过的部署故障。

## 目录与运行产物

| 路径 | 作用 | 是否含敏感信息 |
| --- | --- | --- |
| `.env.example` | `./manage.sh init` 的唯一配置模板 | 否，只有占位值 |
| `.env` | 操作者配置；由 `init` 首次复制 | 是；Git 忽略，建议权限 `0600` |
| `.secrets/bootstrap.json` | `bootstrap` 生成的模型/签名主体状态 | 是；Git 忽略，权限 `0600` |
| `.secrets/runtime.env` | `publish` 生成的 BFF 上游凭据和发布 ID | 是；Git 忽略，权限 `0600` |
| `authorization/public-authorization.json` | 人工批准的语料 SHA、页数和审核合同 | 否 |
| `authorization/public-manifest.json` | `manifest` 生成的确定性公开页面清单 | 不含密钥；发布前仍需审查 |
| `release-state.json` | `publish/check` 使用的本地 release、KB、Agent、model 状态 | 含内部 ID；Git 忽略 |
| `builtin_models.yaml` | WeKnora 启动时加载的唯一 KnowledgeQA 模型定义 | 否；API Key 不在这里 |
| `docker-compose.local.yml` | 对上游 Compose 的 SQLite、BFF、网页覆盖 | 否 |
| `run-native-weknora.sh` | 不改上游工作树的本机 Go overlay 启动器 | 否 |

Docker 模式会新增两个承载产品状态的 SQLite 命名卷：

- `llm-wiki-public-weknora-sqlite-data`：Wiki 页面、Agent、模型凭据、上游会话和消息。
- `llm-wiki-public-bff-sqlite-data`：匿名会话映射、回合、限流、并发租约、幂等结果和清理队列。

合并上游 Compose 后还会出现 `data-files`、`docreader-tmp` 等运行卷；它们不是上述两个 SQLite 主库，但做卷盘点或灾备时不能把“两个 SQLite 卷”误解为整个 Compose 项目只有两个卷。

## 前置依赖

需要准备：

- Git、Bash、ripgrep（`rg`）、`jq`，以及 `cp`、`chmod`、`mkdir`、`curl` 等常规命令；首次 `init` 需要能访问 GitHub，本机 overlay 还需要 `patch`、`shasum`、`awk` 和 `gofmt`。
- Python 3.11 或更高版本，以及 `uv`。
- Docker Engine 和较新的 Docker Compose v2；Compose 必须支持 `!reset`、`!override` 和 `env_file.required`。
- 执行完整测试或本机开发时，还需要 Go 1.26、Node.js `^20.19.0` 或 `>=22.12.0`，以及 npm。
- 生成随机密钥建议使用 OpenSSL。

从仓库根目录进入集成并启动一个 Bash 会话；本文后续 shell 片段均假定仍在这个目录和 Bash 会话中：

```bash
cd integrations/llm-wiki-public
bash
```

`./manage.sh init` 会在缺少源码时自动克隆 WeKnora `v0.7.2`，并核对提交 `3d5d8bfcdfeeea266b292b71cea616847af28d0f`。`test` 会再次执行补丁可应用性检查。不要预先把本项目补丁直接应用到 `services/WeKnora` 工作树。

## `./manage.sh init` 详解

### 它实际做什么

```bash
./manage.sh init
```

该命令完成本地配置和锁定依赖初始化：

1. 当 `.env` 不是已有普通文件时，逐字复制 `.env.example` 为 `.env`，再设置为 `0600`。
2. 创建 `.secrets/`（如果尚不存在），并把目录权限设置为 `0700`。
3. 当仓库根目录下不存在 `services/WeKnora` 时，自动创建 `services/`，从腾讯上游浅克隆 `v0.7.2`，并验证其提交 SHA 与本项目锁定值完全一致。
4. 当 `services/WeKnora` 已存在时，只验证它是干净的 Git 工作树、提交正确且包含 `docker-compose.yml`；不会自动 fetch、checkout、覆盖或丢弃本地改动。
5. 不生成任何随机密钥，不校验 `.env` 占位值，也不会把 `.env` 导出到当前 shell。

它是有限幂等的：已有普通文件 `.env` 时不会覆盖、补充新字段、重新校验，也不会修复该文件权限；已有正确且干净的 WeKnora 工作树不会重复下载；只会确保 `.secrets/` 存在并设为 `0700`。模板升级后请人工比较新字段，并按需执行：

```bash
chmod 600 .env
```

> [!IMPORTANT]
> 命令打印 `WeKnora source ready` 和最后的 `initialized ...`，才表示配置文件及锁定源码准备步骤均没有报错；这仍不表示密钥、模型或发布授权已经可用。
> 如果 `.env` 路径已经存在但不是普通文件（例如误建成目录），请先停止并修复路径；不要继续执行后续命令。
> 如果 WeKnora 目录存在但提交错误、包含本地改动或不是 Git 工作树，`init` 会 fail-closed。请先审计并保存其中的修改，不要为通过初始化而直接删除或强制覆盖。

自动下载失败时，应先修复网络；若部署机不能访问 GitHub，可在可信且可联网环境的仓库根目录中人工准备同一锁定版本，再把包含 `.git` 的完整、干净目录安全传输到部署机的 `services/WeKnora`。准备命令为：

```bash
git clone --depth 1 --branch v0.7.2 --single-branch \
  https://github.com/Tencent/WeKnora.git services/WeKnora
git -C services/WeKnora rev-parse HEAD
```

预期 SHA 为 `3d5d8bfcdfeeea266b292b71cea616847af28d0f`。

### 首次生成的 `.env` 完整案例

以下内容与当前 `.env.example` 一致；`init` 不会对其插值或随机化：

```dotenv
WEKNORA_VERSION=v0.7.2

DB_DRIVER=sqlite
DB_PATH=/data/weknora/weknora.db
RETRIEVE_DRIVER=sqlite
STREAM_MANAGER_TYPE=memory
JWT_SECRET=CHANGE_ME_JWT_SECRET
SYSTEM_AES_KEY=CHANGE_ME_AES_KEY_32_BYTES
AUTO_MIGRATE=true
GIN_MODE=release
LOG_LEVEL=info
TZ=Asia/Shanghai

DISABLE_REGISTRATION=false
WEKNORA_AUTH_DEFAULT_TENANT_MODE=create_personal
WEKNORA_TENANT_SELF_SERVICE_CREATION_ENABLED=false
WEKNORA_TENANT_ENABLE_CROSS_TENANT_ACCESS=false
WEKNORA_TENANT_ENABLE_RBAC=true
WEKNORA_TENANT_AUTO_CREATE_API_KEY=false
WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL=

STORAGE_TYPE=local
STORAGE_ALLOW_LIST=local
LOCAL_STORAGE_BASE_DIR=/data/files
NEO4J_ENABLE=false
LANGFUSE_ENABLED=false
OLLAMA_OPTIONAL=true
MAX_FILE_SIZE_MB=10

WEKNORA_BASE_URL=http://app:8080
WEKNORA_ADMIN_BASE_URL=http://127.0.0.1:8080
WEKNORA_TENANT_ID=1
WEKNORA_CHAT_API_KEY=
WEKNORA_EXTERNAL_HMAC_SECRET=
WEKNORA_AGENT_ID=
WEKNORA_KB_ID=
WEKNORA_MODEL_ID=

LLM_BASE_URL=http://host.docker.internal:50288/v1
LLM_MODEL_NAME=
LLM_PROVIDER=openai

PUBLIC_LISTEN_ADDR=:8091
PUBLIC_ORIGIN=http://127.0.0.1:8090
PUBLIC_COOKIE_SECURE=false
PUBLIC_COOKIE_SECRET=CHANGE_ME_PUBLIC_COOKIE_SECRET
PUBLIC_CONVERSATION_TTL=168h
PUBLIC_STREAM_TIMEOUT=120s
PUBLIC_GLOBAL_CONCURRENCY=20
PUBLIC_TRUSTED_PROXY_CIDRS=172.16.0.0/12
PUBLIC_DB_PATH=/data/public-bff/public-bff.db

APP_PORT=8080
PUBLIC_PORT=8090
```

模板共有 47 个变量声明：3 个 `CHANGE_ME`、7 个空值。首次启动前最少需要完成以下动作：

1. 替换 `JWT_SECRET`、`SYSTEM_AES_KEY`、`PUBLIC_COOKIE_SECRET`。
2. 填写 `LLM_MODEL_NAME`。
3. 为全新数据库填写首个 Owner 的 `WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL`；它必须与稍后注册使用的邮箱完全一致。已有 system admin 的数据库可留空。
4. 核对 `LLM_BASE_URL` 和 `LLM_PROVIDER` 是否与实际 OpenAI-compatible 模型网关一致。
5. 保持 5 个 `WEKNORA_*` 发布期字段为空；它们由 `bootstrap/publish` 写入 `.secrets/`，不应人工复制回 `.env`。

可分别生成三个值后粘贴到 `.env`，不要把输出贴到 issue、聊天记录或日志：

```bash
openssl rand -hex 32
openssl rand -hex 16
openssl rand -hex 32
```

建议依次用于：

- 第一个 64 字符值：`JWT_SECRET`。
- 第二个 32 字符值：`SYSTEM_AES_KEY`；它必须恰好是 32 字节，不能使用模板中的 26 字节占位文本。
- 第三个 64 字符值：`PUBLIC_COOKIE_SECRET`。

`SYSTEM_AES_KEY` 用于保护数据库中的模型 API Key 等凭据。首次写入凭据后必须长期保存原值；丢失、改短或轮换都会使已有密文无法解密。当前上游在密钥长度不合法时无法取得加密 key，新写入的模型 API Key 会按 legacy 明文兼容格式落盘，因此不要在长度检查通过前执行 `bootstrap`。

可在不打印密钥的情况下检查长度：

```bash
LC_ALL=C awk -F= '$1 == "SYSTEM_AES_KEY" { print length(substr($0, index($0, "=") + 1)) }' .env
```

预期只输出 `32`。已有 `.env` 不会被 `init` 补充模板新增字段；先逐项检查键名是否完整：

```bash
missing=0
while IFS='=' read -r name _; do
  [[ -z "$name" ]] && continue
  if ! rg -q "^${name}=" .env; then
    printf '缺少环境变量：%s\n' "$name" >&2
    missing=1
  fi
done < .env.example
(( missing == 0 )) || exit 1
```

如果工作区已有旧 `.env`，它可能缺少新加入的 `WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL`；上面的检查会明确报告，操作者应从 `.env.example` 人工补入，不能指望再次运行 `init` 自动合并。

再检查是否仍有必须处理的通用占位值和空值：

```bash
rg -n 'CHANGE_ME|^(JWT_SECRET|SYSTEM_AES_KEY|PUBLIC_COOKIE_SECRET|LLM_BASE_URL|LLM_MODEL_NAME|LLM_PROVIDER)=$' .env
```

通用检查通过时，该命令应无输出；`SYSTEM_AES_KEY` 还必须单独满足前述 32 字节检查。全新数据库还要单独执行：

```bash
rg -n '^WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL=$' .env
```

全新数据库应无输出；已有 system admin 的数据库允许该行为空，因此这项检查可以输出一行。

### 需人工填写或核对的变量

| 变量 | 用途 | 规则 |
| --- | --- | --- |
| `JWT_SECRET` | 签发 WeKnora 登录 JWT | 使用强随机值；改变后现有 Owner JWT 失效 |
| `SYSTEM_AES_KEY` | 加密 WeKnora 数据库中的模型/API 凭据 | 必须恰好 32 字节；首次使用后不得遗失或随意轮换 |
| `LLM_BASE_URL` | WeKnora 容器访问的 OpenAI-compatible `/v1` 地址 | 容器内的 `127.0.0.1` 指容器自身；宿主网关通常使用 `host.docker.internal` |
| `LLM_MODEL_NAME` | `builtin-llm-wiki-chat` 实际调用的模型名 | 不得为空，必须是网关真实支持的名称 |
| `LLM_PROVIDER` | WeKnora 模型 provider | 当前模板为 `openai`；需与网关兼容 |
| `PUBLIC_COOKIE_SECRET` | 从 BFF 匿名 Cookie 派生 visitor ID 的 HMAC 密钥 | 至少 16 字符且不得含 `CHANGE_ME`；轮换会重置访客身份，使原匿名会话不可访问 |
| `WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL` | 在数据库尚无 system admin 时，把同邮箱的已注册用户一次性提升为 system admin | 新库必须填写，并与首个 Owner 注册邮箱完全一致；已有 system admin 时可留空 |

模型 API Key 不写入 `.env`。确认 `SYSTEM_AES_KEY` 已通过 32 字节检查后，`./manage.sh bootstrap` 会用隐藏输入提示读取一次，并通过 WeKnora credentials API 加密保存到 SQLite。

Owner JWT 也不写入 `.env`。`bootstrap/publish/check` 默认会逐次隐藏提示输入；如需在同一私有终端临时复用，应使用静默读取或下文不含 token 字面值的登录响应解析方式，完成后立即 `unset WEKNORA_OWNER_TOKEN`。

### 应保持为空、由发布流程生成或解析写入的变量

| `.env` 中的变量 | 生成阶段 | 实际保存位置 | 作用 |
| --- | --- | --- | --- |
| `WEKNORA_EXTERNAL_HMAC_SECRET` | `bootstrap` | `.secrets/bootstrap.json`，随后写入 `runtime.env` | signed-token principal 的 HMAC 密钥 |
| `WEKNORA_CHAT_API_KEY` | `publish` | `.secrets/runtime.env` | 90 天有效、仅 `chat` capability 的上游 API Key |
| `WEKNORA_AGENT_ID` | `publish` | `.secrets/runtime.env` | BFF 固定调用的 Wiki-only Agent |
| `WEKNORA_KB_ID` | `publish` | `.secrets/runtime.env` | 固定公开快照对应的绿色 KB |
| `WEKNORA_MODEL_ID` | `bootstrap/publish` | `bootstrap.json`、`runtime.env`、`release-state.json` | 发布状态记录；浏览器不可见 |

`publish` 还会在 `runtime.env` 中写入不属于模板的 `PUBLIC_RELEASE_ID`。生成后的结构如下，尖括号内容只是说明，不是可用值：

```dotenv
WEKNORA_TENANT_ID=1
WEKNORA_CHAT_API_KEY=<自动生成的-chat-only-key>
WEKNORA_EXTERNAL_HMAC_SECRET=<bootstrap-自动生成>
WEKNORA_AGENT_ID=<publish-自动生成>
WEKNORA_KB_ID=<publish-自动生成>
WEKNORA_MODEL_ID=builtin-llm-wiki-chat
PUBLIC_RELEASE_ID=public-<corpus-sha-prefix>
```

不要手工编辑这些生成文件。重新发布会创建新的 KB、Agent 和 chat-only key，并不是对旧发布的原地无副作用重放。

chat-only key 的有效期为创建后 90 天。应记录创建时间并在到期前按完整 `publish -> check -> start/重建` 流程受控切换；切换成功后先审计引用与运行状态，再回收旧 KB、Agent 或 key，不要等线上出现上游 401 才处理。

### 其余变量说明

#### WeKnora、SQLite 与进程

| 变量 | 模板值 | 说明 |
| --- | --- | --- |
| `WEKNORA_VERSION` | `v0.7.2` | 上游/DocReader 版本锁；本项目补丁和派生镜像也锁定到 v0.7.2，不能只改这一项升级 |
| `DB_DRIVER` | `sqlite` | WeKnora 数据库驱动；Docker 覆盖层会强制为 `sqlite` |
| `DB_PATH` | `/data/weknora/weknora.db` | WeKnora SQLite 路径；Docker 覆盖层会强制为该容器路径 |
| `RETRIEVE_DRIVER` | `sqlite` | 检索驱动；Docker 覆盖层会强制为 `sqlite` |
| `STREAM_MANAGER_TYPE` | `memory` | 活动流状态保存在内存；Docker 覆盖层会强制为 `memory` |
| `AUTO_MIGRATE` | `true` | 启动时执行数据库迁移 |
| `GIN_MODE` | `release` | WeKnora Gin 运行模式；release 模式不用于暴露管理 Swagger |
| `LOG_LEVEL` | `info` | WeKnora 日志级别 |
| `TZ` | `Asia/Shanghai` | 容器时区 |

#### 注册、租户与 RBAC

| 变量 | 模板值 | 说明 |
| --- | --- | --- |
| `DISABLE_REGISTRATION` | `false` | 首次创建 Owner 时允许注册；初始化完成并准备对外代理前应重新评估并关闭 |
| `WEKNORA_AUTH_DEFAULT_TENANT_MODE` | `create_personal` | 注册用户时创建个人 Tenant |
| `WEKNORA_TENANT_SELF_SERVICE_CREATION_ENABLED` | `false` | 禁止普通用户自行再创建 Tenant |
| `WEKNORA_TENANT_ENABLE_CROSS_TENANT_ACCESS` | `false` | 禁止跨 Tenant 访问 |
| `WEKNORA_TENANT_ENABLE_RBAC` | `true` | 启用 Tenant RBAC |
| `WEKNORA_TENANT_AUTO_CREATE_API_KEY` | `false` | 不为新 Tenant 自动创建 full-access API Key |
| `WEKNORA_TENANT_ID` | `1` | 全新 SQLite 卷的首个自增 Tenant ID；已有数据库必须填写登录响应中的实际 ID，创建发布资源后不要擅自改动 |

#### 存储与未启用能力

| 变量 | 模板值 | 说明 |
| --- | --- | --- |
| `STORAGE_TYPE` | `local` | 使用本地文件存储 |
| `STORAGE_ALLOW_LIST` | `local` | 只允许本地存储后端 |
| `LOCAL_STORAGE_BASE_DIR` | `/data/files` | WeKnora 容器内文件目录 |
| `NEO4J_ENABLE` | `false` | 关闭 Neo4j/GraphRAG |
| `LANGFUSE_ENABLED` | `false` | 关闭 Langfuse |
| `OLLAMA_OPTIONAL` | `true` | Ollama 不可达时不阻断启动 |
| `MAX_FILE_SIZE_MB` | `10` | 文件/DocReader 上限；本产品本身不开放上传 |

#### URL、BFF 与端口

| 变量 | 模板值 | 说明 |
| --- | --- | --- |
| `WEKNORA_BASE_URL` | `http://app:8080` | BFF 容器访问 WeKnora 的内部地址 |
| `WEKNORA_ADMIN_BASE_URL` | `http://127.0.0.1:8080` | 宿主上的发布 CLI 访问 WeKnora；改 `APP_PORT` 时同步修改 |
| `PUBLIC_LISTEN_ADDR` | `:8091` | BFF 容器监听地址；Nginx 固定代理到 `public-bff:8091`，Compose 模式不要单独修改 |
| `PUBLIC_ORIGIN` | `http://127.0.0.1:8090` | BFF 严格接受的浏览器 Origin；改 `PUBLIC_PORT` 或接入 HTTPS 域名时同步修改 |
| `PUBLIC_COOKIE_SECURE` | `false` | 本机 HTTP 使用 `false`；只有真正由 HTTPS 访问时才设为 `true` |
| `PUBLIC_CONVERSATION_TTL` | `168h` | 匿名会话保留 7 天 |
| `PUBLIC_STREAM_TIMEOUT` | `120s` | 单次流式回答的总超时 |
| `PUBLIC_GLOBAL_CONCURRENCY` | `20` | 单个 BFF 进程的全局同时回答上限 |
| `PUBLIC_TRUSTED_PROXY_CIDRS` | `172.16.0.0/12` | 只有直连地址命中这些 CIDR 的代理才可提供可信 `X-Real-IP` 供限流使用；不要扩大为任意来源 |
| `PUBLIC_DB_PATH` | `/data/public-bff/public-bff.db` | BFF SQLite 路径；Docker 覆盖层固定为该容器路径 |
| `APP_PORT` | `8080` | 宿主 `127.0.0.1` 映射到 WeKnora `8080` |
| `PUBLIC_PORT` | `8090` | 宿主 `127.0.0.1` 映射到公开网页 `80` |

## Docker Compose 完整流程

### 1. 初始化并导出配置

```bash
./manage.sh init
# 按上一节编辑 .env
set -a
. ./.env
set +a

wait_http() {
  local url="$1"
  local attempt
  for attempt in {1..60}; do
    if curl -fsS "$url" >/dev/null; then
      return 0
    fi
    sleep 2
  done
  printf '服务在 120 秒内未就绪：%s\n' "$url" >&2
  return 1
}
```

`docker compose` 子命令通过 `--env-file .env` 读取配置；但当前 shell 已导出的同名变量优先级更高，而 `manifest/bootstrap/publish/check` 又是直接调用 Python 发布器，`manage.sh` 不会自动 `source .env`。因此每个新终端以及每次编辑 `.env` 后，都必须重新执行上面的 `set -a` 三行。`wait_http` 用于规避容器刚进入 `started`、应用尚未接受请求的就绪竞态；新终端也要重新定义它。

### 2. 预览候选语料，不覆盖固定快照

从本目录执行：

```bash
python3 -B ../../scripts/build_knowledge_catalog.py
python3 -B ../../scripts/build_knowledge_catalog.py \
  --write \
  --output /tmp/llm-wiki-public-candidate.jsonl
```

第一条只输出当前正式 Markdown 的页数、待审核数和 SHA；第二条把候选语料写到临时文件。不要因为候选生成成功就覆盖发布器默认读取的 `.knowledge-catalog/retrieval-corpus.jsonl` 或修改固定合同 `public-authorization.json`。

只有发布负责人完成人工审核，并明确批准新的 corpus SHA、页数和审核范围后，才可更新固定授权与默认语料。固定语料满足授权合同时执行：

```bash
./manage.sh manifest
./manage.sh plan
```

- `manifest` 重新计算并写入 `authorization/public-manifest.json`；SHA 或页数不一致时以状态 2 失败。
- `plan` 只打印将创建的页数、KB、Agent 和 key，不调用 WeKnora，也不发布。

### 3. 运行本地验证

```bash
npm --prefix web ci
./manage.sh test
```

该命令依次运行：

1. Python publisher/current-wiki/Compose 单元测试。
2. `git apply --check`，确认补丁仍可应用到锁定的 WeKnora 上游源码。
3. BFF 的 `go test -race ./...`。
4. Web 的 `npm test` 和生产构建。

它验证本地合同和构建，不等于模型网关、真实 Agent 问答或公网部署已经通过。若根 README 所记录的当前语料漂移仍存在，完整 `test` 中的 `test_current_wiki` 应 fail-closed；不要为了得到绿色结果削弱授权检查。

### 4. 启动 WeKnora 基础设施

```bash
./manage.sh infra
wait_http http://127.0.0.1:8080/health
```

`infra` 只构建并启动 `docreader` 与精简 WeKnora `app`，不会启动 BFF、公开网页、WeKnora 管理前端、PostgreSQL 或 Redis。

Compose 会显式强制以下运行值，即使 `.env` 中仍有旧数据库变量：

```text
DB_DRIVER=sqlite
DB_PATH=/data/weknora/weknora.db
RETRIEVE_DRIVER=sqlite
REDIS_ADDR=
STREAM_MANAGER_TYPE=memory
PUBLIC_DB_PATH=/data/public-bff/public-bff.db
```

### 5. 创建并核对首个 Owner

本产品不启动 WeKnora 管理前端，因此新卷需要通过回环 API 注册首个 Owner，再由启动引导把它提升为 system admin。普通 Tenant Owner 无权为内置模型写入 credentials；如果跳过提升，后续 `bootstrap` 会返回 `403`。

开始前确认 `.env` 的 `WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL` 与本次注册邮箱完全一致。用户名长度必须为 2–50 个字符，邮箱必须合法，密码至少 6 个字符。只在可信、单用户主机上完成“开放注册 → 首次注册 → 重启提升”窗口，不得把 `8080` 端口转发或暴露给其他用户；如果注册意外提示该邮箱已存在，或 app 日志显示匹配了非预期用户，应立即停止，不要执行重启提升，先审计数据库或重建尚未投入使用的新卷。

以下示例在前文进入的 Bash 会话中隐藏读取密码，并在请求后清理临时变量；密码只作为单次 Python 子进程的环境传入，不导出给 curl 或后续子进程。仍不要在共享终端、录屏、issue 或日志中执行和展示：

```bash
printf 'Owner username: '
IFS= read -r OWNER_USERNAME
printf 'Owner email: '
IFS= read -r OWNER_EMAIL
if [[ "$OWNER_EMAIL" != "${WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL:-}" ]]; then
  printf '注册邮箱必须与 WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL 完全一致\n' >&2
  exit 1
fi
printf 'Owner password: '
IFS= read -r -s OWNER_PASSWORD
printf '\n'

OWNER_USERNAME="$OWNER_USERNAME" OWNER_EMAIL="$OWNER_EMAIL" OWNER_PASSWORD="$OWNER_PASSWORD" \
  python3 -c 'import json, os; print(json.dumps({"username": os.environ["OWNER_USERNAME"], "email": os.environ["OWNER_EMAIL"], "password": os.environ["OWNER_PASSWORD"]}))' | \
  curl -fsS -X POST http://127.0.0.1:8080/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  --data-binary @-
```

首次注册创建 Tenant 后重启 app。重启会同时重新加载 `builtin_models.yaml`，并且只在数据库尚无任何 system admin 时提升与配置邮箱匹配的用户：

```bash
./manage.sh reload-model
wait_http http://127.0.0.1:8080/health
```

服务就绪后重新登录，取得带当前权限状态的 Owner JWT：

```bash

OWNER_LOGIN_RESPONSE="$(
  OWNER_EMAIL="$OWNER_EMAIL" OWNER_PASSWORD="$OWNER_PASSWORD" \
    python3 -c 'import json, os; print(json.dumps({"email": os.environ["OWNER_EMAIL"], "password": os.environ["OWNER_PASSWORD"]}))' | \
    curl -fsS -X POST http://127.0.0.1:8080/api/v1/auth/login \
      -H 'Content-Type: application/json' \
      --data-binary @-
)"
printf '%s\n' "$OWNER_LOGIN_RESPONSE" | jq '{success, system_admin: .user.is_system_admin, active_tenant}'
if ! printf '%s\n' "$OWNER_LOGIN_RESPONSE" | jq -e --arg tenant "$WEKNORA_TENANT_ID" \
  '.success == true and .user.is_system_admin == true and (.active_tenant.id | tostring) == $tenant' >/dev/null; then
  unset OWNER_USERNAME OWNER_EMAIL OWNER_PASSWORD OWNER_LOGIN_RESPONSE
  printf 'Owner 权限或 active Tenant 不符合配置；停止发布并检查 app 日志\n' >&2
  exit 1
fi
export WEKNORA_OWNER_TOKEN="$(printf '%s\n' "$OWNER_LOGIN_RESPONSE" | jq -er '.token')"
unset OWNER_USERNAME OWNER_EMAIL OWNER_PASSWORD OWNER_LOGIN_RESPONSE
```

登录响应中的 `.token` 是后续发布器需要的 Owner JWT，`.user.is_system_admin` 必须为 `true`，`.active_tenant.id` 必须与 `.env` 的 `WEKNORA_TENANT_ID` 一致。全新 SQLite 卷的首个 Tenant 应为 `1`；任一项不符都应停止，先核对 app 日志、邮箱和 Tenant 配置，不能只为通过检查而改动已有发布的 Tenant ID。

完成首个 system admin 引导后，建议把 `.env` 的 `DISABLE_REGISTRATION` 改为 `true`。编辑后重新导出配置并重建 app：

```bash
set -a
. ./.env
set +a
./manage.sh stop
./manage.sh infra
wait_http http://127.0.0.1:8080/health
```

环境文件改动不能靠 `restart` 注入已有容器。`WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL` 可继续保留：只要数据库已经存在 system admin，它不会再提升其他用户。

### 6. 配置模型、发布并做真实合同检查

```bash
./manage.sh bootstrap
# 再次确认紧接着要创建的资源
./manage.sh plan
./manage.sh publish
./manage.sh check
```

- `bootstrap` 校验唯一 KnowledgeQA 模型，隐藏提示读取模型 API Key，执行一次真实模型调用，并创建 signed-token principal。成功后写入 `.secrets/bootstrap.json`。
- `publish` 创建新的 Wiki-only KB、51 个页面、纯 Wiki Agent 和 90 天 chat-only API Key，随后写入 `.secrets/runtime.env` 与 `release-state.json`。
- `check` 不只是检查状态：它会逐页核对 hash、KB/Agent 合同，并真实执行 `wiki_search -> wiki_read_page` 流式问答和引用检查。模型网关不可用、工具未调用或引用异常都会阻断。

`publish` 会创建新资源。网络中断后不要不加判断地反复执行；先检查输出、`release-state.json` 和上游资源，避免遗留重复 KB、Agent 或 key。

### 7. 启动 BFF 和公开网页

```bash
./manage.sh start
wait_http http://127.0.0.1:8080/health
wait_http http://127.0.0.1:8090/healthz
./manage.sh status
```

`start` 要求 `.secrets/runtime.env` 已存在，并构建/启动完整 Compose 服务。默认访问地址：

- 公开问答网页：`http://127.0.0.1:8090`
- 公开健康检查：`http://127.0.0.1:8090/healthz`
- 内部 WeKnora 健康检查：`http://127.0.0.1:8080/health`

验证时至少执行：

```bash
curl -fsS http://127.0.0.1:8080/health
curl -fsS http://127.0.0.1:8090/healthz
curl -fsS -o /dev/null -w 'web=%{http_code}\n' http://127.0.0.1:8090/
```

`/healthz` 成功只证明 BFF 数据库与固定 manifest 可读；完整交付仍需在网页发起真实问题并检查回答、引用、未审核标识和浏览器控制台。

完成发布操作后可从当前 shell 清除 Owner JWT：

```bash
unset WEKNORA_OWNER_TOKEN
```

## `manage.sh` 命令参考

| 命令 | 作用与注意事项 |
| --- | --- |
| `./manage.sh init` | 首次复制 `.env.example`、设置权限、创建 `.secrets/`，并在缺失时自动克隆和验证锁定的 WeKnora；不生成密钥、不校验业务配置 |
| `./manage.sh manifest [--corpus PATH]` | 从固定 corpus 生成并校验 `public-manifest.json`；授权漂移时失败 |
| `./manage.sh plan` | 只读显示本次发布计划，不访问远端 |
| `./manage.sh test` | 运行 Python、补丁、Go race、Web 测试与构建 |
| `./manage.sh infra` | 只启动 `docreader` 和 WeKnora `app` |
| `./manage.sh reload-model` | 重启 WeKnora app 以重新加载内置模型；会中断活动回答，不会重建容器环境 |
| `./manage.sh bootstrap` | 校验模型、录入模型 API Key、真实调用模型并生成 bootstrap 状态 |
| `./manage.sh publish` | 创建新的 KB、Wiki 页面、Agent 和 chat-only key，并生成 BFF runtime env |
| `./manage.sh check` | 核对远端合同并执行真实 Agent 工具链问答 |
| `./manage.sh start` | 在 `runtime.env` 存在后启动完整服务 |
| `./manage.sh stop` | 执行 Compose `down`；默认保留全部命名卷，包括两个 SQLite 主库卷 |
| `./manage.sh status` | 显示 Compose 服务状态 |
| `./manage.sh logs [service1] [service2]` | 持续跟随最近 200 行日志；默认是 `public-bff` 和 `public-web` |
| `./manage.sh config` | 输出合并后的 Compose 配置；其中可能展开敏感环境值，不要粘贴到公开日志或 issue |

## 本机无 Docker 开发

本机调试仍使用同一份固定 manifest、publisher 和 SQLite 补丁，但三个进程需要分别启动。每个新终端都先进入本集成目录并运行 `bash`，避免 macOS 默认 zsh 对交互式 `read` 参数作不同解释。先在终端 1 初始化、编辑并导出 `.env`：

```bash
./manage.sh init
set -a
. ./.env
set +a
mkdir -p .runtime/files .runtime/native-state
```

### 终端 1：WeKnora SQLite 运行时

容器路径和地址不能直接用于本机进程，因此覆盖为宿主值：

```bash
export SERVER_HOST=127.0.0.1
export SERVER_PORT=8080
export DB_PATH="$PWD/.runtime/weknora.db"
export LOCAL_STORAGE_BASE_DIR="$PWD/.runtime/files"
export WEKNORA_NATIVE_STATE_DIR="$PWD/.runtime/native-state"
export BUILTIN_MODELS_CONFIG="$PWD/builtin_models.yaml"
export LLM_BASE_URL=http://127.0.0.1:50288/v1
./run-native-weknora.sh
```

如果模型网关不在本机 `50288`，相应修改 `LLM_BASE_URL`。该脚本使用 Go overlay 编译补丁文件，不改 `services/WeKnora`。

只准备 overlay 而不启动服务时可执行：

```bash
WEKNORA_NATIVE_STATE_DIR="$PWD/.runtime/native-state" ./run-native-weknora.sh --prepare
```

首次创建 Tenant 后，本机模式需要停止并重新运行该脚本，等价于 Docker 的 `reload-model`。

### 首次本机发布

WeKnora 运行后，按 Docker 流程中的回环 API 示例注册首个 Owner。确认 `.env` 已预先设置匹配的 `WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL`，然后停止并重新运行终端 1 的脚本；待 `/health` 就绪后重新登录，确认 `.user.is_system_admin=true` 和 Tenant ID，再取得新的 Owner JWT。不要复用提升前签发的 token。

然后在另一个位于本集成目录的私有 Bash 终端执行：

```bash
set -a
. ./.env
set +a
export LLM_BASE_URL=http://127.0.0.1:50288/v1
for attempt in {1..60}; do
  if curl -fsS http://127.0.0.1:8080/health >/dev/null; then
    break
  fi
  if (( attempt == 60 )); then
    printf 'WeKnora 在 120 秒内未就绪\n' >&2
    exit 1
  fi
  sleep 2
done
printf 'WeKnora Owner JWT: '
IFS= read -r -s WEKNORA_OWNER_TOKEN
printf '\n'
export WEKNORA_OWNER_TOKEN
./manage.sh bootstrap
./manage.sh plan
./manage.sh publish
./manage.sh check
unset WEKNORA_OWNER_TOKEN
```

这里的 `LLM_BASE_URL` 必须与终端 1 启动 WeKnora 时使用的值完全一致；否则 `bootstrap` 会因内置模型参数不一致而拒绝继续。当前固定授权仍有漂移时，必须先处理前述人工审核门禁，不能借本机模式绕过。

### 终端 2：BFF

必须已经完成 `bootstrap/publish`，并同时导出 `.env` 与 `runtime.env`：

```bash
set -a
. ./.env
. ./.secrets/runtime.env
set +a
export PUBLIC_LISTEN_ADDR=127.0.0.1:8091
export PUBLIC_ORIGIN=http://127.0.0.1:5173
export PUBLIC_DB_PATH="$PWD/.runtime/public-bff.db"
export PUBLIC_MANIFEST_PATH="$PWD/authorization/public-manifest.json"
export PUBLIC_TRUSTED_PROXY_CIDRS=
export WEKNORA_BASE_URL=http://127.0.0.1:8080
go -C bff run .
```

### 终端 3：网页

```bash
cd web
npm ci
npm run dev -- --port 5173 --strictPort
```

本机开发地址：

- 网页：`http://127.0.0.1:5173`
- BFF：`http://127.0.0.1:8091/healthz`
- WeKnora：`http://127.0.0.1:8080/health`

Vite 只把 `/qa` 和 `/healthz` 代理到 `127.0.0.1:8091`。这里固定并严格占用 `5173`，因为 BFF 会严格匹配 `PUBLIC_ORIGIN=http://127.0.0.1:5173`；端口被占用时应先释放，或同时修改两处，不能让 Vite 自动换端口。模型和运行密钥只注入后端进程，不进入浏览器 bundle。

## 常见问题

### `WeKnora source missing or incomplete`

部署机没有主仓库刻意忽略的 `services/WeKnora`。在本集成目录重新执行 `./manage.sh init`，脚本会在路径完全不存在时自动克隆并验证锁定版本。如果该路径已经存在但不完整、提交不匹配或含本地改动，脚本会拒绝覆盖；应先审计和保存现有内容，再决定如何恢复干净的锁定工作树。

### `缺少环境变量 LLM_MODEL_NAME`

发布器没有自动读取 `.env`。确认该变量已填写，然后在当前终端重新执行：

```bash
set -a
. ./.env
set +a
```

### `未找到唯一 builtin-llm-wiki-chat`

通常是首个 Tenant 尚未创建，或创建后 app 还没有重新读取 `builtin_models.yaml`。核对 `WEKNORA_TENANT_ID` 后执行 `./manage.sh reload-model`；如果改过 `.env`，需重建而不只是 restart。

### `bootstrap` 写入模型凭据时返回 `403`

当前 token 对应的用户不是 system admin。核对 `WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL`、注册邮箱和 app 启动日志，重启并等待 `/health` 后重新登录；只有登录响应明确显示 `.user.is_system_admin=true` 且 Tenant ID 匹配时才能继续。不要用普通 Tenant Owner token 重试或放宽模型权限。

### `语料 SHA-256 漂移`

这是固定授权门禁在正常工作。先把候选语料写到 `/tmp` 审查；只有发布负责人明确批准新的 SHA、页数和审核范围后，才能更新固定授权。不要直接改期望值来消除错误。

### `run bootstrap and publish first`

`start` 没找到 `.secrets/runtime.env`。依次完成 `bootstrap`、`publish` 和 `check`，不要手工伪造运行凭据。

### 模型网关从容器内不可达

`LLM_BASE_URL=http://127.0.0.1:...` 在容器内指向 WeKnora 容器自身。宿主上的兼容网关通常使用 `http://host.docker.internal:<port>/v1`；远程网关则使用其可从容器访问的 HTTPS 地址。

### 健康检查路径返回 404

- WeKnora 使用 `/health`。
- BFF 使用 `/healthz`。
- Docker 模式通过网页端口访问 `http://127.0.0.1:8090/healthz`；BFF 的 `8091` 没有直接发布到宿主。

### 端口占用

在首次创建容器前修改 `APP_PORT` 或 `PUBLIC_PORT`。修改 `APP_PORT` 时同步修改 `WEKNORA_ADMIN_BASE_URL`；修改 `PUBLIC_PORT` 时同步修改 `PUBLIC_ORIGIN`。每次编辑 `.env` 后都要重新 source；已有容器需要重建才能应用新环境。

## SQLite 数据、备份与限制

- BFF 与 WeKnora 都只允许单副本。已完成的消息和匿名状态可跨重启恢复。
- WeKnora 活动流管理器位于内存；重启 app 会中断当时正在生成的回答。
- `./manage.sh stop` 不删除命名卷；不要使用 `down -v` 或删除卷，除非已明确确认不再需要数据。
- 数据库备份至少同时覆盖两个 SQLite 卷；这只是数据库备份，不是完整灾备。还应盘点上游的 `data-files` 等运行卷。
- 若要原样恢复公开服务，还要加密备份 `.env`、`.secrets/bootstrap.json`、`.secrets/runtime.env`、`release-state.json`，并记录仓库 commit/固定授权版本。至少要保住 `JWT_SECRET`、`SYSTEM_AES_KEY`、`PUBLIC_COOKIE_SECRET`；缺失 AES key 无法解密模型凭据，缺失 Cookie key 会改变 visitor ID，缺失 runtime/release 状态则不能原样接回当前发布资源。
- 为获得一致备份，应先停止写入或使用 SQLite 在线备份机制，不要在持续写入时只复制其中一个数据库文件。恢复材料包含明文运行凭据，必须限制访问并单独加密保存。
- 旧 PostgreSQL/Redis volume 不会由本项目自动删除，便于需要时人工恢复。
