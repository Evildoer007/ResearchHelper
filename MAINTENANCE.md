# Research Helper 维护手册

## 1. 系统结构速览

### 1.1 主流程

```text
客户需求与产品约束
        ↓
需求解析与市场确认
        ↓
确认研究取数目标
  ├─ 标准行业成分
  ├─ 人工主题公司篮子
  └─ 主题 ETF 真实指数成分
        ↓
数据摸底与候选论点
        ↓
分析师审核
        ↓
报告正文、图表、HTML/PDF 与内部底稿
        ↓
研究完成后确认待报价标的
        ↓
标的产品画像 → OptionHelper 推荐 → 人工确认 → 正式报价
        ↓
选择报价写入一页通并重新校验交付文件
```

### 1.2 目录与职责

| 路径 | 职责 | 常见维护任务 |
|---|---|---|
| `main.py` | 命令行入口、端到端编排、运行底稿元数据 | 调整阶段顺序、确认协议、底稿留痕 |
| `gui/` | PySide6 桌面端、历史任务、报告编辑器 | 调整交互、对话框、任务状态、编辑功能 |
| `core/brief.py` | 客户需求结构化解析 | 增加需求字段、修正主题或事件识别 |
| `core/market_confirmation.py` | 市场、取数路径、主题篮子及 ETF 校验 | 调整研究路径、候选发现、暴露规则 |
| `core/theme_company_evidence.py` | 主题公司检索规划、原文约束和关联分级 | 调整公司候选证据逻辑 |
| `core/pipeline.py` | 数据摸底、论点规划和字段回填 | 调整研究数据流或篮子口径 |
| `core/fetcher.py`、`core/provider.py` | 指标调度和数据提供者 | 新增数据字段、处理数据源变化 |
| `core/evidence_discovery.py` | Tavily/Bing 检索、正文读取和事件证据发现 | 调整搜索策略、实体消歧、来源过滤 |
| `core/thesis.py`、`THESIS_LIBRARY.md` | 论点定义、触发条件和字段依赖 | 新增或修改研究论点 |
| `core/writer.py` | 报告文字生成及写作约束 | 调整客户版表达和论证结构 |
| `render/` | 图表、HTML、PDF、版式和交付渲染 | 新增图型、改配色、修复一页布局 |
| `core/report_edits.py`、`gui/report_editor.py` | 结构化人工修订 | 增加可编辑字段或图表操作 |
| `core/product_profile.py` | 报价前标的产品画像 | 调整结构推荐前的市场事实输入 |
| `core/optionhelper_*` | OptionHelper 推荐、报价、兼容和诊断 | 协议适配、超时、报价错误排查 |
| `core/run_tracker.py` | 运行阶段、外部调用、产物和恢复建议 | 增加可观测性和故障留痕 |
| `tools/` | 发布、OptionHelper 检查、迁移和 smoke 工具 | 构建、升级、激活、回退 |
| `tests/` | 自动化回归 | 所有逻辑修改必须补充或更新测试 |

### 1.3 三类运行环境

三类环境分别承担开发、报价和发布任务：

| 环境 | 用途 | 典型解释器 |
|---|---|---|
| Research Helper 开发环境 | 启动 GUI、运行研究、执行测试和构建 | 项目 `.venv` 或维护者指定 Python |
| OptionHelper 独立环境 | 运行指定版本 Skill 的依赖、推荐和正式报价 | `OPTIONHELPER_PYTHON` |
| 发布版冻结环境 | 最终用户运行Windows `ResearchHelper.exe`或macOS `ResearchHelper.app` | PyInstaller 内置运行时 |

Research Helper 的测试与构建使用开发环境解释器；OptionHelper 任务使用独立解释器，其锁定依赖仅安装在独立环境中。

---

## 2. 路径、配置与本机数据

### 2.1 源码态与发布态路径

源码运行时，项目根目录同时是默认可写数据目录。发布版将只读程序与用户数据分离：

```text
%LOCALAPPDATA%\ResearchHelper\
├─ config.local.json
├─ sources\
├─ data_cache\
├─ output\
│  └─ runs\
├─ data\
├─ result\
└─ .optionhelper\
```

路径规则集中在 `core/app_paths.py`。测试发布态路径时可使用：

- `RESEARCH_HELPER_RESOURCE_ROOT`：只读程序资源根目录；
- `RESEARCH_HELPER_DATA_ROOT`：可写用户数据根目录。

