# StarunWiki

面向深空摄影爱好者与 AI Agent 的 Markdown 原生知识库。知识正文、适用边界、来源和审核状态均保存在 Git 管理的 Markdown 中；请从[知识库索引](index.md)开始阅读。

> [!IMPORTANT] 当前权威状态
> 截至 2026-08-27，51/51 个正式页面已通过机器可完成的结构、时效、适用范围、主张引用和一手来源门禁，但仍有 51/51 页等待真人逐页签署。没有有效 `verified` 的页面只能作为“非权威参考”，不能对外宣称已经完成人工核验。

## 项目内容

| 路径 | 用途 |
|---|---|
| [`index.md`](index.md) | 知识库总入口和场景导航 |
| [`00-知识库规范/`](00-知识库规范/) | 来源、审核、生命周期和权威问答门禁 |
| `01-新人入门/` 至 `09-踩坑与复盘/` | 51 个正式知识页面，属于问答候选语料 |
| [`raw/`](raw/) | 调研输入、来源台账和受限捕获说明，不直接作为公开问答正文 |
| [`archive/`](archive/) | 已弃用页面和历史材料 |
| [`scripts/`](scripts/) | 构建、权威资格审计、来源检查和可复现迁移脚本 |
| `integrations/llm-wiki-public/` | 受 Git 管理的只读问答派生服务代码；运行态与密钥被忽略，不是知识真相源 |

仓库提交可公开维护的知识页、治理规则、变更日志、来源台账、生成脚本和公开问答集成代码。本地问答运行态与密钥、第三方 WeKnora 工作树、生成索引、调研工作区，以及受限的原始网站文本捕获均不提交。

## 快速开始

### 只阅读知识库

阅读 Markdown 不需要安装依赖：

1. 打开 [`index.md`](index.md)。
2. 按“新人入门、器材、拍摄 SOP、后期、目标、环境、软件、FAQ、复盘”进入分类。
3. 使用页面末尾的“关联知识”继续浏览。
4. 在采用器材参数、软件行为或安全建议前，检查页面的 `stale_after`、`review.state`、`applies_to`、正文引用和 `verified.scope`。

本项目兼容普通 Markdown/Git 浏览，也可作为 Obsidian vault 打开。为兼容 GitHub 和静态站，仓库内链接主要使用标准 Markdown 格式。

### 准备维护环境

维护和审计脚本需要 Python 3.10+ 与 PyYAML：

```bash
python3 --version
python3 -c 'import yaml; print(yaml.__version__)'
```

如果缺少 PyYAML，可安装到当前 Python 环境：

```bash
python3 -m pip install PyYAML
```

以下命令均从仓库根目录执行。

## 验证知识库

### 1. 验证 51 个正式页面并预览候选语料摘要

```bash
python3 -B scripts/build_knowledge_catalog.py
```

该命令只解析页面并输出页数、待审核数量和确定性 SHA-256，不写文件。

### 2. 检查机器可修复的权威化缺口

```bash
python3 -B scripts/audit_authority_readiness.py \
  --machine-gate \
  --summary-only
```

退出码解释：

- `0`：没有机器可修复的阻断；报告仍会保留真人签署待办。
- `2`：存在缺失适用边界、过期页面、引用错误、来源字段不全或高影响页面缺少一手来源等阻断。

查看逐页详情：

```bash
python3 -B scripts/audit_authority_readiness.py --only-blocked
```

不带 `--machine-gate` 时，只有所有页面都具备有效真人签署，命令才会返回 `0`。

### 3. 检查正文实际引用的外部来源

该步骤需要联网：

```bash
python3 -B scripts/check_source_links.py \
  --only-cited \
  --output /tmp/starunwiki-cited-sources.json
```

HTTP `403`、TLS 或超时只表示自动检查未能读取，不等于来源内容错误或网页已经删除；签署前仍需由审核人在浏览器中核对来源、版本和证据位置。

### 4. 检查补丁格式

```bash
git diff --check
```

## 生成检索语料

建议先把候选语料写到临时目录，避免触碰固定公开快照：

```bash
python3 -B scripts/build_knowledge_catalog.py \
  --write \
  --output /tmp/starunwiki-candidate.jsonl
```

需要维护本地默认生成物时：

```bash
python3 -B scripts/build_knowledge_catalog.py --write
python3 -B scripts/build_knowledge_catalog.py --check
```

