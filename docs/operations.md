# StarunWiki v0.2 运行与迁移说明

StarunWiki 是“Wiki 网页 AI 问答平台 + 可替换知识包”。本目录在 `v0.2.x` 只保留兼容入口；正式 CLI 是仓库根目录的 `./manage.sh`，本目录 wrapper 会输出弃用提示，并在 `v0.3.0` 删除。

默认知识包是 `knowledge-packs/deep-sky`。Markdown/Git 是内容真相源，运行应用只消费已批准、版本化且完整性校验通过的 release。WeKnora、BFF 和浏览器都不得回写知识正文。

## 当前 M0 边界

- active release 固定为 `public-de219d707e39`。
- 它是 `legacy-manifest-only`：保留旧 manifest、原 payload 顺序与旧 corpus SHA，但没有可用 corpus，不能据此重建或 fresh publish。
- 当前 Markdown 候选仍是 51 页、51 页待人工审核；候选 corpus SHA 是 `4585dab44a298c1a6afe3501b58f3f9d19549aedf34bd920995ea522a1f22405`，不等于旧授权 SHA。
- M0 不执行 `release publish` 或 `bootstrap init`，不创建 KB/Agent/key，不旋转凭据，不修改数据库 schema。
- M0 的 Compose 强制 `AUTO_MIGRATE=false`；原生启动器默认并保持 `false`，即使旧 `.env` 仍保留历史值。原生路径只有在显式提供 `PUBLIC_RELEASE_ID`、且正式 verifier 确认它是 full release 时才接受 `STARUNWIKI_AUTO_MIGRATE=true`。若现有 SQLite schema 不能直接运行，应停止迁移并恢复旧代码，不能在 M0 临时放开迁移。
- 只有新候选获得明确发布授权后，才进入 M1：使用单命令批准生成完整 release，再单独执行发布。

## 目录和状态边界

```text
apps/web                     公开网页
apps/bff                     匿名会话、引用和 SSE 网关
src/starunwiki               pack、release、publisher、state 内核
deploy/compose.yml           WeKnora + BFF + Web Compose 覆盖
deploy/weknora               锁定补丁、模型定义和原生启动器
knowledge-packs/deep-sky     默认知识包与 Git 版本化 release
.runtime                     新运行状态根，完整 Git 忽略
services/WeKnora             锁定的外部依赖工作树，Git 忽略
```

状态选择是“整体选根”，不会逐文件混用：

1. `--state-root` 或 `STARUNWIKI_STATE_ROOT`；
2. 新 `.runtime`；
3. 旧 `integrations/llm-wiki-public` 运行状态。

新旧状态同时非空时通常会 fail-closed。唯一例外是 `state migrate` 写入的完整迁移证明仍能确认“旧副本未漂移、canonical 是权威根”；此时默认选择 canonical。旧状态只允许原地读取并执行 `runtime start/status/logs/config` 与 `release check` 等兼容操作；任何写入密钥或 release state 的动作都要求先显式迁移：

```bash
./manage.sh state doctor
./manage.sh state migrate
./manage.sh state doctor
```

`state migrate` 只复制文件、核对权限与 SHA，不删除旧状态。迁移证明锁定旧副本的文件集合和哈希；旧副本被改动时默认 resolver 会重新 fail-closed，而 canonical 状态可在后续正式命令中正常演进。迁移完成后应保留旧目录直到回滚窗口结束。

迁移前已经存在、且被 Git 忽略的 `raw/official-captures`、`raw/site-captures`、`archive/research` 也原地保留；M0 不移动、删除或提交这些受限材料。新 pack 工具只默认写入 pack-local 的同名忽略目录，不会隐式读取或合并旧捕获。

## 前置依赖

- Python 3.12、`uv`；测试还需要 Go 与 Node.js/npm。
- Docker Engine 与支持 `!reset`、`!override`、`env_file.required` 的 Compose v2。
- 锁定 WeKnora `v0.7.2`，提交 `3d5d8bfcdfeeea266b292b71cea616847af28d0f`。
- Git、Bash、curl、jq、rg；原生 WeKnora 启动还需要 patch、gofmt 等工具。