维护代码时统一通过 `core/app_paths.py` 取得路径。发布版的报告、配置和运行数据统一写入用户数据目录。

### 2.2 配置优先级

同名环境变量优先于 `config.local.json`。`core/config.py` 在模块导入时读取配置，因此长期设置修改后应重启应用。运行级模型降级等临时覆盖由专用环境变量传入，仅对当前运行生效。

主要配置分组：

| 分组 | 配置项 | 注意事项 |
|---|---|---|
| LLM | `DEEPSEEK_API_KEY`、`DEEPSEEK_FAST_MODEL`、`DEEPSEEK_QUALITY_MODEL`、`DEEPSEEK_FALLBACK_MODEL`、`DEEPSEEK_BASE_URL` | 模型 ID 必须以实时 `/models` 目录为准 |
| 搜索 | `TAVILY_API_KEY`、`SEARCH_PROVIDER`、`SEARCH_DEPTH`、`SEARCH_COUNTRY`、`SEARCH_LANGUAGE`、`SEARCH_BING_FALLBACK` | 高级搜索消耗更多额度；Bing 仅是低成本兜底 |
| 研究数据 | `IFIND_ACCOUNT`、`IFIND_PASSWORD`、`IFIND_SDK_PATH` | iFinD 官方 SDK 不随发布包分发 |
| OptionHelper | `OPTIONHELPER_SKILL_ROOT`、`OPTIONHELPER_PYTHON`、`OPTIONHELPER_SELECTION_PATH` | 必须指向已验收 Skill 与独立解释器 |
| 客户默认约束 | `OPTIONHELPER_DEFAULT_CONSTRAINTS` | 只放期限、最大损失和本金波动等约束，不放市场价格或条款结果 |

### 2.3 凭证规则

以下内容不得进入代码、测试数据、提交记录、截图或外发日志：

- API Key、账号密码、Refresh Token；
- Authorization 请求头；
- `config.local.json`、`.env`；
- OptionHelper `.optionhelper/` 下的本机状态；
- 客户材料、客户报价、`data/`、`result/` 与 `output/`。

日志只记录提供商、模型、调用状态、耗时、错误类型和脱敏摘要。排错输出同样遵守该范围，完整配置对象和 HTTP 请求体不得写入日志。

---

## 3. 日常运行与巡检

### 3.1 启动源码版

在项目根目录执行：

```powershell
python gui/start.py
```

桌面应用通过普通 Python 进程或终端启动。VS Code 的 Jupyter Interactive 模式会额外要求 `ipykernel`，且工作目录和模块路径可能偏离正式运行环境，因此不作为启动方式。

### 3.2 运行环境检查

桌面端“运行环境检查”调用 `core/release_health.py`，检查：

- 用户数据目录是否可写；
- DeepSeek 是否配置；
- Tavily 是否配置（未配置可使用 Bing 兜底）；
- iFinD SDK 和凭证状态；
- OptionHelper Skill 与解释器完整性；
- 本地 ECharts 资源是否存在。

机器可读结果写入：

```text
output/environment-check.json
```

`ready_for_research=true` 只代表基本研究入口可用；`ready_for_formal_quote=true` 才表示正式报价所需本机条件同时就绪。

### 3.3 每日/每次使用前建议检查

1. 启动后运行环境检查；
2. LLM 设置页刷新实时模型目录，确认快速档和质量档仍可用；
3. 需要事件检索时测试 Tavily 连接和剩余额度；
4. 需要正式报价时确认 OptionHelper 就绪；
5. 运行一个低敏感、短流程的问题，确认取数确认框、报告预览和历史记录正常；
6. 检查磁盘空间，尤其是 `output/`、`data/`、`result/` 和 `.optionhelper/migrations/`。

### 3.4 定期维护建议

| 周期 | 工作 |
|---|---|
| 每周 | 检查失败运行、搜索额度、iFinD 配额、模型目录变化和输出目录增长 |
| 每次代码发布前 | 全量测试、发布构建、干净机验收、秘密文件扫描 |
| 每次 OptionHelper 更新前 | 一键检查、真实小规模报价、激活后重启与回归 |
| 每季度或按机构政策 | 清理过期客户材料和输出；验证备份、恢复和账号权限 |

删除运行数据前应按机构数据保留规则确认范围，并优先归档到受控位置。递归删除操作必须使用经过核验的具体子目录，项目根目录和用户数据根目录不得作为目标。

---

## 4. 标准修改流程

### 4.1 修改前

