# Research Helper

Research Helper 是一款面向投研分析师的桌面应用，用于将客户需求、市场数据与研究材料整理为可审计的
《场外衍生品投资策略》一页通。系统覆盖需求解析、研究口径确认、自动取数、论点筛选、图表生成、数字溯源、
HTML/PDF 交付，以及可选的 OptionHelper 结构推荐与正式参考报价。

> 本项目用于内部研究与信息整理。生成内容不构成投资建议、销售要约或交易承诺。

## 核心能力

- **需求解析**：识别研究主题、市场范围、事件事实、客户约束和客户点名的 ETF/个股。
- **统一取数确认**：所有研究在取数前均确认市场、主题和取数目标，不以需求风险高低豁免；研究目标与报价标的分离。
- **多路径取数**：支持标准行业成分、人工主题篮子和主题 ETF 真实指数成分三种研究路径。
- **双路径触发研究**：明确外部事件走原文证据硬门；市场回调、风格轮动等市场状态触发题使用行情与历史区间验证。
- **可审计交付**：逐项校验正文数字，输出一页通 HTML、内部底稿、运行日志和可选 PDF。
- **受控产品流程**：为每只待报价标的生成产品画像，经 OptionHelper 推荐、分析师确认后再正式报价。
- **逐标的调用留痕**：内部底稿分别保存研究完成时的共同观点快照，以及后来实际送入 OptionHelper 的标的、产品画像、客户约束和调用状态。
- **多标的处理**：支持客户同时指定多只 ETF/个股，逐只形成候选与报价，并由分析师决定哪些写入一页通。

## 工作流程

```text
客户需求与约束
      │
      ▼
需求解析 ──► 市场、研究主题、事件路径、候选影响分支、客户点名标的
      │
      ▼
分析师确认研究取数目标（所有研究必经；此时不确认正式挂钩标的）
      ├─ 标准行业：自动使用数据源行业成分
      ├─ 人工主题篮子：使用分析师勾选的已核验公司
      └─ 主题 ETF：使用 ETF 真实跟踪指数成分
      │
      ▼
市场数据 + sources/ 补充材料
      │
      ├─ 普通研究：数据触发 + 材料提炼
      ├─ 市场状态触发：当前分化 + 历史区间 + 持续/失效条件
      └─ 明确外部事件：三路检索（事件事实 / 产业机制 / A股暴露）
                         → 影响分支筛选（1主+最多1备选）
                         → 原文逐字校验 → 组合传导链 → 分析师确认
      │
      ▼
候选论点生成（数据触发 / 已确认主事件分支 / 材料提炼）
      → 分析师审核；明确外部事件至少保留一条已确认主分支
      │
      ▼
研究观点、图表、数字溯源与一页校验
      │
      ├─► 一页通 HTML / PDF + 内部底稿 + 运行日志
      │
      └─► 可选产品流程（研究完成后才进入）
             │
             ▼
        统一展示并确认待报价标的
             │
             ▼
        Research Helper 逐标的产品画像
             │
             ▼
        OptionHelper 结构推荐
             │
             ▼
        分析师确认结构
             │
             ▼
        OptionHelper 正式定价与报价
             │
             ▼
        分析师选择写入一页通的报价
```

## 关键概念

| 对象 | 作用 | 示例 |
|---|---|---|
| 研究主题 | 回答“研究什么”，用于材料检索、事件分析和报告标题 | 光模块、汽车电子、固态电池 |
| 标准行业 | 数据源能够验证并取得成分股的行业节点 | 通信设备、电力设备、消费电子 |
| 主题研究篮子 | 用于形成主题结论的公司集合 | 中际旭创、新易盛、天孚通信等 |
| 主题 ETF | 以 ETF 真实跟踪指数成分作为研究篮子 | 智能驾驶 ETF 的真实指数成分 |
| 挂钩标的 | 用于产品表达、结构推荐和正式报价的证券 | ETF、指数或客户点名个股 |
| 标的产品画像 | 结构推荐前的标的事实，包括收益、波动、回撤、情景收益和流动性 | 每只待报价标的独立生成 |

研究篮子决定“研究数据从哪里来”，挂钩标的决定“产品对什么报价”。二者可以相关，但不能互相替代。
机器人等主题不必先映射为标准行业：可选主题 ETF 路径，以所选 ETF 的真实指数成分研究。
已发现 ETF 候选时，不因短主题识别遗漏而隐藏该路径；展示候选不代表通过校验，仍须确认证券、跟踪指数、主题暴露和流动性。

ETF 候选发现、主题筛选和提交校验统一使用短研究主题，期限与投资诉求不参与基金名称匹配。
系统合并常用工具池和 iFinD 动态检索结果，保留官方信息并去重；常用池不是免检白名单。
无候选时，确认页说明检索不可用、官方主题不相关或流动性不足等原因；悬停提示及运行日志保存更完整的检索与淘汰记录。

## 系统要求

