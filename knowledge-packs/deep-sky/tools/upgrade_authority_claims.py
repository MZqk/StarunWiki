#!/usr/bin/env python3
"""Add bounded, primary-source-backed answer claims to high-impact pages.

This migration deliberately does not add ``verified`` records. Human review
signatures remain a separate, fail-closed release gate.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


CLAIMS = {
    "01-新人入门/购买前成像系统设计与兼容性清单.md": """## 权威问答口径

- “能被控制软件发现”不等于整套系统兼容。回答具体兼容性问题时，必须同时核对当前操作系统、设备驱动/协议、控制软件支持范围和实际连接测试；N.I.N.A. 官方要求页只证明其自身的平台与设备支持边界。[^src-nina-requirements][^src-ascom-docs]
- 后截距必须按具体望远镜、改正器或减焦器制造商给出的基准面和目标距离核算，不能把常见的 55 mm 当作跨产品标准。[^src-celestron-back-focus]
""",
    "02-器材百科/关键附件.md": """## 权威问答口径

- 附件是否可用必须落到具体型号的接口、工作距离、承载、供电和驱动版本；厂商目录只能证明其自身产品当期声明的规格，不能证明跨品牌组合必然兼容。[^src-5125778b]
- 导星镜、OAG、滤镜轮和电调的选择应由主镜焦距、成像圈、后截距、机械刚性和自动化目标共同决定，不给出脱离系统清单的“必买附件”。[^src-5125778b]
""",
    "02-器材百科/天文相机选型.md": """## 权威问答口径

- 像元尺寸、传感器面积、读出模式、满阱、制冷能力和接口必须以具体型号的官方手册或产品页为准；相同传感器也不能推导出整机性能完全相同。[^src-e7c0e7cf][^src-dc12dc71][^src-de353674]
- 彩色或单色没有脱离目标光谱、天空背景、滤镜、采集时间和处理流程的绝对优劣；权威回答只能给出选择条件，不能用单一参数替用户下结论。[^src-e7c0e7cf]
""",
    "02-器材百科/成像光路：后截距、像圈与接口预算.md": """## 权威问答口径

- 后截距是从制造商指定基准面到成像面的系统约束；必须按具体光学件说明书计算，并把滤镜、转接环、倾斜板和相机法兰距离计入，不能套用跨产品固定值。[^src-celestron-back-focus]
- 能合焦不等于边角已校正。像圈覆盖、后截距、倾斜、调焦器承载和边角星点需要分别用官方约束与实拍数据验收。[^src-celestron-back-focus]
""",
    "02-器材百科/滤镜系统.md": """## 权威问答口径

- 窄带滤镜可以压低部分连续谱背景并提高特定发射线与背景的对比，但不能“消除光污染”或让目标本身变亮；带宽、中心波长和透过率只对具体产品成立。[^src-zwo-narrowband-filters]
- 滤镜选择必须同时说明目标光谱、相机类型、入射光锥、天空背景和颜色目标；对星系、反射星云等连续谱目标，不能把窄带当作宽带信号的通用替代。[^src-zwo-narrowband-filters]
""",
    "02-器材百科/赤道仪选型.md": """## 权威问答口径

- 标称载荷是具体产品在厂商定义下的规格，不等于某套长曝光系统的保证载荷；选型必须把镜筒力矩、风、三脚架、线缆和实拍星点纳入验收。[^src-97c20712]
- 不使用“摄影载荷一律取标称一半”作为权威结论。任何比例只能是试配起点，最终由完整载荷清单和目标曝光下的跟踪结果决定。[^src-97c20712]
""",
    "03-拍摄SOP/供电、线缆与现场运行安全.md": """## 权威问答口径

- 电源连接必须遵守设备官方允许电压、极性、端口电流和总功率限制，并在最大实际负载与最低预期温度下测量负载端电压；“同为 12 V”不能证明端口可互换。[^src-pegasus-cable][^src-pegasus-powerbox][^src-zwo-faq]
- 听到雷声或收到雷暴威胁时应停止户外作业并进入封闭建筑或硬顶车辆；帐篷、棚架和孤立树木不是安全避雷场所。[^src-nws-lightning]
""",
    "03-拍摄SOP/城市阳台首次深空拍摄.md": """## 权威问答口径

- 首拍计划必须以拍摄地点、日期、地平线遮挡和目标过中天轨迹为条件，用星图软件现场复核；示例目标不能脱离纬度和季节直接照搬。[^src-stellarium]
- 光污染地图只能作为规划层输入，不能替代现场天空背景、局部灯光、透明度和测试帧测量；权威回答应明确地图日期与实际观测可能不一致。[^src-light-pollution-map]
""",
    "03-拍摄SOP/拍摄序列计划.md": """## 权威问答口径

