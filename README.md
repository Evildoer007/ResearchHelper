# ResearchHelper

输入一个标的、事件或研究问题，取得所需数据，形成几条投资逻辑和可追踪的研究报告。App与Skill共用研究内核。

## 一、入口

- [产品规范](blueprint/prd.md)：唯一PRD。
- [Skill](SKILL.md)：对话式研究入口。
- [当前状态](docs/CURRENT_VERSION.md)：源码、安装物和实际验收范围。
- [目录说明](docs/PROJECT_STRUCTURE.md)：每层代码放在哪里。
- [构建说明](packaging/README.md)：macOS和Windows构建、安装及同步。
- [维护说明](maintenance/README.md)：清理与候选核验。

开发以仓库根目录为准。OptionHelper、DataFetcher和InvestBuddy只作只读参考。

## 二、项目结构

|目录|职责|
|---|---|
|core/|任务、工具契约、预算、知识、档案和服务|
|modules/|取证、分析、论点、报告、设计与导出|
|products/app/|ResChat、ResDesk、后端和两端原生壳|
|references/|Skill运行、证据、知识、交付四份指南|
|blueprint/|产品与视觉规范|
|packaging/|唯一构建流程、依赖锁和资源清单|
|maintenance/|现用维护工具|
|docs/|当前开发和能力说明|
|tests/|跨层验证，单层测试留在所属目录|
|assets/、LICENSES/|离线资源、图标与许可|
|data/、result/|本地数据、报告、现版候选和必要验收样本|
|dist/、versions/|当前安装包与内容寻址清单|
|demo/|主项目生成的展示副本|

研究实现只维护一份。生成的App、独立Skill和demo使用自包含内核，不依赖开发仓库绝对路径。不要修改.skills中的安装副本或把界面逻辑另外复制进demo。

## 三、研究与报告

先识别对象和问题，再按需取数、执行可用计算、形成策略逻辑并保存首稿。缺少部分数据不阻断首次报告；缺口、核验与分析限制放在ResDesk，后续补充形成新版本。

iFinD固定API优先，行情与财务字段复用，不默认下载PDF，不使用Wind。Skill由宿主负责语言推理，App使用自己的模型配置。两者不共享其他产品的凭据或生产库。

对客报告保留核心结论、策略逻辑、图表和风险。内部详版在原逻辑位置展开数据、理论、计算、反证和来源。所有格式先保存HTML，再从同版HTML转换PDF、Word、Excel或CSV；样式和导出不增加模型或数据调用。

## 四、构建与安装

双击build-researchhelper-macos.command或build-researchhelper-windows.bat。macOS先生成安装包，再从实际DMG隔离启动App，验证机制、ETF和投资判断三类案例、标准和详细报告及五种导出，并与独立Skill核对数据；全部通过后更新项目Skill。开发使用现有MachineLearning环境，不另建环境。成品App不依赖用户的Conda或源码目录。

|交付|位置|
|---|---|
|macOS安装包|[dist/ResearchHelper-0.10.0-arm64.dmg](dist/ResearchHelper-0.10.0-arm64.dmg)|
|Skill安装包|[dist/ResearchHelper-Skill-0.10.0.zip](dist/ResearchHelper-Skill-0.10.0.zip)|
|项目级Skill安装|.skills/researchhelper/|
|已安装App|/Applications/ResearchHelper.app|
|主项目demo|[demo/index.html](demo/index.html)|
|桌面demo|/Users/haoranxu/Desktop/demo/ResearchHelper demo/|

表中安装包、项目级Skill和demo均为本地生成物，不随源码提交。下载原件、模型和数据源凭据、研究库及验收临时目录也不进入Git；完整本地验收中的留存真实数据案例需要原研究资料，不能视为新clone即可重放的公开测试数据。

构建、包内检查、安装和demo同步分别确认，构建成功不自动表示已经安装。Windows安装验收需要Windows主机；本机源码检查不能代替。当前App未Apple公证。

App数据位于Application Support/ResearchHelper/research-v1；独立Skill数据位于使用项目的data/researchhelper与result/researchhelper。私密配置、下载原件和测试Store不进入分发包。

## 五、开发约定

根VERSION维护版本；依赖按锁文件核对；打包按明确白名单取源码。测试和可再生验收产物不进入运行包。docs维护当前说明，references维护按需指南，接口字段从实际工具读取，不再按每次修改追加历史文档。

清理只移除已确认不再使用的内容，保留现版交付、实际报告和回归样本。清单及恢复映射放在result/maintenance，不另存整仓备份。当前状态和验收边界见[当前状态](docs/CURRENT_VERSION.md)。