- Windows 10/11
- Python 3.10 或更高版本
- 可用的 DeepSeek API Key
- iFinD SDK、账号和密码（主要研究数据源）
- Qt WebEngine/PySide6（桌面界面和 PDF 导出）
- OptionHelper Skill 与独立 Python 环境（仅正式报价需要）

## 安装发布版（推荐）

面向研究员分发时使用 `ResearchHelper-Setup-<版本>.exe`；没有安装器时也可解压
`ResearchHelper-<版本>-win64.zip`，双击 `ResearchHelper.exe`。最终用户无需安装项目 Python
依赖，也不需要克隆仓库。首次启动会打开设置向导：

1. 填写本人的 DeepSeek API Key；Tavily、iFinD 与 OptionHelper 可按实际权限选填。
2. iFinD 由研究员先安装官方终端/Quant SDK；发布包不转售或复制专有 SDK。
3. 正式报价用户填写已验收的 OptionHelper Skill 路径和其独立 Python 环境。
4. 进入主界面后点击“运行环境检查”，确认研究、搜索、行情和报价能力的状态。

程序文件与用户数据相互分离。安装目录只读，每位 Windows 用户的配置与运行数据位于：

```text
%LOCALAPPDATA%\ResearchHelper\
├─ config.local.json       # 本机凭证与设置，不进入安装包
├─ sources\                # 用户上传/粘贴的补充材料
├─ data_cache\             # 可复用缓存
├─ history_titles.json      # 历史任务自定义标题；不改运行原文
├─ output\                 # HTML、PDF、底稿与运行记录
├─ data\ / result\         # OptionHelper 受控运行数据
└─ .optionhelper\          # 一次性 selection 与本机状态
```

卸载程序默认不删除该目录，便于保留研究记录；需要彻底清除时由用户确认后手动删除。

## 源码安装（开发与维护）