- 自动序列应明确连接、制冷、对焦、板解、翻转、导星、异常恢复和收尾动作；具体触发器与行为以当前 N.I.N.A. 序列文档和现场干跑结果为准。[^src-0232a4c5][^src-aabaeaf1]
- 无人值守前必须验证失败路径和安全收尾，不能因为一次模拟成功就宣称序列可长期无人值守。[^src-0232a4c5]
""",
    "03-拍摄SOP/数据管理、命名、备份与可复现归档.md": """## 权威问答口径

- FITS 头字段、数据类型和扩展结构应遵循 FITS 标准；软件能够打开文件不等于元数据完整或像素值未被改变。[^src-fits-standard][^src-siril-fits]
- 可复现归档至少要保留原始帧、校准帧、处理参数/脚本、软件版本和输出谱系；脚本语法与执行边界以当前 Siril 官方文档为准。[^src-siril-scripts]
""",
    "03-拍摄SOP/极轴校准.md": """## 权威问答口径

- 极轴校准工具输出的是特定测量模型下的估计；操作步骤、视场要求和误差读数含义必须以当前 SharpCap 官方说明为准，并用实际跟踪/导星结果复核。[^src-6f1a86fe]
- 极轴误差不是所有拖线的唯一原因；回答诊断问题时必须同时排查周期误差、风、线缆、机械松动、对焦和场旋转。[^src-6f1a86fe]
""",
    "03-拍摄SOP/校准帧现场速查卡.md": """## 权威问答口径

- 校准帧能否复用取决于相机、增益/偏置、温度、曝光、光路和软件校准模型；不把“固定拍多少张”或“所有相机都需要同一类校准帧”作为通用规则。[^src-siril-calibration]
- 现场速查卡是防漏项工具，不替代具体相机与处理软件文档；归档时必须记录与亮场的匹配条件。[^src-siril-calibration]
""",
    "03-拍摄SOP/校准帧规范.md": """## 权威问答口径

- Bias、Dark、Flat 与 Dark-flat 的用途和组合取决于传感器行为与软件校准流程；应依据当前 Siril 校准模型和具体相机测试选择，不能机械叠加所有帧型。[^src-siril-calibration]
- Flat 必须对应未改变的光路，并避免进入非线性或饱和区；精确曝光和数量应由相机响应、光源稳定性与实测统计决定。[^src-665f52ac][^src-siril-calibration]
""",
    "03-拍摄SOP/现场搭建流程.md": """## 权威问答口径

- 现场搭建的安全优先级高于出片：雷暴威胁下应停止作业并进入封闭建筑或硬顶车辆，不能在帐篷、棚架或孤立树木附近继续架设。[^src-nws-lightning]
- 完成平衡、线缆活动范围、限位、碰撞和断电收尾测试后，才进入自动指向与跟踪；一次白天干跑不能替代夜间全姿态检查。
""",
    "05-目标图鉴/四季目标推荐.md": """## 权威问答口径

- “四季目标”只是在特定半球和纬度下的规划入口；实际可拍窗口必须按地点、日期、目标高度、月相和遮挡重新计算。厂商季节清单可用于发现候选目标，不能作为可见性保证。[^src-32776405]
- 目标是否适合当前系统还要通过角尺寸、相机传感器、焦距、旋转角与视场计算复核；不从季节标签直接推导焦距、滤镜或曝光。
""",
    "06-选址与环境/月相-季节窗口-远程台.md": """## 权威问答口径

- 月相来自日、地、月几何关系；月光是太阳光经月面反射。深空计划不能只看“月龄”，还要结合月亮高度、角距、目标光谱、天空透明度和滤镜。[^src-nasa-moon-phases][^src-nasa-moonlight]
- 远程台的权威判断必须覆盖天气传感、屋顶/镜筒互锁、断电恢复、网络失联和人工接管；商业页面只能证明服务商当期声明，不能替代用户的安全验收。
""",
    "06-选址与环境/视宁度-透明度-云量.md": """## 权威问答口径

- 视宁度是大气湍流属性；长曝光图像质量还与波长、天顶距和仪器有关，不能把单一预报数值等同于最终星点 FWHM。[^src-eso-observing-conditions]
- 云量、透明度和视宁度是不同变量。天气云量产品描述天空覆盖，不能代替透明度或湍流测量；开拍前仍需用现场星点、背景和气象安全条件复核。[^src-nws-sky-cover][^src-eso-observing-conditions]
""",
    "07-软件工具/N.I.N.A 使用要点.md": """## 权威问答口径