`services/WeKnora` 必须是上述提交的干净工作树。不要把项目补丁直接写入该工作树；Docker 构建或原生 overlay 会从锁定源码派生运行版本。

## 配置和密钥

`.env.example` 只含配置模板和占位值。实际配置、模型凭据、HMAC principal、Owner JWT、KB/Agent/model ID、SQLite 和 release state 都属于运行状态，必须留在 Git 忽略的状态根中。

全新环境可执行：

```bash
./manage.sh init
```

它准备 `.runtime/config.env` 和锁定依赖，不代表凭据或发布已经可用。至少应替换以下值，并将配置文件权限保持为 `0600`：

```dotenv
JWT_SECRET=<strong-random-value>
SYSTEM_AES_KEY=<exactly-32-bytes>
PUBLIC_COOKIE_SECRET=<strong-random-value>
LLM_BASE_URL=http://host.docker.internal:50288/v1
LLM_MODEL_NAME=<deployed-model-name>
LLM_PROVIDER=openai
PUBLIC_ORIGIN=http://127.0.0.1:8090
```

不要把真实值写入命令行记录、issue、日志或文档。`SYSTEM_AES_KEY` 写入模型凭据后必须长期保存；改变它可能使既有密文不可解密。凭据轮换是独立维护动作，不属于目录迁移或知识发布。

`bootstrap` 已拆分为两个语义明确的命令：

```bash
./manage.sh bootstrap init --pack deep-sky      # 仅全新状态，创建模型凭据和 HMAC principal
./manage.sh bootstrap check --pack deep-sky     # 只读验证，不生成或覆盖 HMAC
```

M0 只允许 `bootstrap check`。旧 wrapper 的 `./integrations/llm-wiki-public/manage.sh bootstrap` 也只转发到只读检查。

## 正式 CLI

```text
./manage.sh pack validate|build|approve
./manage.sh release list|verify|plan|publish|check
./manage.sh runtime infra|start|stop|status|logs|config
./manage.sh bootstrap init|check
./manage.sh state doctor|migrate
./manage.sh test
```

常用只读检查：

```bash
./manage.sh pack validate deep-sky
./manage.sh release list --pack deep-sky
./manage.sh release verify --pack deep-sky --release current
./manage.sh state doctor
```

验证当前候选时，只写临时路径：

```bash
./manage.sh pack build deep-sky --output /tmp/deep-sky-candidate.jsonl
shasum -a 256 /tmp/deep-sky-candidate.jsonl
```

不要因为候选构建成功而更新 active release、授权 SHA 或页面 `verified`。

## M0 启动和检查

部署机先验证锁定源码、状态根和旧 release，然后使用原有命名卷启动：

```bash
./manage.sh release verify --pack deep-sky --release public-de219d707e39
./manage.sh bootstrap check --pack deep-sky --release public-de219d707e39
./manage.sh runtime config --pack deep-sky --release public-de219d707e39
./manage.sh runtime start --pack deep-sky --release public-de219d707e39
./manage.sh runtime status --pack deep-sky --release public-de219d707e39
```

Compose project、容器名、端口和 SQLite 命名卷保持兼容：

- `llm-wiki-public-weknora-sqlite-data`
- `llm-wiki-public-bff-sqlite-data`

就绪条件必须全部满足：

```bash
curl -fsS http://127.0.0.1:8080/health
curl -fsS http://127.0.0.1:8091/healthz
curl -fsS http://127.0.0.1:8091/qa/v1/meta
```

此外应通过网页完成真实 Wiki 问答，确认 SSE 正常、引用只指向 manifest 中页面、页面数和待审核数来自 `/qa/v1/meta`。`/health` 或 `/healthz` 单独成功不代表真实工具链与引用已经交付。