在项目根目录创建并启用 Python 环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install requests PySide6 matplotlib seaborn pyecharts akshare pypdf python-docx
```

iFinD 的 `iFinDPy` 由同花顺终端或官方 SDK 安装程序提供，不建议通过非官方 PyPI 包替代。安装完成后可运行：

```powershell
python -c "import iFinDPy; print(iFinDPy.__file__)"
```

## 配置

发布版由首次启动向导在 `%LOCALAPPDATA%\ResearchHelper\config.local.json` 创建配置；
源码开发态仍在项目根目录创建 `config.local.json`。该文件已加入 `.gitignore`，不得提交到版本库。

```json
{
  "DEEPSEEK_API_KEY": "your-api-key",
  "DEEPSEEK_FAST_MODEL": "deepseek-flash",
  "DEEPSEEK_QUALITY_MODEL": "deepseek-flash",
  "DEEPSEEK_FALLBACK_MODEL": "deepseek-flash",
  "TAVILY_API_KEY": "tvly-your-api-key",
  "SEARCH_PROVIDER": "tavily",
  "SEARCH_DEPTH": "basic",
  "SEARCH_COUNTRY": "china",
  "SEARCH_LANGUAGE": "zh-cn",
  "SEARCH_BING_FALLBACK": true,
  "IFIND_ACCOUNT": "your-ifind-account",
  "IFIND_PASSWORD": "your-ifind-password",
  "IFIND_SDK_PATH": "C:/path/to/official/ifind/sdk",
  "OPTIONHELPER_SKILL_ROOT": "C:/path/to/option-helper",
  "OPTIONHELPER_PYTHON": "C:/path/to/optionhelper/python.exe",
  "OPTIONHELPER_DEFAULT_CONSTRAINTS": {
    "horizon": "3个月",
    "max_loss": "100%",
    "principal_fluctuation": true
  }
}
```

同名环境变量优先于 `config.local.json`。主流程常用变量如下：

| 配置项 | 必需 | 用途 |
|---|---:|---|
| `DEEPSEEK_API_KEY` | 是 | 需求解析、论点规划和研究写作 |
| `DEEPSEEK_FAST_MODEL` | 否 | 需求解析、材料抽取和证据分类使用的快速档模型 |
| `DEEPSEEK_QUALITY_MODEL` | 否 | 研究规划、正文及结构推荐使用的质量档模型 |
| `DEEPSEEK_FALLBACK_MODEL` | 否 | 已选模型不可用时，供分析师明确确认后仅在本次运行使用 |
| `DEEPSEEK_MODEL` | 否 | 旧版兼容键；迁移后等同质量档，不建议新配置继续使用 |
| `DEEPSEEK_BASE_URL` | 否 | 使用兼容 API 地址 |
| `TAVILY_API_KEY` | 建议 | 事件证据的高质量公开检索与清洗正文 |
| `SEARCH_PROVIDER` | 否 | `tavily`（默认）或 `bing` |
| `SEARCH_DEPTH` | 否 | Tavily `basic` 或 `advanced`；后者消耗更多 credits |
| `SEARCH_COUNTRY` / `SEARCH_LANGUAGE` | 否 | 搜索结果的国家与语言排序偏好 |
| `SEARCH_BING_FALLBACK` | 否 | Tavily 不可用、额度不足或无结果时是否使用 Bing RSS |
| `IFIND_ACCOUNT` / `IFIND_PASSWORD` | 建议 | Research Helper 研究取数 |
| `IFIND_SDK_PATH` | 发布版建议 | 官方 `iFinDPy` 所在目录；应用也会检查常见 Anaconda/Python 安装 |
| `RESEARCH_HELPER_GUI_PYTHON` | 否 | 指定安装了 PySide6 的桌面端解释器 |
| `OPTIONHELPER_SKILL_ROOT` | 报价时 | OptionHelper Skill 根目录 |
| `OPTIONHELPER_PYTHON` | 报价时 | OptionHelper 独立解释器 |
| `OPTIONHELPER_SELECTION_PATH` | 否 | 一次性待报价 selection 路径 |

OptionHelper 使用的 iFinD Refresh Token 由其就绪检查流程保存至本地 `.optionhelper/`，不写入
`config.local.json`，也不与 Research Helper 的 iFinD 账号密码混用。

模型名称不再由版本代码写死。桌面端“LLM 设置”可通过当前 API Key 调用 DeepSeek
`GET /models` 刷新账号真实可用目录，并分别选择快速档、质量档和备用档。刷新后，仍有效的选择保持不变；已经
下线的模型会在表单中改为当前目录内的合适候选，并明确列出替换关系，只有研究员点击“保存设置”后才长期生效。
保存时若填写的模型不在刚取得的实时目录中，应用会拒绝保存，避免一个已下线 ID 长期触发备用档。每次研究启动前
仍会重新校验目录：若模型在运行前才下线，应用会询问是否仅在本次运行中使用备用模型；拒绝则停止。设置页还可
对质量档发起一次最小真实调用，运行日志同时记录请求模型和接口回报的实际模型，便于识别供应商的别名路由。
最近一次成功模型目录缓存在被 Git 忽略的
`data_cache/deepseek_models.json`，只在目录接口短暂不可达时用于提示和预检。

## 构建 Windows 发布包

维护者在干净的 Windows 构建环境执行：

```powershell
python -m pip install -r requirements-release.txt
python tools/build_release.py --installer
```

构建脚本会先运行 `tests/`，再生成 PyInstaller `onedir` 程序、SHA-256 文件清单和 ZIP；
如果检测到 Inno Setup 6，还会生成安装器。输出位于 `dist/`。构建器会检查发布目录，拒绝把
`config.local.json`、`sources/`、`output/` 或 `.optionhelper/` 混入发布包。

发布前还应在一台没有源码环境的 Windows 机器完成最小验收：首次设置、普通行业研究、事件检索、
HTML/PDF 导出，以及（有权限时）一只标的的 OptionHelper 正式报价。代码测试通过不等于外部账号、
SDK 授权和网络条件已经通过。

## 快速开始

### 桌面应用

推荐使用桌面界面完成完整工作流：

```powershell
python gui/start.py
```

桌面端提供客户需求与约束输入、补充材料上传/粘贴、研究口径确认、主题篮子勾选、事件证据自动查找与管理、候选逻辑审核、
运行进度、交付预览、结构化报告编辑、历史运行以及正式报价队列。报告完成后可点击“编辑报告”修改标题、核心结论、
策略逻辑标题/正文、图表标题和挂钩说明，并调整论点顺序、隐藏论点或删除单张图表；正式报价、图表数据、来源、日期和免责声明保持锁定。
编辑窗口顶部固定“保存修订版 HTML”“保存并导出 PDF”和“打开文件夹”；保存/导出会显示完整文件路径。删除两图中的一张后，若剩余图适合半栏展示，正文与图自动改为左右排版；宽图继续整行展示。人工修订不会覆盖自动生成稿，导出 PDF 后仍重新执行真实一页校验。客户需求框支持完整多行输入、自动换行和滚动；研究确认页会在每个
字段旁说明其用途及影响的流程，避免把研究取数目标误解为正式挂钩标的。“搜索设置”可由每位研究员在本机填写
Tavily API Key、选择基础/高级搜索、国家/语言偏好、测试连接并控制 Bing RSS 兜底；Key 不会回显或写入仓库。

启动后直接进入“研究输入”，右侧页面通过顶部“研究输入 / 报告交付 / 内部复核”切换，不再把表单和报告
压在同一个分屏。左侧历史任务栏可收起，按客户问题合并同题重跑、支持搜索；选中任务后点“重命名”或右键可
修改侧栏标题。自定义标题单独保存在本机，不改客户原文、运行摘要和报告；搜索仍能匹配原始需求。点击任务优先
打开最近一次可用报告及其内部底稿。历史视图默认只读；如要修改旧报告，可主动点击“编辑此报告”，修订层和
运行记录归于该次历史任务，不切换当前研究或报价队列。需要重做研究时须主动把旧需求带回输入框。
研究、报价、PDF 校验仍由原有桌面流程执行。

### 命令行

生成一份研究报告：

```powershell
python main.py -b "近期黄金价格波动加大，客户想了解黄金 ETF 的投资机会。" --confirm-market
```

人工选择正文主轴并导出 PDF：

```powershell
python main.py -b "光模块需求上修，分析相关投资机会。" --confirm-market --pick --pdf
```

指定客户约束：

```powershell
python main.py -b "分析消费电子板块的投资机会。" --confirm-market `
  --horizon 6个月 `
  --max-loss 20% `
  --principal-fluctuation yes `
  --return-preference "更偏上涨参与"
