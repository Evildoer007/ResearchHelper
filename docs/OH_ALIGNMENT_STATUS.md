# 与OptionHelper的对齐边界

ResearchHelper参考OptionHelper的开发分层、原生App壳、共享交互、登录角色、构建入口和报告视觉。OptionHelper只读，不修改其源码、数据或配置。

|部分|ResearchHelper归属|
|---|---|
|共享内核|core与modules，App和Skill共用|
|产品界面|products/app，ResChat发起研究，ResDesk展示底稿|
|内容与设计|ReportComposer、ResearchDesigner、ArtifactExporter分工|
|构建与安装|packaging，按白名单组装，安装与同步单独核对|
|开发说明|docs与maintenance，保持当前有效说明|
|Skill指南|references，字段以实际工具契约为准|

不复制OptionHelper的衍生品产品、收益计算、定价、回测或推荐规则。模块名按投研职责确定，研究报告沿用用户确认的券商版式，不能把OptionHelper合同报告硬套为研究内容。

macOS安装包、Skill和两份demo按实际候选核验；Windows有构建源码，仍需Windows实机验收。当前来源、模型质量、行业覆盖及全部PRD尚未全面验收。每次对齐只记录本轮实际检查范围，不以代码相似或视觉一致代替研究质量。
