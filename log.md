# 知识库更新日志

## 2026-08-27

* **Authority-readiness baseline**: 为全部 51 个正式页面补齐逐页 `applies_to`，明确系统、使用条件和不适用边界；构建器开始强制检查这些字段，并要求任何 `verified` 都必须包含 `human:` Actor、时间和可审计范围。
* **Authoritative-answer fail-closed gate**: 明确只有未过期且处于 `verified.scope` 内的人工签署内容才能承担权威回答；混入未审核、过期或越界依据时，整条回答必须降级为“非权威参考”或拒答。当前 51 页仍待真人逐项签署，未伪造完成状态。
* **Machine authority-readiness complete**: 51/51 正式页面已通过适用边界、时效、来源字段、主张引用和高影响页面一手来源门禁；自动审计的机器阻断归零，剩余 51 项全部为不可自动代签的真人 `verified` 审核。
* **Primary-source answer claims**: 为采购、器材、现场安全、采集、目标、天气、软件与排障等高影响页面增加就近的一手来源问答口径；把不可访问的二手案例引用从权威结论中移出，并以 NASA、ESO、NWS、SIMBAD 及软件/厂商官方文档承担相应事实边界。
* **Starun market-specification capture**: 新增[Starun 智能望远镜参数规格表来源记录](/raw/2026-08-27-Starun智能望远镜规格表来源记录.md)与受限浏览器渲染文本捕获：覆盖 DWARF 3、DWARF mini、Seestar S30、S30 Pro、S50、S50 Pro 共 6 款机型、63 条实际参数记录；保留 URL、HTTP `Last-Modified`、抓取时间与正文哈希，不保存媒体，也不将第三方汇编表伪装为厂商原始规格。
* **Starun current-source revision**: 页面同日更新后，新增 `2026-08-27T03:23:18Z` 的受限 raw 快照（HTML SHA-256 `4a7c89e9…fd022b67`），并保留先前快照而不做静默覆盖；公开核验页改指向当前快照，仍不公开复刻整张规格表。
* **Specification-reference boundary**: 新增[常见智能望远镜参数规格对照与使用边界](/02-器材百科/常见智能望远镜参数规格对照与使用边界.md)，用于型号识别和核验分流；价格、App/固件、导出、未公开、猜测与几何换算字段仍须回到厂商当前资料复核，页面维持 `needs-human-review`。
* **Official-source raw capture correction**: 按“原始知识先入库”补建本地受限文本捕获：DWARF 71 篇、Seestar 46 篇（其中 40 篇为明确标记的 OCR 文本）、Siril 1 篇、PixInsight 1 篇，共 119 个带 URL、产品范围、抓取时间、采集状态和哈希的受限文本页，约 14.1 万字符；公共仓库的发布边界见[受限原始文本捕获说明](/raw/受限原始文本捕获说明.md)。
* **Public boundary retained**: 捕获页全部标记 `rights: unknown`、`access: restricted`、`content_withheld: true`；不保存官网图片、视频、PDF 或附件，公开目录、公开搜索和 LLM Wiki 仍只发布派生知识与官方链接。
* **Governance**: 更新来源许可门禁和维护规范，明确用户纳入维护范围的官网资料先进入受限 raw 文本捕获层，再由正式知识页综合改写；OCR 文本必须保留采集状态并待人工回看官网画面。

## 2026-08-26

* **Official smart-telescope catalog**: 新增[智能望远镜与深空后期官方资料台账](/raw/2026-08-26-智能望远镜与深空后期官方资料台账.md)，登记 DWARF mini / DWARF 3 去重后的 71 篇官方目录、Seestar 去重后的 46 篇教程，以及 Siril Seestar 与 PixInsight FAQ 导航；当日只建立目录台账，2026-08-27 已补建受限正文文本捕获；始终不镜像官网图片、视频或 PDF。
* **Product and handoff boundary**: 新增智能望远镜的产品/文件边界与首拍/数据交接页面，按型号、App/固件、文件状态和可回退副本分流，全部维持 stable + needs-human-review。
* **Desktop processing boundary**: 新增 Seestar 专用 Siril 分流页，明确厂商成图、已堆栈结果和多张 Lights 的不同处理入口，不把通用校准步骤错误套用于专用数据。
* **PixInsight navigation**: 新增 PixInsight FAQ 导航页，并在软件对比中补入安装、许可、平台、文档与支持入口；FAQ 不作为官方处理配方。