```

所有研究都需取数目标确认。GUI 自动弹窗；CLI 使用 `--confirm-market`，按提示输入确认 JSON。
未启用或未完成确认时停止，不静默采用系统建议。例如：

```powershell
python main.py -b "分析港股互联网板块的投资机会。" --confirm-market
```

扫描市场并生成候选主题：

```powershell
python main.py
```

根据扫描结果生成指定主题：

```powershell
python main.py 1 4 --confirm-market
```

### 常用参数

| 参数 | 说明 |
|---|---|
| `-b`, `--brief` | 输入客户需求 |
| `--pick` | 由分析师选择正文候选论点 |
| `--confirm-market` | CLI 研究必需：接收分析师取数目标确认；扫描选题也逐个确认 |
| `--overrides <json>` | 导入人工数据补充文件 |
| `--pdf` | 导出 PDF 并执行真实页数校验 |
| `--optionhelper recommend` | 仅运行结构推荐，不正式报价 |
| `--optionhelper quote` | 使用已确认的一次性 selection 正式报价 |
| `--horizon` | 客户期限 |
| `--max-loss` | 最大损失比例，范围 0%–100% |
| `--principal-fluctuation yes\|no` | 是否接受本金波动 |
| `--return-preference` | 客户收益偏好原话 |

## 研究取数路径

确认页中的“研究取数路径”提供三个互斥选项：

1. **标准行业**：系统使用数据源认可的行业节点及其成分股。无需人工勾选公司，也不要求填写 ETF。
2. **人工主题篮子**：系统只使用分析师勾选的公司。客户点名公司单独标注；系统建议与 iFinD 概念股仅作为待核实候选，不会因代码、简称真实就自动勾选。确认页展示可取得的业务关联原文及来源；没有可核实原文的公司须由分析师填写具体关联理由和资料来源后才能选入。适合光模块、汽车电子等细分或跨行业主题。
3. **主题 ETF**：系统读取 ETF 的真实跟踪指数成分。ETF 成分无法核验时不会回退到宽行业或单只代表股。

需求解析确认页只决定研究数据从哪里来，不提前确定正式挂钩标的。在标准行业和人工主题篮子路径中，客户点名或
系统发现的 ETF 只保留在研究完成后的待报价池，不会覆盖研究篮子；只有分析师明确选择“主题 ETF”路径时，所选
ETF 才会作为研究取数目标。客户同时点名 ETF 和个股时，个股进入待确认主题篮子，ETF 保留为后续报价候选。

### ETF 主题暴露校验

候选展示和最终提交共用同一套主题暴露判定，并显示官方基金名称、跟踪指数、可取得的主要成分和判定依据：

- **直接暴露**：官方事实直接命中研究主题，可继续真实性、指数和流动性校验；
- **部分暴露**：仅覆盖较宽行业或相邻产业链。分析师必须填写映射理由并勾选确认后继续；
- **不相关**：无法由人工理由绕过，需更换 ETF。

证券真实性、跟踪指数真实性和近 20 日流动性是独立硬条件，任何人工确认均不能绕过。若主要成分因权限或网络
暂未取得，应用会显示缺口，并在主题 ETF 的真实成分取数阶段再次核验。

ETF 的近 20 日日均成交额硬门槛为 **0.1 亿元**；低于 1 亿元仅标记为非优选，并提示正式询价前结合名义本金
复核冲击成本，不会因未达到 1 亿元而自动排除客户指定 ETF。

## 补充材料与人工数据

- `sources/`：存放当次研究使用的 PDF、TXT、Markdown 或 DOCX 材料。
- 桌面端“补充材料”支持上传文件，也支持粘贴正文并单独填写来源和原材料发布日期。
- 材料日期是客户报告准入硬条件：日期未知、日期在未来或超过 90 天的材料在抽取前被拦截，不进入候选论点和
  客户报告。系统不会把文件修改时间或录入时间当成发布日期，也不会自动删除原文件；被拦材料仍留在 `sources/`
  供内部复核或人工归档。
- 需求解析把触发题分成两类：`明确外部事件`（IPO、业绩/指引、政策、产品发布、事故、并购等）和`市场状态触发`（板块回调、风格轮动、估值切换、成交或资金状态变化）。只有前者启用事件证据硬门；后者使用当前行情、历史类似区间及持续/失效条件，不要求公司公告式证据包。
- 桌面端可勾选“按明确外部事件处理”。勾选后，本次运行不再依赖 LLM 的事件路径判断，事件证据区域会高亮提示，并强制进入事件事实、产业机制、A 股暴露与传导链审核；市场状态型题目不要勾选。
- 明确外部事件采用两阶段检索。研究对象确认前，“管理事件证据”只查事件主体发生了什么及该变量通过什么产业机制产生影响；分析师确认行业、公司篮子或主题 ETF 后，系统自动使用这个已确认对象查找 A 股暴露，并将三类原文组合为候选传导链。不会在研究对象尚未确定时让 LLM 猜 A 股公司或 ETF。
- 已上传材料、事件事实、产业机制和 A 股暴露各有独立原文配额；每个公开检索通道最多读取 6 份正文，并至少覆盖
  两组不同检索意图，避免第一条宽查询独占全部名额。第二阶段会沿用分析师第一阶段已经确认的事实与机制，
  不要求重复勾选。
- 事件事实、产业机制或 A 股暴露未形成候选时，系统会分别追加对应的定向补检；事实补检优先事件主体的官方公告、
  交易所披露、财报/指引或产品发布，机制和暴露补检则使用各自的关系与已确认研究对象。纯行情、走势图和报价页会
  被过滤，不作为证据候选。扩大检索覆盖会增加搜索调用及 Tavily credits，实际调用数仍完整写入审计。
- 自动查找窗口显示 0–100% 进度和当前动作，包括检索词规划、逐条公开检索、正文读取、逐字分类、定向补检与组合传导链；检索在独立进程执行，界面不会假死并可随时停止。
- 默认使用 Tavily Search API 返回候选网址与清洗正文；Tavily 不可用、额度不足或无结果时可自动回退 Bing RSS。基础搜索每次通常消耗 1 credit，高级搜索每次通常消耗 2 credits，实际消耗以 Tavily 返回的 usage 为准。
- 事件事实检索会给事件主体增加精确名称锚点；对存在同形品牌或中英文名称的主体同时使用确认别名，并在读取网页前验证标题、摘要或清洗正文确实出现该主体。主体不匹配结果不会进入正文读取和 LLM 分类。
- 搜索服务、三路检索词、命中网址、正文读取结果、淘汰原因、调用次数和 credits 会写入事件证据的内部检索审计；API Key 不进入审计或运行日志。
- 自动检索候选默认不选。LLM 返回的引文必须逐字存在于对应原文中；没有来源、链接或原文校验失败的内容不会展示。
- LLM 可将已验证的“事实 ID＋机制 ID＋暴露 ID”组合成候选传导链，但不得补充新事实或新数字。审核窗口显示三路检索词、各通道读取数、候选分类、引用 ID、方向、置信度与边界；勾选组合链时会同时采用其引用原文。
- 每条组合链同时标记“影响分支”和“分支用途”。正文最多采用一个主方向和一个备选方向；其他已核验分支保留在内部审计，不进入一页通。分析师可以在证据窗口直接调整分支用途。
- 分析师也可在证据包的四个页签中补充三类原文，再通过证据 ID 手工组合传导链；来源、影响分支、用途和组合边界会保留在本次运行中。
- 启动自动查找前会进行外发确认：检索词将发送到公开搜索服务，读取到的公开原文将发送给配置的 LLM 分类。
- 分析师确认后的候选才进入事件证据硬门。硬门接受两种方式：一段可直接证明“事件→A股对象”的原文；或完整的“事件事实＋产业机制＋A股暴露＋确认后的组合链”。不会把模型常识当成证据。
- 通过硬门的主事件分支（以及可选的一个备选分支）会作为独立候选进入正文逻辑选择器，而不是只用于准入校验；明确外部事件报告至少选择一条事件传导主轴，自动建议、桌面审核和后端执行同一约束。
- 公开检索失败不会导致应用退出；仍可从已上传材料导入原文、粘贴文字或手工录入。
- `--overrides` 仅用于补充自动数据源无法取得的展示或解释字段；人工值不能触发机械论点。

材料内容、客户提供事实和系统推断会分开标记。无法核验的客户事实可以作为“客户提供”保留，但不得伪装成公开数据。

## 产品推荐与正式报价

### OptionHelper 版本与升级

Research Helper 调用**构建后的 OptionHelper Skill**，不直接调用源码仓库或桌面 App。
`OPTIONHELPER_SKILL_ROOT` 应指向同时包含 `SKILL.md`、`scripts/tool_entry.py` 和
`scripts/environment_check.py` 的目录；`OPTIONHELPER_PYTHON` 是为该版本明确选择并验证的解释器。

升级采用独立安装，避免源码更新立即影响正在使用的版本：

1. 确认 OptionHelper 源码工作区干净，再执行 `git pull --ff-only`，记录完整提交号。
2. 保留旧 Skill 与解释器，为新版本建立独立环境，经授权安装 `core/requirements.lock`。
3. 用 `packaging/skill/build_skill.py --output <独立目录> --catalog-version <发行版本>` 构建；
   若缺少已签发知识源归档，只能使用 `--candidate` 构建开发候选，不得标称正式发行。
4. 执行 Skill 验收、`tools/optionhelper_smoke.py <Skill目录>` 和本项目测试，再激活。

激活与回退命令均使用已选解释器的绝对路径：

```powershell
& 'C:\path\to\selected\python.exe' tools/optionhelper_install.py --activate 'C:\path\to\option-helper' --source-commit '<完整提交号>'
& 'C:\path\to\selected\python.exe' tools/optionhelper_install.py --rollback 'C:\path\to\Research Helper\.optionhelper\migrations\<迁移记录>'
```

激活前先在 Windows 上恢复候选 Skill 的 ACL 继承，并仅向 Research Helper 工作区所有者授予读取/执行权限；随后
校验文件哈希、依赖、外部 Store、本机数据 API 配置、`tool_entry.py --list` 以及与正式推荐相同的模块导入方式。
权限修复或任一检查失败时不切换配置。成功后备份旧 Skill、配置和解释器记录，仅更新本地集成路径。回退保留后来
修改的其他设置。版本目录和迁移记录位于被 Git 忽略的 `.optionhelper/`；
备份可能包含 API 配置，不应外发。切换后必须重启 Research Helper，已经运行的进程不会自动换版。

离线 smoke 测试使用模拟模型和真实产品目录，验证双标的推荐与回执协议，不联网、不生成真实报价。
上线使用前仍应人工验证一次实际取数、定价、报价预览和底稿写回。

#### 自动检查现状

现有检查已经合并到只读入口 `tools/optionhelper_check_update.py`。默认读取当前激活回执，比较
`D:\download\OptionHelper` 的候选提交，构建临时 technical candidate，依次运行 Skill 完整性、统一就绪、
工具目录、离线兼容 smoke 和 Research Helper 全量回归，最后在 `output/optionhelper-update-check-<时间>/`
生成 Markdown 总报告和 JSON 结构化结果。

使用 Research Helper 日常解释器执行：

```powershell
& 'C:\path\to\research-helper-python.exe' tools/optionhelper_check_update.py
```

源码仓库、候选独立解释器或预构建 Skill 不在默认位置时可显式指定：

```powershell
& 'C:\path\to\research-helper-python.exe' tools/optionhelper_check_update.py `
  --source 'D:\download\OptionHelper' `
  --candidate-python 'C:\path\to\candidate-environment\Scripts\python.exe' `
  --candidate-skill 'C:\path\to\built-option-helper'
```