- N.I.N.A. 的功能、系统要求、插件和序列行为以当前官方文档与版本日志为准；插件存在不代表它适配所有设备或与当前版本兼容。[^src-cfce4141][^src-e587bbb6][^src-b938f0f6]
- 自动化序列必须经过本机连接、模拟/白天干跑和夜间安全收尾验证；文档支持某功能不等于用户系统已经可无人值守。[^src-b938f0f6]
""",
    "07-软件工具/PHD2 导星软件.md": """## 权威问答口径

- PHD2 应为不同设备组合建立独立配置，并按官方 Basic Use 完成连接、选星、校准和导星；不能仅凭一个 RMS 数字判断整套主相机曝光是否合格。[^src-phd2-basic-use][^src-63fdabf7]
- 高级参数要在明确故障证据后调整；焦距、像元、binning 和校准条件错误会影响下游参数与角秒报告，优先使用新配置向导和官方说明。[^src-phd2-advanced-settings]
""",
    "07-软件工具/规划与解析工具.md": """## 权威问答口径

- 板解结果必须与图像尺度、坐标、旋转角和可接受误差一起判断；ASTAP 是否支持某流程以当前官方算法与版本说明为准。[^src-03aabae0][^src-206f5ba7]
- Stellarium 与 SkySafari 可用于目标发现和构图规划，但最终可见性仍受地点、时间、遮挡和现场条件约束；软件画面不是天空可拍性的保证。[^src-c8b18352][^src-70b05db4]
""",
    "08-FAQ/单张检查与翻车诊断.md": """## 权威问答口径

- 单张诊断先区分显示拉伸与原始数据，再检查星点、饱和、背景、对焦、跟踪和元数据；预览“看起来正常”不能证明原始线性帧合格。[^src-phd2-basic][^src-siril-calibration]
- 导星日志只说明导星系统记录到的行为，校准帧只修正其模型覆盖的缺陷；两者都不能单独证明最终主相机像素可交付。[^src-phd2-basic][^src-siril-calibration]
""",
    "08-FAQ/高频FAQ汇总.md": """## 权威问答口径

- 导星问题应按 PHD2 官方流程核对设备配置、校准、选星和日志，不能用论坛中的“合格 RMS”阈值代替主相机星点验收。[^src-phd2-basic-use]
- Flat、Dark、Bias/Dark-flat 的组合取决于相机行为与处理软件模型；回答“是否必须拍某类校准帧”时必须带上相机、温度、增益、曝光和软件条件。[^src-siril-calibration]
""",
    "09-踩坑与复盘/新手常见踩坑与复盘.md": """## 权威问答口径