1. 用可复现的客户问题确认现象；
2. 记录 `run_id`、失败阶段、错误全文和相关产物；
3. 检查 `git status`，保留无关的用户修改；
4. 判断问题属于配置、外部服务、数据权限、业务规则、UI、报告渲染还是 OptionHelper；
5. 在测试中先构造最小复现，或至少明确需要增加的回归案例。

### 4.2 修改中

1. 只改拥有该职责的模块，不在 GUI 中复制后端规则；
2. 数据事实、分析结论、产品建议和正式定价保持分层；
3. 新增字段时同步更新序列化、底稿、GUI 回填和旧记录兼容；
4. 外部失败必须形成可读诊断，原研究对象保持不变；
5. 任何自动推荐保持分析师确认入口；
6. 业务阈值、数据日期和来源口径在代码和文档中保持一致。

### 4.3 修改后

依次执行：

```powershell
python -m py_compile main.py
python -m compileall -q gui core llm render tools
python -m pytest -q tests
git diff --check
git status --short
```

然后进行与修改风险相匹配的人工验收。测试通过只证明代码契约未回归，不证明外部账号、数据权限、网络、真实行情和 PDF 渲染正常。

### 4.4 文档同步

- 用户能感知的安装、配置或操作变化：更新 `README.md`；
- 架构、业务对象和强制校验规则变化：更新 `DESIGN.md` 正文；
- 修改、排障、发布或升级方法变化：更新本手册；
- 论点定义或字段依赖变化：更新 `THESIS_LIBRARY.md`；
- 历史修复经过：只写入 `DESIGN.md` 修改日志，不把 README 写成开发日志。

当前 `.gitignore` 将 `DESIGN.md` 作为本地内部工作文档排除。对外或跨机器交接使用机构批准的受控文档渠道，保留现有忽略规则。

---

## 5. 业务逻辑维护指南

### 5.1 需求解析与事件识别

主要文件：

- `core/brief.py`：客户需求结构化；
- `core/genres.py`：研究类型和结构方向；
- `core/event_evidence.py`：事件证据模型与强制校验；
- `core/evidence_discovery.py`：事件事实、产业机制和 A 股暴露检索。

修改原则：

1. “普通行业研究”和“明确外部事件”的分类应同时结合客户原文、事件主体和分析师选择；
2. 分析师显式勾选事件型时应优先于自动分类；
3. 明确事件报告需要事件事实、产业机制和 A 股暴露；普通主题研究使用行业和市场数据路径；
4. LLM 的职责限定为整理候选，证据内容必须来自原文；
5. 搜索失败时允许上传、粘贴或手工录入；无来源推断不得进入证据包。

推荐测试：`tests/test_event_paths.py`、`tests/test_evidence_discovery.py`、`tests/test_material_evidence.py`。

### 5.2 三条研究取数路径

规则集中在 `core/market_confirmation.py` 和 `core/pipeline.py`：

| 路径 | 数据对象 | ETF 的作用 |
|---|---|---|
| 标准行业 | 数据源可核验行业节点的真实成分 | 仅可作为后续报价工具 |
| 人工主题篮子 | 分析师勾选且代码/关联依据通过校验的公司 | 仅可作为后续报价工具 |
| 主题 ETF | 所选 ETF 官方跟踪指数的真实成分 | 同时是本次研究取数对象，但仍不自动成为正式报价标的 |

硬性要求：

- 所有研究在取数前都必须经过目标确认；
- 三条路径互斥；
- 主题 ETF 成分取不到时不得回退到宽行业或代表股；
- 人工篮子少于 5 只只能称为核心样本，不生成行业整体聚合结论；
- 研究完成后再单独确认挂钩标的。

推荐测试：`tests/test_market_confirmation.py`、`tests/test_etf_discovery_scope.py`、`tests/test_theme_company_evidence.py`。

### 5.3 主题公司候选

当前主流程：

```text
LLM 拆产业链环节和规划搜索词
        ↓
Tavily/Bing 检索公开正文
        ↓
LLM 只能从正文逐字抽取公司名和业务片段
        ↓
iFinD 解析代码并核验官方简称
        ↓
披露来源分级与分析师逐只确认
        ↓
iFinD“XX 概念股”仅在候选不足时补漏
```

LLM 公司发现采用“产业链规划—网页检索—原文抽取”的受控流程。修改候选逻辑时必须保留：