省略 `--candidate-skill` 时，脚本自动从当前源码构建临时候选；省略 `--candidate-python` 时，沿用当前激活的
OptionHelper 解释器进行兼容检查，但不会为新版安装或升级依赖。若锁定依赖已经变化，就绪检查会失败并在报告中
列出差异。`--skip-project-tests` 仅供快速定位，使用后报告标为“不完整”，不能据此激活。

| 检查项 | 当前入口 | 是否会切换版本 |
|---|---|---:|
| Skill 文件、入口和哈希完整性 | 一键脚本调用安装模块的只读校验 | 否 |
| Python、锁定依赖、iFind 与外部 Store | 一键脚本调用 `environment_check.py` | 否 |
| Research Helper ↔ OptionHelper 推荐协议、双标的隔离与回执 | 一键脚本调用 `optionhelper_smoke.py` | 否 |
| Research Helper 全量回归 | 一键脚本优先运行 `pytest`；不可用时明确标记覆盖不完整 | 否 |
| 新旧源码提交和接口差异 | 一键脚本读取激活回执并运行 `git log/diff` | 否 |
| 真实行情、定价和客户报价结果 | 分析师小规模端到端验收 | 否 |

因此“自动检查”是**脚本按固定规则给出通过/失败**，不是后台自动拉取、自动安装或自动上线。现有
`optionhelper_install.py --activate` 是带保护的最终激活入口，不是纯检查命令：它先校验 Skill 哈希、统一就绪状态及
`tool_entry.py --list` 和模块导入，同时修复由构建账户带入的 Windows 只读访问权限；任一步失败都不会修改当前配置，
全部通过后才备份旧版并切换路径。不要手工修改 `config.local.json` 指向新目录来绕过激活入口。

