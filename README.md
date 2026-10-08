# Research Helper

Research Helper 是面向投研分析师的桌面应用，用于将客户需求、市场数据和研究材料整理为可复核的《场外衍生品投资策略》一页通。Windows 与 macOS 使用同一套研究流程，并分别构建独立发布包；系统覆盖需求解析、研究取数、论点审核、图表与报告生成、结构化编辑，以及可选的 OptionHelper 产品推荐和正式参考报价。

> 本项目用于内部研究与信息整理。生成内容不构成投资建议、销售要约或交易承诺。

## 核心功能

- 解析客户问题、期限、风险约束及客户点名的 ETF/个股。
- 在取数前统一确认研究主题、市场和研究取数目标。
- 支持标准行业、人工主题篮子和主题 ETF 三种取数路径。
- 支持普通行业研究、市场状态研究和明确外部事件研究。
- 读取 iFinD 市场数据、上传材料、粘贴文字及公开网页证据。
- 由分析师审核候选论点、主题公司、事件证据和待报价标的。
- 生成一页通 HTML/PDF、内部底稿、交互复核页和运行日志。
- 支持结构化报告编辑、历史任务搜索与重命名。
- 为每只待报价标的生成产品画像，并接入 OptionHelper 推荐和正式报价。

## 工作流程

```text
客户需求与产品约束
        ↓
需求解析
        ↓
确认研究取数目标
  ├─ 标准行业成分
  ├─ 人工主题公司篮子
  └─ 主题 ETF 真实指数成分
        ↓
市场数据、研究材料与事件证据
        ↓
候选论点审核
        ↓
一页通 HTML/PDF、内部底稿与运行记录
        ↓
确认待报价标的
        ↓
标的产品画像 → OptionHelper 推荐 → 人工确认 → 正式报价
        ↓
选择报价写入一页通
```

## 关键概念

| 对象 | 作用 | 示例 |
|---|---|---|
| 研究主题 | 确定报告需要回答的问题 | 光模块、汽车电子、固态电池 |
| 标准行业 | 数据源可验证并取得成分股的行业节点 | 通信设备、电力设备、消费电子 |
| 人工主题篮子 | 分析师确认的主题公司集合 | 中际旭创、新易盛、天孚通信等 |
| 主题 ETF | 使用 ETF 官方跟踪指数的真实成分开展研究 | 机器人 ETF、消费电子 ETF |
| 挂钩标的 | 用于产品推荐和正式报价的证券 | ETF、指数、客户点名个股 |
| 标的产品画像 | 推荐前整理的收益、波动、回撤、情景收益和流动性 | 每只待报价标的单独生成 |

研究取数目标决定报告分析哪些数据；挂钩标的决定产品对什么证券报价。两者分别确认并独立留痕。

## 系统要求

### Windows发布版

- Windows 10/11；
- DeepSeek API Key；
- iFinD 官方终端/Quant SDK、账号及数据权限；
- Tavily API Key（建议，用于公开资料搜索）；
- 已验收的 OptionHelper Skill 与独立 Python 环境（正式报价需要）。

### macOS发布版

- macOS 12或更高版本；
- DeepSeek API Key；
- iFinD HTTP API权限与Refresh Token；
- Tavily API Key（建议，用于公开资料搜索）；
- macOS可用且已验收的OptionHelper Skill与独立Python环境（正式报价需要）。

### 源码开发

- Python 3.10 或更高版本；
- `requirements-release.txt` 中列出的依赖；
- iFinD 官方提供的 `iFinDPy`。

## 安装

### 安装Windows发布版

优先使用 `ResearchHelper-Setup-<版本>.exe`。没有安装器时，可解压 `ResearchHelper-<版本>-win64.zip` 并运行 `ResearchHelper.exe`。

首次启动按设置向导完成：

1. 填写 DeepSeek API Key；
2. 按权限配置 Tavily、iFinD 和 OptionHelper；
3. 运行“环境检查”；
4. 确认研究、搜索、行情和报价能力状态。