- 公司名和证据原文的逐字存在性检查；
- A 股代码解析与官方简称一致性；
- 否认性披露过滤；
- 系统候选默认不勾选；
- 检索词、命中、淘汰和身份核验进入内部底稿。

相关文件：`core/theme_company_evidence.py`、`core/market_confirmation.py`。推荐测试：`tests/test_theme_company_evidence.py`。

### 5.4 ETF 候选与暴露规则

ETF 暴露分为直接、部分和不相关：

- 直接暴露：官方名称、跟踪指数或主要成分直接命中主题；
- 部分暴露：仅命中宽行业或相邻产业链，必须由分析师确认映射理由；
- 不相关：停止该 ETF 的主题路径；
- 证券真实性、指数真实性和流动性属于强制校验项，不允许人工跳过。

主题同义词和暴露规则主要在 `core/market_confirmation.py`，常用工具目录在 `core/instruments.py`，行业/主题映射还涉及 `underlying_map.json`、`core/signals.py` 和 `core/universe.py`。单个案例出现异常时，先检查短主题提取、通用同义词和官方事实判定，再评估是否需要新增专用规则。

### 5.5 论点库和数据字段

新增论点时：

1. 在 `THESIS_LIBRARY.md` 定义论点、适用类型、方向和证伪条件；
2. 在 `core/thesis.py` 注册机械判定与真实字段依赖；
3. 确认 `core/fetcher.py` 和相应数据模块能返回该字段；
4. 缺数时返回明确 gap，不用其他指标冒充；
5. 为触发、未触发、边界值和缺数各补测试；
6. 检查 Writer 获得的是已验证数据，不让 LLM自行计算或补数。

调整提示词前先确认上游已提供可用字段和序列。图表和正文质量问题通常要先从数据完整性排查。

### 5.6 搜索与事件证据

搜索层使用 Tavily 主入口和 Bing RSS 兜底。调整搜索时应分别评估：

- 查询词是否锚定事件主体；
- 是否区分事件事实、产业机制和 A 股暴露；
- 是否过滤纯行情、走势图、聚合转载和实体误命中；
- Tavily 清洗正文是否足够，是否又重复抓取网页；
- 候选是否保留原网址、逐字原文、来源等级和淘汰原因；
- 扩大搜索轮数对 Tavily credits 和等待时间的影响。

来源等级用于排序和审核，原文校验仍须单独通过。普通新闻按媒体来源处理，只有原始披露文件可归入公司披露。

### 5.7 报告文字、图表与一页校验

相关文件：`core/writer.py`、`render/charts.py`、`render/layout.py`、`render/interactive.py`、`render/pdf_out.py`。

修改时必须保证：

- 客户版不出现内部证据编号、可信度标签、调试提示或“见底稿”等占位文字；
- 图题描述对象和指标，不使用带结论的长句代替标题；
- 图表注明真实数据日期、单位和来源；
- 图型必须匹配数据关系，不为视觉效果强行使用复杂图；
- HTML 交互图与 PDF 静态图来自同一数据；
- 报价写回后重新生成并重新校验 PDF；
- PDF 实测一页才标记为正式交付。

推荐测试：`tests/test_layout.py`、`tests/test_structure_charts.py`、`tests/test_reporting_hygiene.py`、`tests/test_delivery_gate.py`。

### 5.8 报告编辑器

人工修订采用结构化覆盖层，自动生成的 HTML 保持只读。可编辑范围主要包括标题、核心结论、逻辑标题/正文、图表标题、论点顺序、论点显隐和单图删除；报价事实、日期、数据源、数值和免责声明保持锁定。

修改编辑功能时检查：

- 旧 sidecar JSON 能否兼容读取；
- 自动稿更新后能否重新应用人工覆盖；
- 删除图表后是否清理空容器并重新布局；
- 保存修订版 HTML 与导出 PDF 是否写入正确历史任务；
- PDF 是否重新执行一页校验。

推荐测试：`tests/test_report_edits.py`、`tests/test_report_editor_gui.py`。

### 5.9 OptionHelper 集成

Research Helper 与 OptionHelper 的职责必须保持分离：

- Research Helper：共同研究观点、客户约束、逐标的产品画像、人工选择和交付整合；
- OptionHelper Recommender：结构候选及适用/不适用条件；
- OptionHelper Quote：正式行情、波动率、利率、分红、交易日历、结构参数和报价文件。

正式定价使用 OptionHelper 当次取得的数据和合同条款。Research Helper 的历史画像仅用于推荐前审核。协议调整主要涉及：