- 复盘先保存原始帧、日志、设备配置和时间线，再按证据定位；论坛案例只能作为排查假设，不能直接定因。导星问题按 PHD2 官方流程复核，校准问题按 Siril 当前模型复核。[^src-phd2-basic-use][^src-siril-calibration]
- 雷暴威胁属于停止条件，不进入“继续尝试”的故障排查；应撤入封闭建筑或硬顶车辆。[^src-nws-lightning]
""",
}


SOURCE_ADDITIONS = {
    "01-新人入门/新手预算与器材路线.md": [
        ("src-nina-requirements", "https://nighttime-imaging.eu/docs/master/site/requirements/", "N.I.N.A. 官方文档：System Requirements and Device Support"),
        ("src-celestron-back-focus", "https://www.celestron.com/blogs/knowledgebase/understanding-your-telescope-s-back-focus", "Celestron：Understanding Your Telescope’s Back Focus"),
    ],
    "01-新人入门/深空摄影是什么.md": [
        ("src-nasa-smartphone", "https://science.nasa.gov/solar-system/skywatching/night-sky-network/astrophotography-with-your-smartphone/", "NASA Science：Astrophotography With Your Smartphone"),
    ],
    "02-器材百科/滤镜系统.md": [
        ("src-zwo-narrowband-filters", "https://www.zwoastro.com/product/narrowband-filters/", "ZWO：Narrowband Filters"),
    ],
    "03-拍摄SOP/现场搭建流程.md": [
        ("src-nws-lightning", "https://www.weather.gov/safety/lightning-safety-overview", "U.S. National Weather Service：Lightning Safety"),
    ],
    "06-选址与环境/月相-季节窗口-远程台.md": [
        ("src-nasa-moon-phases", "https://science.nasa.gov/moon/moon-phases/", "NASA Science：Moon Phases"),
        ("src-nasa-moonlight", "https://science.nasa.gov/moon/moonlight/", "NASA Science：Moonlight"),
    ],
    "06-选址与环境/视宁度-透明度-云量.md": [
        ("src-eso-observing-conditions", "https://www.eso.org/sci/observing/phase2/ObsConditions.SPHERE.html", "ESO：Observing Conditions Definitions"),
        ("src-nws-sky-cover", "https://www.weather.gov/media/directives/010_pdfs_archived/pd01008013a.pdf", "U.S. National Weather Service：TAF Sky Cover Definitions"),
    ],
    "09-踩坑与复盘/新手常见踩坑与复盘.md": [
        ("src-phd2-basic-use", "https://openphdguiding.org/man/Basic_use.htm", "PHD2 官方文档：Basic Use"),
        ("src-siril-calibration", "https://siril.readthedocs.io/en/latest/preprocessing/calibration.html", "Siril 官方文档：Calibration"),
        ("src-nws-lightning", "https://www.weather.gov/safety/lightning-safety-overview", "U.S. National Weather Service：Lightning Safety"),
    ],
    "05-目标图鉴/经典目标推荐参数.md": [
        ("src-simbad-m42", "https://simbad.cds.unistra.fr/simbad/sim-basic?Ident=M+42&submit=SIMBAD+search", "SIMBAD：M 42"),
        ("src-simbad-m31", "https://simbad.cds.unistra.fr/simbad/sim-basic?Ident=M+31&submit=SIMBAD+search", "SIMBAD：M 31"),
        ("src-simbad-m51", "https://simbad.cds.unistra.fr/simbad/sim-basic?Ident=M+51&submit=SIMBAD+search", "SIMBAD：M 51"),
        ("src-nasa-m81", "https://science.nasa.gov/image-detail/m81-print/", "NASA Science：M81"),
        ("src-simbad-ngc7000", "https://simbad.cds.unistra.fr/simbad/sim-basic?Ident=NGC+7000&submit=SIMBAD+search", "SIMBAD：NGC 7000"),
    ],
}


BUDGET_CLAIM = """## 权威问答口径

- 预算建议首先回答“整套系统能否运行”，而不是给出长期固定价位：操作系统与设备支持要按当前控制软件文档核对，光学距离要按制造商指定基准面核对。[^src-nina-requirements][^src-celestron-back-focus]
- 实时价格、库存、税费、保修与二手状态不属于本页的稳定权威结论；给出具体购买清单时必须标明地区、查询日期和完整兼容性验收结果。
"""


CLASSIC_CORE = """## 核心知识点
- M42 是 SIMBAD 收录的 H II 区；其公开角尺寸约为 66′，因此通常需要先用传感器尺寸与焦距计算是否能容纳核心和外围。目录角尺寸是构图输入，不是曝光建议。[^src-simbad-m42]
- M31 的 SIMBAD 条目给出接近 200′×71′ 的光学角尺寸，属于大尺度目标；是否单幅容纳或需要马赛克由传感器、焦距、旋转角和预留背景共同决定。[^src-simbad-m31]
- M51 是星系目标；小角尺度目标是否适合当前系统，应先查询科研目录并计算视场与采样，再用测试帧决定曝光，不能复制他人单帧时长。[^src-simbad-m51]
- M81 是旋涡星系；NASA 页面可确认目标身份，但不提供跨设备的通用曝光参数。与 M82 同框与否必须由目标间距、传感器和焦距计算。[^src-nasa-m81]
- NGC 7000 的 SIMBAD 条目提供目标标识、坐标和参考文献；其宽场构图、滤镜与曝光仍需按当前系统和天空条件测试。[^src-simbad-ngc7000]

## 权威问答口径

- 科研目录可承担目标身份、坐标、分类和有出处的角尺寸等事实；公开实拍文章只能证明某个设备组合曾这样拍摄，不能承担跨系统“推荐曝光”。[^src-simbad-m42][^src-simbad-m31][^src-simbad-m51][^src-nasa-m81][^src-simbad-ngc7000]
- 对这五个目标，本页只权威回答构图与测试方法：先计算视场/采样，再拍测试帧检查星点、背景、饱和和直方图，最后记录本机采用的增益、温度、滤镜、单帧与总积分条件。