发布版程序文件与用户数据分开保存。用户数据默认位于：

```text
%LOCALAPPDATA%\ResearchHelper\
├─ config.local.json
├─ sources\
├─ data_cache\
├─ history_titles.json
├─ output\
├─ data\
├─ result\
└─ .optionhelper\
```

卸载程序默认保留用户数据。

### 安装macOS发布版

打开`ResearchHelper-<版本>-macOS.dmg`，将`ResearchHelper.app`拖入“应用程序”。首次启动完成DeepSeek、
Tavily和iFinD Refresh Token设置，再运行“环境检查”。macOS用户数据位于：

```text
~/Library/Application Support/ResearchHelper/
```

Windows安装包、Inno Setup脚本和iFinDPy SDK流程继续独立维护；Mac发布物不会覆盖或替换Windows版本。

### 从源码运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-release.txt
python gui/start.py
```

`iFinDPy` 由 iFinD 官方终端或 SDK 安装程序提供。安装后可检查：

```powershell
python -c "import iFinDPy; print(iFinDPy.__file__)"
```

桌面端通过 `gui/start.py` 启动。VS Code Jupyter Interactive 模式不适合作为应用启动入口。

## 配置

Windows发布版配置写入`%LOCALAPPDATA%\ResearchHelper\config.local.json`；macOS发布版写入
`~/Library/Application Support/ResearchHelper/config.local.json`；源码版写入项目根目录的
`config.local.json`。环境变量优先于本地配置。配置修改后重启应用。

| 配置项 | 用途 |
|---|---|
| `DEEPSEEK_API_KEY` | 需求解析、研究规划、写作和结构推荐 |
| `DEEPSEEK_FAST_MODEL` | 需求解析、材料抽取和证据分类 |
| `DEEPSEEK_QUALITY_MODEL` | 研究规划、正文生成和结构推荐 |
| `DEEPSEEK_FALLBACK_MODEL` | 已选模型下线时的单次运行备用模型 |
| `DEEPSEEK_BASE_URL` | DeepSeek 兼容 API 地址 |
| `TAVILY_API_KEY` | 公开资料搜索与清洗正文 |
| `SEARCH_PROVIDER` | `tavily` 或 `bing` |
| `SEARCH_DEPTH` | Tavily `basic` 或 `advanced` |
| `SEARCH_COUNTRY` / `SEARCH_LANGUAGE` | 搜索排序偏好 |
| `SEARCH_BING_FALLBACK` | Tavily 无结果或不可用时启用 Bing RSS |
| `IFIND_ACCOUNT` / `IFIND_PASSWORD` | Research Helper 研究取数 |
| `IFIND_SDK_PATH` | iFinD 官方 SDK 路径 |
| `IFIND_REFRESH_TOKEN` | macOS/Linux通过iFinD官方HTTP API研究取数 |
| `IFIND_API_BASE_URL` | iFinD官方HTTP API地址，通常无需修改 |
| `OPTIONHELPER_SKILL_ROOT` | 构建并验收通过的 OptionHelper Skill 根目录 |
| `OPTIONHELPER_PYTHON` | OptionHelper 独立解释器 |
| `OPTIONHELPER_DEFAULT_CONSTRAINTS` | 默认期限、最大损失和本金波动约束 |

示例：

```json
{
  "DEEPSEEK_API_KEY": "your-api-key",
  "DEEPSEEK_FAST_MODEL": "deepseek-flash",
  "DEEPSEEK_QUALITY_MODEL": "deepseek-flash",
  "DEEPSEEK_FALLBACK_MODEL": "deepseek-flash",
  "TAVILY_API_KEY": "tvly-your-api-key",
  "SEARCH_PROVIDER": "tavily",
  "SEARCH_DEPTH": "basic",
  "SEARCH_BING_FALLBACK": true,
  "IFIND_ACCOUNT": "your-account",
  "IFIND_PASSWORD": "your-password",
  "IFIND_SDK_PATH": "C:/path/to/ifind/sdk",
  "IFIND_REFRESH_TOKEN": "your-refresh-token",
  "OPTIONHELPER_SKILL_ROOT": "C:/path/to/option-helper",
  "OPTIONHELPER_PYTHON": "C:/path/to/optionhelper/python.exe"
}
```

`config.local.json` 已加入 `.gitignore`。个人凭证不得写入源码、测试或发布包。

### LLM 模型

桌面端“LLM 设置”通过 DeepSeek `GET /models` 读取账号当前可用模型，并分别配置快速档、质量档和备用档。运行前再次检查模型目录；模型临时下线时，由分析师确认是否仅在本次运行使用备用模型。运行日志记录请求模型和接口实际返回模型。

### 公开搜索

Tavily 是主要公开搜索入口，可选择基础或高级搜索；Bing RSS 提供免费兜底。设置页支持 API Key、国家/语言偏好、连接测试和兜底开关。搜索服务、检索词、命中网址、正文读取和候选淘汰原因写入内部审计，API Key 不进入日志。

## 使用桌面应用

### 1. 输入需求

粘贴客户完整问题，填写期限、最大损失、本金波动和收益偏好。补充材料可上传 PDF，也可粘贴正文并填写来源和原材料日期。

### 2. 确认研究取数目标

所有研究在取数前均进入确认页。三条路径互斥：

1. **标准行业**：使用数据源认可的行业节点及其成分股；
2. **人工主题篮子**：使用分析师勾选并完成证券身份和业务关联审核的公司；
3. **主题 ETF**：使用所选 ETF 官方跟踪指数的真实成分。

人工主题篮子的系统候选按以下流程产生：

```text
LLM 拆解产业链环节并规划检索词
        ↓