- `core/optionhelper_bridge.py`；
- `core/optionhelper_recommender_worker.py`；
- `core/optionhelper_quote_worker.py`；
- `core/optionhelper_quote_watchdog.py`；
- `core/optionhelper_compat.py`；
- `core/optionhelper_pricing.py`。

推荐测试：`tests/test_optionhelper_compat.py`、`tests/test_optionhelper_migration.py`、`tests/test_optionhelper_update_check.py`、`tests/test_quote_diagnostics.py`、`tests/test_gui_quote_candidates.py`。

---

## 6. 测试策略与验收矩阵

### 6.1 自动测试层级

| 层级 | 命令 | 适用场景 |
|---|---|---|
| 语法检查 | `python -m compileall -q gui core llm render tools` | 所有修改 |
| 单文件定向测试 | `python -m pytest -q tests/test_xxx.py` | 开发过程中快速验证 |
| 全量回归 | `python -m pytest -q tests` | 提交、发布和依赖升级前 |
| OptionHelper 离线 smoke | `python tools/optionhelper_smoke.py <Skill目录>` | OptionHelper 候选版本验收 |
| 发布构建测试 | `python tools/build_release.py --installer` | 正式发布前 |

标准测试命令始终显式指定 `tests`。项目根目录的 `data/`、`result/` 可能受权限保护或包含非测试资产，不带路径的 `pytest` 容易产生无关的收集错误。

### 6.2 人工验收最小集合

正式发布前至少验证：

1. 一个标准行业问题；
2. 一个细分主题公司篮子问题；
3. 一个主题 ETF 真实成分问题；
4. 一个明确外部事件问题；
5. 一次材料上传和一次粘贴文字；
6. HTML 预览、报告编辑、删除图表和 PDF 导出；
7. 历史任务搜索与重命名；
8. 一个真实但非敏感的单标的 OptionHelper 推荐和正式报价；
9. 一次取消、失败和重试，确认不会复用旧 selection 或旧报价；
10. 发布版在无源码电脑首次启动并完成环境检查。

### 6.3 测试失败处理

- 先判断是产品回归、测试夹具过时、外部网络还是本机权限；
- 不删除失败断言来让测试变绿；
- 外部 API 测试使用 mock 或明确的集成测试入口；全量单测保持离线可重复；
- 若只运行部分测试，交付说明必须明确“未完成全量回归”；
- 发布构建不得使用 `--skip-tests` 作为正式流程。

---

## 7. OptionHelper 更新、激活与回退

### 7.1 源码仓库与构建后 Skill 的区别

Research Helper 调用构建后的 OptionHelper Skill。源码仓库可能缺少 `scripts/tool_entry.py`、能力清单、文件哈希或锁定依赖状态，直接把源码目录写入 `OPTIONHELPER_SKILL_ROOT` 会导致 `No module named 'tool_entry'`、协议不兼容或权限问题。

### 7.2 一键只读检查

使用 Research Helper 日常解释器：

```powershell
python tools/optionhelper_check_update.py
```

或显式指定：

```powershell
python tools/optionhelper_check_update.py `
  --source 'D:\download\OptionHelper' `
  --candidate-python 'C:\path\to\candidate-env\Scripts\python.exe' `
  --candidate-skill 'C:\path\to\built-option-helper'
```

该脚本的职责限于只读检查源码提交、关键接口差异、Skill 完整性、锁定依赖、统一就绪、双标的离线 smoke 和 Research Helper 回归，并输出 Markdown/JSON 总报告。源码更新、依赖安装、真实报价和版本切换均由后续步骤执行。

### 7.3 标准升级步骤

1. 保留当前 Skill、解释器和迁移记录；
2. 确认 OptionHelper 源码工作区干净；
3. 执行 `git pull --ff-only` 并记录完整提交号；
4. 创建独立 Python 环境，安装该版本锁定依赖；
5. 从源码构建新的独立 Skill 目录；
6. 运行一键只读检查；
7. 完成一次真实小规模报价，核对标的、结构、期限、估值日、条款、报价表、预览和底稿；
8. 使用候选解释器执行受保护的激活命令；
9. 重启 Research Helper；
10. 再完成一次最小验收。

激活命令：

```powershell
& 'C:\path\to\candidate-python.exe' tools/optionhelper_install.py `
  --activate 'C:\path\to\option-helper' `
  --source-commit '<完整提交号>'