默认输出为 `.knowledge-catalog/retrieval-corpus.jsonl`，该目录被 Git 忽略。`--check` 发现漂移时会返回错误；这通常意味着 Markdown 已更新而本地生成语料仍是旧版本。

公开问答使用固定授权快照。不要因为生成了新语料就自动修改授权 SHA、发布清单或人工审核状态。

## 完成人工权威签署

自动构建、模型检查、链接可访问和 `status: stable` 都不能替代领域审核。审核人逐项核对正文主张、来源、适用条件、版本和实测证据后，才能在对应页面 frontmatter 中加入：

```yaml
verified:
  by: human:<审核人ID>
  at: <ISO-8601时间>
  scope: <具体且可审计的已核验主张范围>
```

`scope` 不应写成“全文正确”或“全部已验证”。部分内容通过时，应只签署已核验范围，并继续保留其余待办。详细流程见[人工审核与证据补齐队列](00-知识库规范/人工审核与证据补齐队列.md)和[知识库维护规范](00-知识库规范/知识库维护规范.md)。

签署后重新执行：

```bash
python3 -B scripts/build_knowledge_catalog.py
python3 -B scripts/audit_authority_readiness.py
```

## 运行本地 AI 问答

问答产品是 Markdown/Git 的只读派生层，数据流为：

```text
正式 Markdown 页面
  → 确定性 retrieval-corpus.jsonl
  → 固定授权发布器
  → WeKnora Wiki-only 知识库
  → 匿名 BFF
  → 浏览器问答页面
```

`integrations/llm-wiki-public/` 的集成代码受 Git 管理；其运行态、密钥以及 `services/` 下的第三方 WeKnora 工作树不提交。开始前先阅读其 [`README.md`](integrations/llm-wiki-public/README.md)。Docker Compose 备用流程为：

> [!WARNING] 发布前置条件
> 当前 51 页仍未获得真人签署，新 Markdown 与既有固定授权语料之间也存在预期漂移。在完成审核、重新生成语料并由发布负责人明确更新固定授权前，`manifest` 或 `publish` 应当失败；不要通过跳过 SHA、页数或审核门禁来强行发布。

```bash
cd integrations/llm-wiki-public
./manage.sh init
# 编辑 .env，替换所有 CHANGE_ME，设置模型地址/模型名，并填写首个 system admin 邮箱
set -a
. ./.env
set +a
./manage.sh manifest
npm --prefix web ci
./manage.sh test
./manage.sh infra
# 按集成 README 注册首个 Owner；注册邮箱必须匹配 .env 的 system admin 引导邮箱
./manage.sh reload-model
# 等待 /health 就绪，再重新登录，确认 system admin/Tenant ID 后导出新的 Owner JWT
./manage.sh bootstrap
./manage.sh publish
./manage.sh check
./manage.sh start
```

常用运维命令：

```bash
./manage.sh status
./manage.sh logs
./manage.sh stop
```

本地问答仅绑定 `127.0.0.1`。浏览器不得接触模型 API Key、WeKnora API Key、JWT、Agent ID、KB ID 或模型 ID；问答系统也不得反向写回 Markdown。

## 修改知识页面

维护单个正式页面时：

1. 保持 YAML frontmatter 可解析。
2. 更新 `updated`、`stale_after`、`applies_to`、`sources` 和必要的维护记录。
3. 数值、型号能力、软件行为和安全建议使用同页 `[^source-id]` 就近引用。
4. 优先使用官方文档、标准、原始研究或厂商规格；经验来源不能单独支撑高影响结论。
5. 在 [`log.md`](log.md) 记录面向知识库用户的实质变更。
6. 依次运行构建、权威资格审计、来源检查和 `git diff --check`。

`scripts/upgrade_*.py` 是本轮全库迁移的可复现工具，不是普通阅读或每次维护都必须执行的初始化命令。

## 信任与发布边界

- 当前 Markdown/Git 是唯一真相源，外部问答系统不得回写。
- `status` 只表示生命周期，不代表人工核验。
- 到达 `stale_after` 后必须复核，不能继续自动视为有效。
- 公开可访问不等于允许镜像、转载、训练或嵌入；来源权利状态必须单独判断。
- `raw/`、`archive/`、`integrations/` 和 `services/` 不进入正式公开问答语料。
- 混入未审核、过期或超出 `verified.scope` 的关键依据时，整条回答必须降级为“非权威参考”或拒答。

完整规则见[来源许可与发布门禁](00-知识库规范/来源许可与发布门禁.md)。