Tavily/Bing 检索公开正文
        ↓
从正文中逐字抽取公司名和业务片段
        ↓
iFinD 核验 A 股代码和官方简称
        ↓
分析师逐只确认
```

iFinD“XX 概念股”仅在公开资料召回不足时补充候选。系统发现的公司默认不勾选。

### 3. 审核研究内容

系统依据市场数据、补充材料和事件证据生成候选论点。分析师确认正文主轴后进入报告生成。

明确外部事件报告会分别搜索：

- 事件事实：发生了什么；
- 产业机制：供需、价格、技术或竞争格局如何变化；
- A 股暴露：本次研究公司、行业或 ETF 与事件的关系。

候选证据需要保留原文、来源和链接，并由分析师确认。LLM 负责检索规划、分类和组合，不补充原文中没有的事实或数字。

### 4. 检查并编辑报告

报告完成后可编辑标题、核心结论、逻辑标题和正文、图表标题、论点顺序及显隐状态，也可删除单张图表。自动稿保持不变，人工修订保存为独立文件。

编辑窗口提供：

- 保存修订版 HTML；
- 保存并导出 PDF；
- 打开产物文件夹。

删除图表后，剩余图表和正文会根据图型重新布局。PDF 重新执行实际页数校验。

### 5. 查看历史任务

左侧历史任务栏支持搜索、折叠和重命名。同一客户问题的重复运行合并展示；自定义标题只影响侧栏显示。历史报告默认只读，可通过“编辑此报告”创建该次运行的修订版。

## 命令行

桌面端是完整工作流的主要入口。命令行适合开发和诊断。

```powershell
python main.py -b "分析未来一个月机器人行业的投资机会。" --confirm-market
```

人工选择正文论点并导出 PDF：

```powershell
python main.py -b "分析光模块行业的投资机会。" --confirm-market --pick --pdf
```

指定客户约束：

```powershell
python main.py -b "分析消费电子板块的投资机会。" --confirm-market `
  --horizon 6个月 `
  --max-loss 20% `
  --principal-fluctuation yes `
  --return-preference "偏好温和上涨参与"