```

激活工具会验证文件哈希、入口、统一就绪状态、工具列表和 `tool_entry` 模块导入；在 Windows 上恢复候选 Skill 的 ACL 继承并只向工作区所有者授予读取/执行权限。检查全部通过后才更新配置，失败时保留当前版本。

### 7.4 回退

迁移记录位于：

```text
.optionhelper/migrations/<时间戳>/
```

执行：

```powershell
& 'C:\path\to\current-optionhelper-python.exe' tools/optionhelper_install.py `
  --rollback 'C:\path\to\research helper\.optionhelper\migrations\<迁移记录>'
```

回退只恢复 OptionHelper 集成路径，不覆盖后来修改的 DeepSeek、Tavily 或其他配置。回退后必须重启应用。

### 7.5 常见更新故障

| 现象 | 常见原因 | 处理 |
|---|---|---|
| `No module named 'tool_entry'` | Skill 路径指向源码根目录、入口缺失或 ACL 拒绝读取 | 用构建后 Skill；运行受保护激活；检查 `scripts/tool_entry.py` 和目录权限 |
| 点击报价无反应 | 子进程未启动、错误回调未显示、解释器不可用 | 查运行 JSON/JSONL、终端和报价任务区；检查 `OPTIONHELPER_PYTHON` |
| `ProcessError.Crashed` | OptionHelper 子进程崩溃或环境/Store/SDK 异常 | 查看同一 quote id 的诊断和 stderr 摘要；运行统一就绪检查 |
| 正式报价超时 | Monte Carlo 路径、行情请求、日历或外部 Store 较慢 | 查阶段耗时，先区分计算耗时、死锁和网络失败，再决定是否调整超时 |
| 报价完成但界面一直“正在重建” | HTML/PDF 刷新回调或路径处理失败 | 查 `finalizing` 阶段、PDF 回调和同一运行产物；研究结果与报价文件应保留 |
| 条款缺少名称或数值 | 新版输出协议变化、内部派生字段被当成外发事实 | 比较候选与当前协议；仅在窄兼容层处理，不在 Writer 猜测字段 |

---

## 8. Windows 发布与分发

### 8.1 发布前准备

1. 工作区状态明确，无未审阅变更；
2. 更新 `VERSION`；
3. 使用干净的 Windows 构建环境；
4. 安装 `requirements-release.txt`；
5. 全量测试通过；
6. 确认发布包不包含个人配置、客户材料或本机 OptionHelper 状态。

### 8.2 构建

```powershell
python -m pip install -r requirements-release.txt
python tools/build_release.py --installer
```

构建产物位于 `dist/`：

- `ResearchHelper/`：PyInstaller onedir 目录；
- `ResearchHelper-<版本>-win64.zip`：便携包；
- `ResearchHelper/release-manifest.json`：文件大小与 SHA-256 清单；
- `dist/installer/`：检测到 Inno Setup 6 时生成安装器。

构建脚本在打包前验证 Qt/WebEngine 与主要运行依赖可导入；打包后检查 `qwindows.dll`、
`QtWebEngineProcess.exe`、WebEngine 资源和离线 ECharts，并用冻结版运行模块导入与环境检查 smoke。
脚本也会拒绝 `config.local.json`、`sources/`、`output/` 或 `.optionhelper/` 混入发布目录。
`--skip-tests` 仅用于本地构建定位；正式发布始终执行完整测试。任何 preflight、资源检查或 smoke 失败都不得分发该包。

### 8.3 干净机验收

在没有源码和开发环境的 Windows 机器验证：

- 安装/解压、首次启动和设置向导；
- `%LOCALAPPDATA%\ResearchHelper` 可写；
- DeepSeek 模型目录刷新和最小调用；
- Tavily 测试及 Bing 兜底；
- iFinD 官方 SDK 发现和登录；
- 普通研究、事件证据、HTML/PDF、报告编辑和历史任务；
- 有权限时完成 OptionHelper 真实单标的报价；
- 卸载后用户数据是否按预期保留。

### 8.4 发布记录

每个发布版本至少记录：

- Research Helper Git 提交号和 `VERSION`；
- 构建时间、Python 和主要依赖版本；
- ZIP/安装器 SHA-256；
- 激活的 OptionHelper 提交、Skill 哈希和解释器；
- 自动测试结果和人工验收清单；
- 已知限制、回退版本和负责人。

### 8.5 macOS并行发布