一键脚本内部等价于依次执行以下不切换版本的检查；保留这些命令用于单项排错：

```powershell
$skill = 'C:\path\to\built-option-helper'
$ohpy = 'C:\path\to\candidate-environment\Scripts\python.exe'
$project = 'C:\path\to\research helper'

# 1. OptionHelper 环境、依赖、iFind 和 Store 就绪检查
& $ohpy "$skill\scripts\environment_check.py" `
  --check-readiness --skill-root $skill --project-root $project

# 2. Research Helper 对候选 Skill 的离线兼容 smoke
& $ohpy "$project\tools\optionhelper_smoke.py" $skill

# 3. Research Helper 全量回归；使用项目日常测试解释器
& 'C:\path\to\research-helper-python.exe' -m pytest -q tests
```

源码更新后还应比较“当前激活回执中的 `source_commit`”与候选提交，重点审阅 `SKILL.md`、
`core/tool_entry.py`、Recommender 的 models/ports/service、锁定依赖、报价与 Reporter 数据契约。技术检查通过后，
仍需分析师完成一次真实小规模报价，核对标的、结构、期限、估值日、条款、报价表、预览和底稿是否属于同一次运行。
只有完成这一步，才执行前述 `--activate`；激活并重启应用后再做一次最小验收。检查报告返回
“技术通过，待人工业务验收”不等于已经获准上线。当前机制不会自动追随源码仓库更新。

### 使用流程

研究完成后，若勾选“研究完成后准备正式报价审核”，应用会统一弹出待报价标的选择框；未勾选时也可稍后从
“确认待报价标的”手动进入。产品流程独立运行，不会重新解析需求或重跑研究：

1. 分析师确认待报价 ETF/个股；
2. Research Helper 为每只标的生成产品画像；
3. OptionHelper 根据共同研究观点、客户约束和当前标的画像生成结构候选；
4. 分析师确认采用的候选结构；
5. OptionHelper 自行取得现价、定价波动率、利率、分红率和交易日历并生成正式报价；
6. 多份报价完成后，由分析师勾选写入一页通的项目。

内部底稿中的“共同市场观点”只是研究完成时冻结的可复用快照，不等于已经调用 OptionHelper。研究完成后，
每只经分析师确认的报价标的都会另行记录实际发送文本及产品画像；结构推荐完成、正式报价尚未发起、正式报价完成
正式报价失败和“正在同步报告/PDF”是不同状态，不能互相替代。OptionHelper 返回报价后，任务进入 `finalizing`；
若 HTML/PDF 的异步加载或打印回调 45 秒仍未返回，系统结束等待并将 PDF 标记为未校验，而不是让任务永久显示
`running`。已生成的报价结果和 HTML 仍保留，分析师可单独重试 PDF 交付。

待报价池遵循明确优先级：客户点名一个或多个证券时，只展示客户范围；客户未点名且存在主题 ETF 研究目标时，
默认只展示该研究目标。系统动态发现的其他同主题 ETF 收在“比较其他同主题工具”入口中，分析师主动展开并勾选后
才进入多标的审核；没有研究 ETF 时，默认展示系统候选第一名，其余候选同样折叠。候选发现不等于已通过
OptionHelper 推荐门槛。相同代码的多条记录会合并，并保留正式名称、具体发现说明和优先级最高的来源角色。

初始研究版 HTML/PDF 不显示“挂钩标的”卡片。只有分析师在研究完成后确认标的并取得正式报价后，应用才把该标的
及其关联理由写入一页通；系统自动发现的 ETF 不会再以“已确认挂钩标的”的口吻提前出现在报告中。

产品画像包括近 20/60 日收益、20 日实现波动率及历史分位、近一年最大回撤、历史情景收益带和流动性。
这些数据用于推荐前理解标的，不替代 OptionHelper 的正式定价参数。

## 输出文件

所有运行结果写入 `output/`：

| 文件 | 用途 |
|---|---|
| `onepager_<主题>.html` | 一页通 HTML；屏幕查看时支持本地交互图 |
| `onepager_<主题>.pdf` | 正式 PDF；仅实测一页时通过交付校验 |
| `onepager_<主题>_人工修订.json` | 人工修改字段、论点顺序、逻辑与单图显隐状态和修订时间；不保存整份自由 HTML |
| `onepager_<主题>_人工修订.html` | 自动稿应用人工覆盖层后的客户版 HTML |
| `onepager_<主题>_人工修订.pdf` | 修订后重新导出的 PDF；仍须实测一页才可正式交付 |
| `onepager_<主题>_内部底稿.md` | 研究口径、数据缺口、证据出处和产品调用记录 |
| `*_内部交互复核.html` | 主题篮子、历史序列和 ETF 候选的内部交互复核页 |
| `output/runs/<run_id>.json` | 运行摘要、阶段状态、产物和恢复建议 |
| `output/runs/<run_id>.jsonl` | 逐事件运行日志 |
| `*.optionhelper-handoff.json` | 冻结的 OptionHelper 研究观点包 |
| `*.product-profile-<代码>.json` | 每只待报价标的的产品画像 |

`output/`、`sources/`、`references/`、本地缓存和凭证均被排除在版本控制之外。

## 图表与交付质量

- 图型按数据关系选择：时间序列用折线/面积图；横向排名可用排序条或棒棒糖图；两个可比时点用
  分组柱或哑铃图；可加总拆解用瀑布图；同口径区间用区间带；同篮子标准化扫描用热力图；构成用
  矩形树图；同一样本的三变量用气泡图。
- 不同量纲、不同业务含义的单点指标不会被拼成“趋势图”，必要时降级为数据卡。
- 哑铃、瀑布、区间带、热力、矩形树图和气泡图均有对象、单位、时点、可加总性或标准化硬校验；
  数据关系不成立时不会为视觉效果强行出图。
- 每张图保留明确的数据截至日、单位、样本口径及“对象＋指标”式简洁图题；图表分析结论单独保留在内部规格，
  不覆盖图题。没有明确日期的数据不会进入客户图表。
- 报告标题下显示“研究策略·YYYY年M月D日”的出具日期，不显示“报告生成时间”或统一的“数据查询日”；各张图、采用来源及页脚继续保存实际数据日期；
  正文按需要说明财报期或统计区间，内部底稿逐字段保留实际日期。
- 核心结论以投资判断为主，建议 120–180 字、最多两项关键数据；事件分析自然说明驱动、产业影响、市场定价和
  兑现条件，不固定套用“事件事实层面/产业机制层面/A股暴露层面”。证据对应问题单独进入内部审核备注，重大业务风险仍应披露。
- 来源栏汇总 iFinD、已选材料、采用的事件事实/机制/A股暴露原文及人工数据出处；公开事件原文保留可点击链接，
  不将未选分支或检索淘汰材料作为正文来源。
- 客户版不显示 F/M/E/C 等内部证据编号、分支角色或高/中/低可信度标签；这些审核信息连同完整证据组合仅保留在
  内部底稿。客户正文只呈现经确认的事实、来源、传导关系、适用边界和待验证事项。
- 客户版不输出“见底稿”等内部占位；数据卡按卡片数量使用固定画布和近白浅粉底色，
  长文本只调整字号，不改变整图尺寸。
- HTML 使用本地 ECharts 交互层；PDF/打印使用同一数据生成的静态图。
- 单张紧凑横截面图会自动采用“文字左、图表右”的版式；时间序列、多系列图、表格和证据链保持整行，
  以避免压缩坐标、图例或长标签。
- 报告与图表共用浅色酒红—浅金主色：`#7D0A0A`、`#BF3131`、`#EAD196`、`#EEEEEE`。多系列图按
  同明度阶梯使用红（核心）、蓝（对照）和绿（验证/改善）；浅色只用于面积、区间与注释底色。
