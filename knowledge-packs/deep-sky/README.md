# deep-sky 知识包

`deep-sky` 是 StarunWiki 默认外挂知识包，面向深空摄影爱好者与 AI Agent。知识正文、适用边界、来源和审核状态均保存在 Git 管理的 Markdown 中；请从[知识包索引](index.md)开始阅读。

> [!IMPORTANT]
> M0 迁移基线保留 51/51 个待人工审核正式页面，候选语料 SHA-256 为 `4585dab44a298c1a6afe3501b58f3f9d19549aedf34bd920995ea522a1f22405`。生成候选不等于发布授权；在新 Git 提交与明确批准生成前，现有 active release 继续指向 `public-de219d707e39`。

## 目录

| 路径 | 用途 |
| --- | --- |
| [`pack.yaml`](pack.yaml) | pack ID、语言、分类、排除目录、profile 与 active release 合同 |
| [`index.md`](index.md) | 知识包入口和场景导航 |
| [`00-知识库规范/`](00-知识库规范/) | 来源、审核、生命周期和权威问答门禁 |
| `01-新人入门/` 至 `09-踩坑与复盘/` | 51 个正式候选页面 |
| [`raw/`](raw/) | 调研输入、来源台账和受限捕获说明，不进入公开语料 |
| [`archive/`](archive/) | 已弃用页面和历史材料，不进入公开语料 |
| [`profile/`](profile/) | 领域身份、公开 UI 和烟测问题；不能覆盖平台安全策略 |
| [`releases/`](releases/) | Git 版本化批准快照和 active 指针 |
| [`tools/`](tools/) | 采集、一次性 upgrade 与领域审计工具 |

知识内容直接位于 pack 根，因此逻辑 `entry_name` 仍为 `00-知识库规范/页面.md` 等路径，不包含 `knowledge-packs/deep-sky/` 物理前缀。

## 阅读与维护

Markdown 阅读不需要安装依赖。采用器材参数、软件行为或安全建议前，应检查页面的 `stale_after`、`review.state`、`applies_to`、正文引用和 `verified.scope`。

维护页面时：

1. 保持 YAML frontmatter 可解析。
2. 更新 `updated`、`stale_after`、`applies_to`、`sources` 和维护记录。
3. 数值、型号能力、软件行为和安全建议使用同页脚注就近引用。
4. 优先使用官方文档、标准、原始研究或厂商规格。
5. 在 [`log.md`](log.md) 记录实质变更。
6. 从仓库根运行 pack 校验和相关领域审计。

## 校验候选

以下命令均从仓库根目录执行：

```bash
./manage.sh pack validate deep-sky
./manage.sh pack build deep-sky --output /tmp/deep-sky-candidate.jsonl
```

从 `v0.3.0` 起不再提供旧 catalog wrapper；候选校验和构建统一使用仓库根目录的 `./manage.sh pack` 命令。

领域权威资格审计位于 pack 内：

```bash
python3 knowledge-packs/deep-sky/tools/audit_authority_readiness.py \
  --machine-gate \
  --summary-only
```

外部来源检查需要联网，应与核心离线合同分开运行：

```bash
python3 knowledge-packs/deep-sky/tools/check_source_links.py \
  --only-cited \
  --output /tmp/starunwiki-cited-sources.json
```

HTTP 403、TLS 或超时只表示自动检查未能读取，不等于来源内容错误或页面已删除；人工签署前仍需在浏览器核对来源、版本和证据位置。

## 人工签署

自动构建、模型检查、链接可访问和 `status: stable` 都不能替代领域审核。审核人核对具体主张后，才可在对应页面 frontmatter 中加入：

```yaml
verified:
  by: human:<审核人ID>
  at: <ISO-8601时间>
  scope: <具体且可审计的已核验主张范围>
```

`scope` 不应写成“全文正确”。部分内容通过时，只签署已核验范围并保留其余待办。部分审核继续使用 `review.state: needs-human-review`；页面约定范围全部完成审核后使用 `review.state: human-reviewed`，且必须同时存在有效的 `verified` 记录。详细流程见[人工审核与证据补齐队列](00-知识库规范/人工审核与证据补齐队列.md)和[知识库维护规范](00-知识库规范/知识库维护规范.md)。

## 发布边界

- Markdown/Git 是内容真相源；外部问答系统不得回写。
- `status` 表示生命周期，不代表人工核验。
- `raw`、`archive`、`releases`、`tools` 和 `profile` 不进入知识正文语料。
- 核心工具白名单、禁止泄密、不回写和 fail-closed 规则位于平台内核，知识包 profile 无权覆盖。
- 生成候选不等于发布授权，不得自动更新 active release、授权 SHA 或页面 `verified`。

当前 active release 是 `public-de219d707e39`，模式为 `legacy-manifest-only`。它保留旧发布可验证信息，但没有 corpus，不能重新发布。新候选必须获得明确批准后，才能按[运行与迁移说明](../../docs/operations.md)进入 M1。