macOS是独立发布目标，不替换Windows安装器、`requirements-release.txt`、`ResearchHelper.spec`或Inno Setup流程。
Mac使用`requirements-macos.txt`、`ResearchHelper-macOS.spec`和`tools/build_macos_release.py`。Windows研究取数继续
优先调用官方`iFinDPy` SDK；macOS在没有SDK时使用iFinD官方HTTP API与`IFIND_REFRESH_TOKEN`。

在真实Mac上执行：

```bash
python3 -m venv .venv-macos
source .venv-macos/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-macos.txt
export APPLE_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export APPLE_NOTARY_PROFILE="research-helper-notary"
python tools/build_macos_release.py
```

发布前分别验收Apple Silicon与需要支持的Intel机型，至少覆盖首次设置、iFinD HTTP取数、Tavily、报告编辑、
PDF、历史任务、OptionHelper推荐与一笔真实小规模报价。没有Developer ID签名和Apple公证的DMG只用于内部测试。

---

## 9. 运行日志与故障定位

### 9.1 首先收集什么

排障时至少收集：

1. 客户问题的脱敏版本；
2. `run_id`；
3. `output/runs/<run_id>.json`；
4. 必要时的 `<run_id>.jsonl`；
5. 失败阶段、外部调用状态、耗时和恢复建议；
6. 相关产物路径；
7. 应用版本、OptionHelper 版本和本机环境检查结果。

重试前先保存现场信息。研究任务和报价子任务可能各有独立记录，应通过 run id、quote id、标的代码和时间核对它们是否属于同一轮。

### 9.2 状态含义

| 状态 | 含义 | 维护判断 |
|---|---|---|
| `running` | 阶段或外部调用正在进行 | 长时间不变时检查心跳、子进程和超时 |
| `completed` | 该阶段成功结束 | 不代表整份报告或正式报价已完成 |
| `failed` | 阶段失败并应有错误原因 | 查该阶段首个真实错误，避免只看后续连锁失败 |
| `cancelled` | 用户或系统明确取消 | 清理未消费 selection 和临时文件，后续任务重新确认 |
| `finalizing` | 报价已返回，正在同步底稿/HTML/PDF | OptionHelper 定价已经结束；交付阶段使用独立超时 |

### 9.3 常见问题排查

#### A. GUI 无法启动或 `No module named 'core'`

1. 确认从项目根目录运行 `python gui/start.py`；
2. 使用 `gui/start.py` 作为桌面端入口；
3. 检查 `gui/start.py` 是否正确加入项目根路径并选择安装了 PySide6 的解释器；
4. 检查是否误用 VS Code Interactive/Jupyter；
5. 运行 `python -c "import core, PySide6; print(core.__file__)"`。

#### B. LLM 总是使用备用模型

1. 在设置页刷新 `/models`；
2. 检查快速档和质量档模型是否仍在账号目录；
3. 查看运行日志中的 requested model 与 effective model；
4. 检查是否存在 `RESEARCH_HELPER_RUN_FAST_MODEL` 或 `RESEARCH_HELPER_RUN_QUALITY_MODEL` 临时覆盖；
5. 保存新设置后重启应用。

#### C. 事件证据搜索成功率低

1. 确认问题确实属于明确外部事件；
2. 查看三类检索词是否锚定正确主体；
3. 检查是否命中同名品牌或无关实体；
4. 查看 Tavily/Bing 命中、正文读取失败和候选淘汰原因；
5. 检查纯行情页是否被正确过滤；
6. 只对薄弱证据类型补检，不盲目增加所有搜索轮次；
7. 无法自动取得时保留上传、粘贴和人工确认入口。

#### D. 主题公司不相关

1. 查看候选来源是 LLM 原文检索、需求解析还是 iFinD 概念兜底；
2. 检查原文是否包含公司主体、具体产品/业务和主题关系；
3. 检查代码与官方简称核验；
4. 检查否认性披露是否被排除；
5. 不因“概念股命中”将其标为核心候选；
6. 必要时修正产业链拆解或检索词；公司白名单只用于有明确业务规则和审核记录的场景。

#### E. 行业或 ETF 取数失败

1. 确认分析师选择的是标准行业、人工篮子还是主题 ETF；
2. 标准行业必须是数据源可验证节点；
3. 主题 ETF 必须能取得真实跟踪指数与成分；
4. 检查 iFinD SDK、账号、权限和额度；
5. 不把报价 ETF 反向当成研究取数对象；
6. 不使用宽行业或单只代表股静默替代失败目标。