- 正文数字必须能够回溯到数据字段或材料出处；无法回溯的数字会进入校验问题。
- PDF 只有在真实渲染结果为一页时才通过正式交付校验。

## 项目结构

```text
main.py                     命令行入口与端到端编排
gui/                        PySide6 桌面应用
core/                       需求解析、取数、研究、校验和 OptionHelper 桥接
llm/                        LLM 客户端
render/                     图表、HTML、PDF 与内部底稿
tests/                      自动化测试
THESIS_LIBRARY.md           机器可读论点库
underlying_map.json         行业/主题与常用工具映射
sources/                    当次补充材料工作区
references/                 内部版式参考，不参与自动研究
output/                     生成结果和运行日志
data_cache/                 本地数据缓存
```

## 开发与测试

运行完整测试：

```powershell
python -m unittest discover -s tests -v
```

提交前建议同时执行：

```powershell
python -m compileall -q main.py gui core render
git diff --check
```

新增能力时应同步更新测试和相关项目文档。历史修改记录不在 README 中重复维护。

## 常见问题

### DeepSeek 网页可用，但 API 无法访问

网页会话和 `https://api.deepseek.com` 是不同链路。请确认 API Key、`DEEPSEEK_BASE_URL`、VPN/代理策略和 443 端口。
Research Helper 的部分国内数据源会使用直连模式，系统代理可用不代表所有 API 都可达。