本机当前没有可用 Docker，因此本次迁移只能在本地验证 Compose 文本合同；合并后的 Compose、补丁可应用性和镜像配置由 GitHub Docker runner 验证。CI 当前不执行完整镜像构建，本地或 CI 结果都不得把该项标记为已通过。

## M1 批准与发布

只有发布负责人明确授权当前候选后，才能在“pack 已提交且整个仓库干净”的提交上执行：

```bash
./manage.sh pack approve deep-sky \
  --approved-by operator:<id> \
  --allow-unreviewed \
  --note "<批准说明>"
```

该命令离线、原子地生成 `corpus.jsonl`、`manifest.json`、`authorization.json`、profile 和 `SHA256SUMS`，最后切换 active release；不会联网、调用 WeKnora 或修改页面 `verified`。未审核或 draft 例外必须分别显式授权。

批准命令会产生新的 Git 变更。必须先验证并提交这一不可变批准快照；未提交、工作区不干净、release 不是 active、source commit 不是当前 `HEAD` 的祖先，或 pack 内容树已经漂移时，`bootstrap init` 和 `release publish` 都会在联网前拒绝：

```bash
./manage.sh release verify --pack deep-sky --release current
git add knowledge-packs/deep-sky/releases
git commit -m "release: approve deep-sky <release-id>"
test -z "$(git status --porcelain --untracked-files=all)"
./manage.sh release plan --pack deep-sky --release current
```

全新环境执行一次 `bootstrap init`；已有凭据只执行只读 `bootstrap check`。`bootstrap init` 在每个不可逆步骤前后写入 `0600` 检查点，不会把模型 API Key 写盘；若远端响应与本地检查点含义不一致，会保留 HMAC secret 并 fail-closed，禁止把重试变成凭据轮换。

```bash
./manage.sh bootstrap init --pack deep-sky --release current   # 仅全新环境
# 或：./manage.sh bootstrap check --pack deep-sky --release current
./manage.sh release publish --pack deep-sky --release current
./manage.sh release check --pack deep-sky --release current
```

发布前会把现有 `release-state.json` 与 `runtime.env` 保存到 `.runtime/release-history/<pack>/<old-release>/`，并在 `.runtime/operations/publish-<pack>-<release>/operation.json` 逐步记录 KB、页面、Agent 和 key 状态。一次性 key 在切换前先落入 `0600` 恢复文件，成功切换后删除额外副本。远端操作失败时旧 runtime 不变；本地切换失败时自动从快照恢复。任何未完成 operation 都禁止自动重试，以免重复创建 KB/Agent/key，必须先按记录中的资源 ID 完成人工审计与处置。

重复发布、release 不完整、SHA/计数不一致、绝对路径、未提交批准快照、未迁移 legacy state 或授权边界不满足都会拒绝执行。

## 回滚

M0 目录迁移失败时，不修改旧 KB、Agent、key 或卷：

1. 停止 BFF/Web，保留 WeKnora 与两个 SQLite 命名卷。
2. 恢复旧代码提交和旧状态根。
3. 验证 `public-de219d707e39` manifest 与旧 runtime state。
4. 仅重启 BFF/Web，再检查 `/health`、`/healthz`、真实问答和引用。

M1 切换失败时，先查看 `.runtime/operations/` 的操作记录；运行状态切换异常会自动尝试恢复 `.runtime/release-history/` 中的旧副本。确认旧 `release-state.json`、`runtime.env` 和旧 release manifest 一致后，只重启 BFF/Web。旧 KB、Agent、key 和卷必须保留整个回滚窗口；禁止通过删除卷或重建数据库来“修复”发布失败，也禁止删除未审计的 operation 记录后直接重跑 publish。

## 测试边界

```bash
./manage.sh test
```

本地核心测试覆盖 Python pack/release/state、Go race、Web test/build；CI 另外覆盖 Compose config、WeKnora 补丁、v1 兼容和运行文件防提交。CI 不执行真实 publish，不读取生产密钥，也不替代部署机的真实 Wiki 工具链验证。