#### F. HTML 正常、PDF 失败或超过一页

1. 检查 Qt WebEngine 是否可用；
2. 查看静态图、字体或本地 ECharts 资源是否缺失；
3. 检查删除图表后的空容器和布局标记；
4. 检查报价写回后是否重新渲染；
5. 以实际 PDF 页数为准，不通过 CSS 预估宣称一页；
6. 修订版和自动版分别检查，避免打开错文件。

---

## 10. 数据、备份与恢复

### 10.1 需要备份的内容

根据机构政策备份：

- `config.local.json`（必须加密并限制访问）；
- `output/` 中需保留的正式报告、底稿和运行日志；
- `sources/` 中依法可保留的客户材料；
- `.optionhelper/migrations/` 回退记录；
- `data/`、`result/` 中需留档的正式报价资产；
- `history_titles.json` 等用户界面状态。

客户数据使用机构批准的受控存储备份。代码仓库只保存可公开的源代码、测试、发布配置和维护文档。

### 10.2 恢复顺序

新机器或故障恢复时建议：

1. 安装同版本 Research Helper；
2. 恢复用户配置，但重新验证凭证；
3. 安装官方 iFinD SDK；
4. 恢复经验证的 OptionHelper Skill、解释器和迁移记录；
5. 运行环境检查；
6. 恢复必要材料和输出；
7. 执行普通研究和真实小规模报价验收。

恢复 OptionHelper 时应同时准备实际 Skill、独立环境和本机配置。绝对路径、Token 和 ACL 均按新电脑及当前用户重新配置。

---

## 11. 安全、权限与合规检查

发布或外发前确认：

- [ ] 仓库和发布包没有 `config.local.json`、`.env` 或密钥文件；
- [ ] 没有 `sources/`、`references/`、`output/`、`data/`、`result/`、`.optionhelper/`；
- [ ] 日志没有密码、API Key、Refresh Token 或 Authorization 头；
- [ ] 第三方 SDK、研报和专有数据没有被重新分发；
- [ ] 客户版报告不含内部证据编号、调试输出或内部审核措辞；
- [ ] 正式报价和研究报告均来自本次运行且可追溯；
- [ ] Windows 权限只授予必要用户，不使用 Everyone 放宽 Skill 目录；
- [ ] 依赖和外部源码来自批准来源；
- [ ] 删除、归档和保留符合机构数据政策。

---

## 12. 维护变更记录模板

每次重要维护建议使用以下记录：

```text
标题：
日期 / 负责人：
影响版本：
问题现象：
复现输入（脱敏）：
运行编号 / 报价编号：
根因：
修改文件：
业务规则变化：
安全与数据影响：
自动测试：
人工验收：
兼容性：
发布方式：
回退方式：
已知限制：
```

错误修复记录应写明根因、具体改动和防回归测试。外部依赖升级还应记录上游提交、依赖锁、协议差异和真实验收结果。

---

## 13. 维护前检查清单

每次修改代码、业务规则、配置或外部依赖前，先完成以下检查：

- [ ] 已保存可复现的问题描述、脱敏输入、截图和完整错误文字；
- [ ] 已记录相关 `run_id`、quote id、失败阶段及产物路径；
- [ ] 已检查 `output/runs/<run_id>.json` 和必要的 `.jsonl` 事件日志；
- [ ] 已确认问题来自代码、配置、数据权限、外部服务或本机环境中的哪一层；
- [ ] 已执行 `git status --short`，并识别现有修改的归属；
- [ ] 本次修改范围明确，没有覆盖无关文件或用户未提交的工作；
- [ ] 已确认涉及的研究对象、取数路径、证据规则、报告内容或报价阶段；
- [ ] 涉及业务规则时，已核对 README、DESIGN 和现有测试中的对应说明；
- [ ] 涉及外部接口时，已记录当前模型、SDK、API、OptionHelper Skill 和依赖版本；
- [ ] 涉及 OptionHelper 更新时，已保留当前 Skill、解释器和迁移记录；
- [ ] 涉及配置或用户数据时，已准备必要备份并确认恢复方式；
- [ ] 已确认日志、测试数据和截图中不包含密钥、密码、Token 或客户敏感信息；
- [ ] 已确定需要新增或更新的回归测试；
- [ ] 已确定修改后的人工验收案例和预期结果；
- [ ] 已准备清晰可执行的回退方案。

以上信息齐全后再开始修改，便于定位根因、控制影响范围并在出现回归时及时恢复。