### iFinD 取数失败

确认 iFinD 终端/SDK 已安装、`iFinDPy` 可导入、账号密码有效，并检查周度额度。系统会缓存静态或慢变数据；
关键数据缺失时不会以代表个股或宽行业静默替代。

### 研究完成但没有正式报价

研究完成与报价完成是两个状态。请检查是否已确认待报价标的、OptionHelper 是否通过就绪检查、是否存在未消费的
`selection.pending.json`，以及报价任务区显示的具体失败阶段。

### HTML 正常但 PDF 未通过

正式 PDF 需要 PySide6/Qt WebEngine，并且必须实测为一页。报价写回后系统会重新生成并再次校验，不会沿用报价前 PDF。

### HTML 交互图未显示

确认 `assets/vendor/echarts.min.js` 与报告保持在项目目录关系中。即使交互层不可用，PDF 和打印仍应保留静态图。

## 安全与合规

- 不要提交 `config.local.json`、`.env`、`.optionhelper/`、`data/`、`result/`、客户材料或生成报告。
- 日志不得记录 API Key、密码、Refresh Token、请求头或完整外部敏感响应。
- 客户材料、第三方研报和正式报价文件仅应保存在授权目录，并遵循机构的数据保留政策。
- 分发桌面程序时不要内置个人凭证；多人使用应通过公司受控后端统一鉴权、限流和审计。

## 相关文档

- [THESIS_LIBRARY.md](THESIS_LIBRARY.md)：论点库与机器判定规则