## 注意事项
- M42 核心可能比外围更早饱和；只有测试帧表明确有动态范围需求时，才规划多档曝光，不能把某个秒数当作跨设备阈值。
- 星系以连续谱为主。窄带不能作为光污染下拍摄所有星系的默认替代；应评估暗空窗口、总积分、梯度处理与宽带策略。
- M31 与 NGC 7000 的构图对视场敏感，必须用拍摄地点、相机、焦距和旋转角重新计算。
- ISO/增益、单帧和总积分取决于传感器读出模式、焦比、滤镜、天空背景、温度、跟踪与拒片率；本页不提供脱离条件的固定值。

## 条件化示例（不作为权威结论）
- 旧版公开案例及其曝光数字保留在“信息来源”中用于追溯，但因链接可用性、设备差异和条件缺失，不进入权威问答。
- 建立本机参数时，先保存一组短测试帧和完整 FITS 头，再根据饱和像素、背景统计与星点决定正式序列。

"""


def source_block(source_id: str, resource: str, title: str) -> str:
    return (
        f"  - id: {source_id}\n"
        f"    resource: \"{resource}\"\n"
        f"    title: \"{title}\"\n"
        "    evidence_level: primary\n"
        "    rights: unknown\n"
        "    usage: link-only\n"
        "    accessed_at: \"2026-08-27\"\n"
    )


def add_sources(text: str, additions: list[tuple[str, str, str]]) -> str:
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("unclosed frontmatter")
    blocks = []
    frontmatter = text[:end]
    for source_id, resource, title in additions:
        if f"id: {source_id}\n" not in frontmatter:
            blocks.append(source_block(source_id, resource, title))
    if blocks:
        text = text[:end] + "\n" + "".join(blocks) + text[end:]
    return text


def insert_claims(text: str, claims: str) -> str:
    if "## 权威问答口径\n" in text:
        return text
    for anchor in ("\n## 注意事项\n", "\n## 关联知识\n", "\n## 维护记录\n"):
        if anchor in text:
            return text.replace(anchor, "\n" + claims + anchor, 1)
    raise ValueError("missing insertion anchor")


def main() -> int:
    changed = []
    targets = set(CLAIMS) | set(SOURCE_ADDITIONS) | {
        "01-新人入门/新手预算与器材路线.md",
        "01-新人入门/深空摄影是什么.md",
        "02-器材百科/望远镜选型.md",
        "05-目标图鉴/经典目标推荐参数.md",
    }
    for relative in sorted(targets):
        path = ROOT / relative
        text = path.read_text(encoding="utf-8")
        original = text
        text = add_sources(text, SOURCE_ADDITIONS.get(relative, []))

        if relative in CLAIMS:
            text = insert_claims(text, CLAIMS[relative])
        elif relative == "01-新人入门/新手预算与器材路线.md":
            text = insert_claims(text, BUDGET_CLAIM)

        if relative == "01-新人入门/新手预算与器材路线.md":
            text = text.replace("。[^src-8802da80]", "。")
        elif relative == "01-新人入门/深空摄影是什么.md":
            text = text.replace("[^src-523671d1]", "[^src-nasa-smartphone]")
        elif relative == "02-器材百科/望远镜选型.md":
            text = text.replace("    evidence_level: secondary\n    rights: unknown\n    usage: link-only\n    accessed_at: \"2026-08-27\"\n  - id: src-08db1f54", "    evidence_level: primary\n    rights: unknown\n    usage: link-only\n    accessed_at: \"2026-08-27\"\n  - id: src-08db1f54", 1)
            text = text.replace("- 经验法则：深空入门总预算中主镜约占总投入的 30%–40%，不要忽略赤道仪与导星的重要性。", "- 采购比例没有跨系统通用值；主镜、跟踪、相机、光路附件、供电、控制和数据存储必须按完整系统列单共同核算。")
            text = text.replace("- Mak(马克苏托夫)焦比偏大(f/11+)，深空效率偏低，更适合行星/月面。", "- Mak 的焦比、像场与调焦结构因型号而异；是否适合深空目标应按具体型号、视场、采样、跟踪和单帧测试判断。")
            text = text.replace("：Askar 120APO 页面记录 120 mm、840 mm、f/7；实际成像还要核对所配平场/减焦器、后截距、像圈和调焦器承载。[^src-08db1f54]", "：折射镜的口径、焦距、焦比、配套平场/减焦器、后截距、像圈和调焦器承载必须从当前制造商资料逐项核对；零售页示例不进入权威结论。")
        elif relative == "05-目标图鉴/经典目标推荐参数.md":
            start = text.index("## 核心知识点\n")
            end = text.index("## 相关资源\n", start)
            text = text[:start] + CLASSIC_CORE + text[end:]

        if text != original:
            path.write_text(text, encoding="utf-8")
            changed.append(relative)

    print(f"updated {len(changed)} pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
