# StarunWiki

StarunWiki 是一个以 Wiki 网页 AI 问答为核心的只读知识平台。应用运行代码、发布合同和部署入口位于仓库根；具体知识内容以可替换的版本化知识包接入。

默认知识包是 [`deep-sky`](knowledge-packs/deep-sky/README.md)，Markdown/Git 仍是其内容真相源。应用只消费经过批准的 release，不会让 WeKnora 或浏览器回写知识文件。

## 产品结构

```text
knowledge pack -> approved release -> WeKnora Wiki-only -> BFF -> Web
```

- `apps/web`：公开问答网页。
- `apps/bff`：匿名会话、限流、引用白名单和 SSE 网关。
- `src/starunwiki`：知识包、语料、release、publisher 与状态管理。
- `deploy`：本地 Compose 和锁定的 WeKnora 补丁。
- `knowledge-packs/deep-sky`：默认外挂知识包。
- `.runtime`：新运行状态根，完整 Git 忽略。

## 正式入口

```bash
./manage.sh pack validate deep-sky
./manage.sh pack build deep-sky --output /tmp/deep-sky-corpus.jsonl
./manage.sh release verify --pack deep-sky --release current
./manage.sh bootstrap check --pack deep-sky
./manage.sh runtime status
./manage.sh test
```

`v0.2.x` 暂时保留 `integrations/llm-wiki-public` 旧入口并输出弃用提示；它计划在 `v0.3.0` 删除。

## M0 发布边界

当前 active release 仍是 `public-de219d707e39`，它被登记为 `legacy-manifest-only`：旧 corpus 已不可用，不能据此重建或重新发布。当前 Markdown 候选为 51 页且仍未获得新发布授权；目录迁移不得更新其授权 SHA、运行凭据或远端 KB/Agent。

运行和发布说明见 [`docs/operations.md`](docs/operations.md)。
