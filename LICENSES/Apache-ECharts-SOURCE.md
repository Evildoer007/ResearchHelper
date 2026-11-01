# Apache ECharts来源

ResearchHelper使用Apache ECharts6.0.0，固定来源为https://github.com/apache/echarts/tree/6.0.0。2026-10-12核对本地assets/vendor/echarts.min.js与该标签dist/echarts.min.js，字节完全一致；没有升级、重建或修改图表库。

图表库SHA256：baa8dfe7e1d9336b98e8986ba7e20ea15e7cdbea1ef42a59d59478632fa45a1d。

- Apache-ECharts-LICENSE.txt：https://raw.githubusercontent.com/apache/echarts/6.0.0/LICENSE。SHA256：634293835b43a6dd2094fa39182a3d9a6b9ca43b7fdb9ac354e8037af2a3093a。
- Apache-ECharts-NOTICE.txt：https://raw.githubusercontent.com/apache/echarts/6.0.0/NOTICE。SHA256：dc0d9176413c3d96d0ac40b39b1dbb11e3043b7b010be560f5dfe66bd3774ae1。
- Apache-ECharts-d3-LICENSE.txt：https://raw.githubusercontent.com/apache/echarts/6.0.0/licenses/LICENSE-d3。SHA256：e1211892da0b0e0585b7aebe8f98c1274fba15bafe47fa1f4ee8a7a502c06304。

原LICENSE包含Apache2.0及d3.js子组件说明，对应d3许可另完整保留。原图表文件内的其他版权与许可注释不改写。

App、Skill及demo通过packaging/shared_payload.py的固定清单携带这些文件。App与demo另包含聊天图形的ThinkingOrbs许可；Skill只包含报告图表需要的ECharts许可。报告由modules/visualrenderer/src/visual.py及report_examples.py使用离线图表库，不依赖旧render目录或外部CDN。