```

常用参数：

| 参数 | 说明 |
|---|---|
| `-b`, `--brief` | 客户需求 |
| `--confirm-market` | 接收分析师研究取数目标确认 |
| `--pick` | 人工选择正文候选论点 |
| `--pdf` | 导出 PDF 并执行实际页数校验 |
| `--overrides <json>` | 导入人工数据补充文件 |
| `--optionhelper recommend` | 运行结构推荐 |
| `--optionhelper quote` | 使用已确认 selection 发起正式报价 |
| `--horizon` | 客户期限 |
| `--max-loss` | 最大损失比例 |
| `--principal-fluctuation yes\|no` | 是否接受本金波动 |
| `--return-preference` | 客户收益偏好 |

CLI 会通过标准输入接收研究取数确认 JSON。日常使用建议采用桌面端确认窗口。

## ETF 校验

ETF 主题暴露分为：

- **直接暴露**：官方名称、跟踪指数或主要成分直接命中研究主题；
- **部分暴露**：覆盖宽行业或相邻产业链，需分析师确认映射理由；
- **不相关**：停止该 ETF 的主题研究路径。

证券真实性、跟踪指数、主题暴露和近 20 日流动性分别校验。ETF 近 20 日日均成交额最低要求为 **0.1 亿元**；达到 **1 亿元** 标记为流动性优选。主题 ETF 无法取得真实成分时，系统保留缺口并停止该路径。

## 补充材料与人工数据

- 支持 PDF 上传和粘贴文字；
- 每份材料记录来源和原材料日期；
- 过期、未来日期或日期不明的材料会进入审核；
- 客户提供事实、材料原文、市场数据和系统分析分别标记；
- `--overrides` 用于补充展示或解释字段，不触发机械论点；
- 客户材料保存在 `sources/`，不会进入版本库。

## OptionHelper 产品流程

正式报价是研究完成后的独立流程：

1. 分析师确认待报价 ETF/个股；
2. Research Helper 为每只标的生成产品画像；
3. OptionHelper 根据研究观点、客户约束和产品画像生成结构候选；
4. 分析师确认结构；
5. OptionHelper 获取正式定价数据并生成报价；
6. 多份报价完成后，由分析师选择写入一页通的项目。

客户点名多个标的时逐只处理。客户未点名时，默认展示研究取数工具；其他同主题 ETF 收在“比较其他同主题工具”入口。候选发现、结构推荐和正式报价分别记录状态。

Research Helper 使用构建并验收通过的 OptionHelper Skill。Skill 目录应包含：

```text
SKILL.md
scripts/tool_entry.py
scripts/environment_check.py
```

OptionHelper 版本检查、激活和回退步骤见 [MAINTENANCE.md](MAINTENANCE.md)。

## 输出文件

源码版默认写入项目 `output/`；发布版写入 `%LOCALAPPDATA%\ResearchHelper\output\`。

| 文件 | 用途 |
|---|---|
| `onepager_<主题>.html` | 自动生成的一页通 HTML |
| `onepager_<主题>.pdf` | 通过实际页数校验的 PDF |
| `onepager_<主题>_人工修订.json` | 结构化编辑覆盖层 |
| `onepager_<主题>_人工修订.html/.pdf` | 人工修订版报告 |
| `onepager_<主题>_内部底稿.md` | 研究口径、数据缺口、证据和产品调用记录 |
| `*_内部交互复核.html` | 主题篮子、历史序列和 ETF 候选复核页 |
| `output/runs/<run_id>.json` | 运行摘要、阶段状态、产物和恢复建议 |
| `output/runs/<run_id>.jsonl` | 逐事件运行日志 |
| `*.optionhelper-handoff.json` | OptionHelper 研究观点包 |
| `*.product-profile-<代码>.json` | 逐标的产品画像 |

客户版报告仅展示已确认内容。内部证据编号、候选淘汰信息、调用诊断和完整证据组合保留在内部底稿。

PDF 只有在实际渲染结果为一页时通过正式交付校验。报告标题下显示“研究策略·YYYY年M月D日”；各图表和数据来源保留实际数据日期。

## 开发、测试与发布

运行完整测试：

```powershell
python -m pytest -q tests
```

提交前检查：

```powershell
python -m compileall -q main.py gui core llm render tools
git diff --check
```

构建 Windows 发布包：

```powershell
python -m pip install -r requirements-release.txt
python tools/build_release.py --installer
```

构建器先验证 Qt/WebEngine 等关键依赖可导入，再执行 `tests/`、生成 PyInstaller onedir、校验 Qt 平台插件、
WebEngine 运行资源和离线 ECharts，最后用冻结程序运行模块导入及环境检查 smoke，生成 SHA-256 文件清单、ZIP 和
可选的 Inno Setup 安装器。输出位于 `dist/`。正式发布还需在无源码 Windows 机器完成首次设置、普通研究、事件检索、
报告编辑、HTML/PDF 导出和真实小规模报价验收。

构建macOS发布包必须在Mac上执行，且不会改动Windows发布物：

```bash
python3 -m venv .venv-macos
source .venv-macos/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-macos.txt
export APPLE_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export APPLE_NOTARY_PROFILE="research-helper-notary"
python tools/build_macos_release.py
```

构建器执行完整测试、生成`ResearchHelper.app`、检查Qt WebEngine与离线图表资源、运行冻结入口自检、
Developer ID签名、DMG封装及可选公证，输出`dist/ResearchHelper-<版本>-macOS.dmg`。

完整维护、升级、排障和回退流程见 [MAINTENANCE.md](MAINTENANCE.md)。

## 项目结构

```text
main.py                     命令行入口与端到端编排
gui/                        PySide6 桌面应用
core/                       需求解析、取数、研究、校验与产品桥接
llm/                        LLM 客户端
render/                     图表、HTML、PDF 与内部底稿
tools/                      发布、检查、迁移和 smoke 工具
tests/                      自动化测试
THESIS_LIBRARY.md           机器可读论点库
MAINTENANCE.md              维护、排障、升级、发布与回退手册
underlying_map.json         行业/主题与常用工具映射
```

## 常见问题

### 应用无法启动或提示 `No module named 'core'`

从项目根目录执行 `python gui/start.py`。检查当前解释器是否安装 PySide6，并确认未使用 Jupyter Interactive 模式运行桌面应用。

### DeepSeek 网页可用，但 API 调用失败

网页会话与 API 使用不同链路。检查 API Key、`DEEPSEEK_BASE_URL`、网络策略和 443 端口，并在“LLM 设置”中刷新模型目录和测试质量档模型。

### iFinD 取数失败

检查官方终端/SDK、`iFinDPy` 导入、账号密码、数据权限和周度额度。关键数据缺失时系统会保留缺口。

### 事件证据候选很少

检查事件主体、Tavily 连接、三类检索词、正文读取和候选淘汰记录。自动检索无法取得原文时，可上传材料、粘贴文字或手工录入证据。

### 研究完成但没有正式报价

检查待报价标的是否确认、OptionHelper 环境是否就绪、结构是否由分析师确认，以及报价任务区显示的失败阶段。

### HTML 正常但 PDF 未通过

检查 Qt WebEngine、静态图资源和实际 PDF 页数。报价写回或人工编辑后，PDF 会重新生成并再次校验。

## 安全与合规

- `config.local.json`、`.env`、`.optionhelper/`、`data/`、`result/`、客户材料和生成报告不得提交到 Git；
- 日志不得记录 API Key、密码、Refresh Token、请求头或完整敏感响应；
- iFinD SDK、第三方研报和专有数据按授权范围使用；
- 发布包不包含个人凭证；
- 客户材料、正式报价和内部底稿保存在机构批准的目录，并遵守数据保留政策。

## 相关文档

- [Research Helper 使用说明书](docs/user-guide.html)：首次设置、研究流程、报告编辑、历史任务与正式报价的图文操作指南
- [MAINTENANCE.md](MAINTENANCE.md)：维护、排障、OptionHelper 升级、发布与回退
- [THESIS_LIBRARY.md](THESIS_LIBRARY.md)：论点库、字段依赖与机械判定规则
