# 开发目录

ResearchHelper采用与OptionHelper相同的分层原则：共享源码、App、Skill、构建和生成物分开。模块职责按投研需求定义，不复制衍生品业务。

|路径|职责|
|---|---|
|blueprint/|PRD、产品与视觉规范|
|core/src/researchhelper/|调度、任务、预算、知识、档案和本地服务|
|core/session.py|Skill宿主调用入口|
|modules/evidencecollector/|数据与原件获取、解析和来源定位|
|modules/researchanalyzer/|指标、登记计算与分析核对|
|modules/thesisbuilder/|问题、策略逻辑与反证|
|modules/reportcomposer/|报告内容、对客与内部投影|
|modules/visualrenderer/|ResearchDesigner、图表、主题和HTML|
|modules/artifactexporter/|HTML之后的PDF、Word和数据附件转换|
|products/app/|共用前后端与macOS、Windows原生壳|
|references/|Skill的运行、证据、知识和交付指南|
|packaging/|构建器、依赖锁、白名单与安装同步工具|
|maintenance/|仍被使用的维护与交付核验工具|
|tests/及各层tests/|现用单模块、跨层与产品验收|
|assets/、LICENSES/|品牌、离线资源和许可|
|data/|项目数据，不随包分发|
|result/|研究报告、现版候选和必要验收样本|
|dist/|当前DMG与Skill ZIP|
|versions/|内容寻址交付物及其清单|
|demo/|由主项目生成的离线展示副本|

## 一、唯一实现

开发态将core和各模块源码加载到同一个researchhelper命名空间。App、Skill与demo由packaging/shared_payload.py组装同一内核。模块不是独立服务，不额外启动一组模型。

Skill源码在SKILL.md、core/session.py、references和agents。项目级安装副本位于.skills/researchhelper，属于生成物，不在里面开发。App安装到系统应用目录，生产数据位于Application Support/ResearchHelper；独立Skill使用研究项目自己的data和result。

## 二、构建与同步

根目录两个双击入口分别负责macOS和Windows。依赖锁在core/requirements.lock和packaging/build-requirements.lock；package-source-map.json声明可打包源码。构建使用临时工作区，不把编译缓存留在开发目录。

构建、安装和同步分别记录。通过包内检查后，安装App并同步主项目demo与桌面demo/ResearchHelper demo。普通构建不直接覆盖用户生产数据。Windows源码与构建入口不等于Windows实机已验收。

## 三、保留规则

源码目录不放按日期累积的修补说明、旧Qt或报价实现、一次性迁移脚本、重复发行副本。docs只维护当前说明；references只维护宿主需要的指南；接口细节以实际工具Schema为准。

测试保留现用用例及其依赖。共享测试类在定义文件收集，显式派生的本地测试类保留其独立场景。真实研究回放保留原始输入依赖，缺少原件不计为通过。旧版单次发布脚本退出现用目录，真实数据样本仍被回归测试使用时保留。当前报告、原件、生产库、现用App、Skill与两份demo不是清理对象。

不修改OptionHelper、DataFetcher或InvestBuddy，不重置Git、不自动push。清理清单与恢复位置统一记录在result/maintenance/repository-cleanup-20261028。