## 2026-08-13

* **P0 trust model**: 新增[主张级引用与适用条件](/00-知识库规范/主张级引用与适用条件.md)，并将 `applies_to`、就近来源、版本/访问日期、验收产物和冲突处理写入维护流程。
* **P0 platform lifecycle**: 新增[采集控制平台选择与迁移](/07-软件工具/采集控制平台与迁移.md)，区分协议互通、设备支持与安全自动化，并提供版本快照、干跑与回滚卡。
* **P0 parameter method**: 新增[采集参数试拍与总积分](/03-拍摄SOP/采集参数试拍与总积分.md)，以传感器模式、实测背景、主相机表现、开销和合格积分替代跨设备固定参数表。
* **P0 recovery loop**: 新增[板解、翻转与任务恢复](/03-拍摄SOP/板解、翻转与任务恢复.md)，将翻转、重新居中、对焦、导星、验收和人工接管组织为在场可演练的状态机。
* **P0 evidence baseline**: 新增本轮原始来源记录，并将四项新页面列入人工审核与证据补齐队列；未自动添加任何 `human:` 核验标记。

## 2026-08-01

* **Compliance remediation**: 修复 raw 来源在网站中的断链，以只含元数据和外链的追溯页替代 raw 正文发布。
* **Trust semantics**: 采用 YAML 解析 verified Actor；只有 `human:` 核验可显示为“人工已核验”，机器校验单独标识。
* **Content safety**: 移除供电、线径、极轴、相机温度、采样与滤镜等跨设备固定门槛，改为按厂商规格和实测工况验收。
* **Publication gate**: 新增[来源许可与发布门禁](/00-知识库规范/来源许可与发布门禁.md)，明确版权、隐私和人工审核边界。
* **Quality gate**: 增加可重复的 OKF/断链/发布边界检查，更新渲染测试、lint 修复和依赖安全基线。
* **Evidence optimization**: 修正深空定义、预算路线、望远镜选型、导星、对焦、目标参数、光污染与软件对比中的过强表述；首批 9 页加入 37 个主张级来源引用。
* **Review queue**: 新增[人工审核与证据补齐队列](/00-知识库规范/人工审核与证据补齐队列.md)，保留全部页面的 `needs-human-review`，未自动添加任何人工核验标记。
* **Reproducibility**: 编译清单加入逐页 SHA-256 与 bundle 内容摘要，生成公开来源权利台账；未声明许可的来源统一显示 `unknown`。
* **Search evaluation**: 抽取共享检索引擎，补充法兰距、彗差、暗角、失焦、供电与极轴别名，并建立 30 条中文检索回归基线。

## 2026-07-30

* **Migration**: 按 Open Knowledge Format v0.2 为全部概念页补充类型、描述、来源、生命周期和生成信息。
* **Restructure**: 建立根目录与分类目录索引，采用稳定文件名，并将初始调研材料移动到 [原始资料层](/raw/)。
* **Linking**: 为 Wiki 页面补充“关联知识”，形成可遍历的主题关系。
* **Governance**: 更新[知识库维护规范](/00-知识库规范/知识库维护规范.md)，加入 LLM 读写、审核、冲突与时效规则。
* **Safety corrections**: 修正校准帧匹配、CMOS 暗场、PHD2 导星验收与校准复用、窄带快焦比及月相表述，并补充官方来源。
* **P0 foundation**: 新增采购前系统兼容性、成像光路预算、供电与现场运行安全、数据管理与可复现归档页面；同步更新新手预算、关键附件、现场搭建与自动序列的边界说明。
* **P0 user paths**: 新增场景导航与术语地图、已有设备分流、城市阳台首次深空拍摄、校准帧现场速查卡、Siril 新手首图工作流和单张检查与翻车诊断；同步接入根目录、分类索引与既有主题页。
* **Conformance audit**: 复核 43 个概念页、13 个目录索引和 246 个内部链接；OKF 必填元数据、来源结构、生命周期及索引覆盖均通过检查。

## 2025-07-10

* **Initialization**: 基于公开资料调研建立首批深空天文拍摄知识页面。
