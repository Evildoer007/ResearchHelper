# ResearchHelper第三方资源

本目录保留实际使用的浏览器资源许可与固定来源，不复制OH未被RH使用的业务或依赖清单。

|组件|版本|许可文件|使用位置|
|---|---|---|---|
|Apache ECharts|6.0.0|Apache-ECharts-LICENSE.txt、Apache-ECharts-NOTICE.txt|研究报告及历史报告的离线图表|
|ECharts中的d3代码|随上述固定版本|Apache-ECharts-d3-LICENSE.txt|原ECharts子组件，未单独升级|
|ThinkingOrbs|0.3.1|ThinkingOrbs-LICENSE.txt|聊天过程的几何图形|

两个SOURCE.md文件记录各自来源及适配边界。ECharts本体与官方固定版本逐字节核对，聊天图形保留原MIT许可与来源记录。

packaging/shared_payload.py按固定组件清单复制许可。App及demo包含图表与聊天图形的许可；Skill仅包含它实际使用的图表许可。各载荷的licenses/inventory.json记录实际复制文件；App另保存已有Python依赖许可，不以这张表替代完整依赖清单。

构建不会联网获取许可、扫描其他产品的许可证或复制任意新增文件。缺少固定文件、源文件是链接或复制不一致时失败。图表文件内的原版权注释保持不变，本轮产品载荷检查不代替逐份独立HTML导出的许可附录核验。
