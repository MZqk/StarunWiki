# 后期处理

* [智能望远镜导出数据的 Siril 工作流](智能望远镜导出数据的Siril工作流.md) - 以数据状态为门禁处理 Seestar 专用 Siril 路径，明确不把通用校准流程错误套用于设备专用导出数据。
* [Siril 新手首图工作流](Siril新手首图工作流.md) - 用可回退的“分组、校准、配准、筛选、整合、线性主图、基础处理”七关完成第一张基础成图。
* [校准与叠加](%E6%A0%A1%E5%87%86%E4%B8%8E%E5%8F%A0%E5%8A%A0.md) - 叠加（Stacking/Integration）是把多张亮场通过校准（暗场/平场/偏置）与配准后做像素级平均，从而大幅提升信噪比的核心步骤。
* [窄带SHO映射](%E7%AA%84%E5%B8%A6SHO%E6%98%A0%E5%B0%84.md) - 窄带映射是把 Ha、SII、OIII 三个单色通道按规则分配到 R/G/B，最经典是 SHO（SII→红、Ha→绿、OIII→蓝）即哈勃调色板。
* [LRGB处理与调色](LRGB%E5%A4%84%E7%90%86%E4%B8%8E%E8%B0%83%E8%89%B2.md) - LRGB 把高分辨的 L（亮度）通道与 R/G/B 彩色通道合成，兼顾细节与色彩。
* [Photoshop精修](Photoshop%E7%B2%BE%E4%BF%AE.md) - Photoshop 是窄带/宽带主图完成后的精修终端：用 16-bit 模式与曲线/色阶做最终拉伸，配合 RC Astro 的 NoiseXTerminator（AI 降噪）、StarXTerminator（星点分离）与 StarShrink（星点缩小）管理星点，再用蒙版做局部亮度/色彩增强。
