# Research Helper Skill

Research Helper Skill 用于在 Codex 中完成中文投研工作流，包括需求解析、研究取数目标确认、iFinD 取数、事件证据整理、候选观点审核，以及 Research Helper HTML/PDF 报告生成。

它是 Research Helper 桌面应用的 Skill 版本，适合通过对话完成研究。正式的场外衍生品结构推荐与报价仍由 OptionHelper 负责。

## 1. 使用前准备

使用者需要具备：

- Codex 桌面端或支持本地 Skill 的 Codex 环境；
- Python 3.10 或更高版本；
- DeepSeek API Key；
- 可用的 iFinD 账号与接口权限；
- Tavily API Key（可选，用于提高公开资料检索覆盖率）；
- OptionHelper Skill（可选，仅在需要进入结构推荐或正式报价时使用）。

Windows 用户如需通过桌面 SDK 取数，还需安装官方 iFinD Python SDK。macOS 用户使用 iFinD HTTP API 时，需要配置有效的 refresh token。

## 2. 安装 Skill

### Windows

关闭 Codex，将 ZIP 解压到当前用户的 Skill 目录：

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.codex\skills"
Expand-Archive -LiteralPath ".\research-helper-0.3.2.zip" -DestinationPath "$env:USERPROFILE\.codex\skills" -Force
```

安装完成后，应存在以下文件：

```text
C:\Users\<用户名>\.codex\skills\research-helper\SKILL.md
```

### macOS / Linux

```bash
mkdir -p ~/.codex/skills
unzip -o research-helper-0.3.2.zip -d ~/.codex/skills
```

安装完成后，应存在：

```text
~/.codex/skills/research-helper/SKILL.md
```

如果目录中多出一层同名文件夹，请将真正包含 `SKILL.md` 的 `research-helper` 文件夹放到 `.codex/skills` 下。

## 3. 安装 Python 依赖

请使用后续运行 Skill 时使用的同一个 Python 解释器安装依赖。

Windows：

```powershell
python -m pip install -r "$env:USERPROFILE\.codex\skills\research-helper\requirements-skill.txt"
```

macOS / Linux：

```bash
python3 -m pip install -r ~/.codex/skills/research-helper/requirements-skill.txt
```

iFinD 官方 SDK 不包含在本 ZIP 中，需由使用者按本机授权和官方安装说明单独配置。

## 4. 配置账号与接口

不要把账号、密码或 API Key 写入 Skill 文件夹，也不要提交到 GitHub。请在当前用户的 Research Helper 配置文件中保存：

- Windows：`C:\Users\<用户名>\AppData\Local\ResearchHelper\config.local.json`
- macOS：`~/Library/Application Support/ResearchHelper/config.local.json`
- Linux：`${XDG_DATA_HOME:-~/.local/share}/ResearchHelper/config.local.json`

Windows 配置示例：

```json
{
  "DEEPSEEK_API_KEY": "在本机填写",
  "IFIND_ACCOUNT": "在本机填写",
  "IFIND_PASSWORD": "在本机填写",
  "IFIND_SDK_PATH": "D:/path/to/official-ifind-sdk/bin/x64",
  "TAVILY_API_KEY": "在本机填写"
}
```

macOS 使用 iFinD HTTP API 时，可将 iFinD 部分改为：

```json
{
  "DEEPSEEK_API_KEY": "在本机填写",
  "IFIND_REFRESH_TOKEN": "在本机填写",
  "TAVILY_API_KEY": "在本机填写"
}
```

只配置本机实际采用的 iFinD 认证方式。Tavily 未配置时仍可运行，但公开资料搜索会改用覆盖率较低的 Bing RSS 备用入口。

## 5. 运行环境检查

Windows：

```powershell
python "$env:USERPROFILE\.codex\skills\research-helper\scripts\environment_check.py"
```

macOS / Linux：

```bash
python3 ~/.codex/skills/research-helper/scripts/environment_check.py
```

返回结果中的 `ok` 应为 `true`，并且不应存在 `missing_imports` 或必需凭证缺失。该检查不会访问外部接口，也不会显示密钥内容。

## 6. 在 Codex 中使用

完成安装和环境检查后，重新启动 Codex。在新对话中直接输入：

```text
请使用 $research-helper 分析未来一个月机器人行业的投资机会。
```

也可以提供更完整的客户条件：

```text
请使用 $research-helper 分析未来三个月风电行业的投资机会，客户看涨，不接受本金波动。请先让我确认研究取数目标。
```

Skill 会按顺序引导使用者完成：

1. 解析客户需求；
2. 确认标准行业、主题 ETF 或分析师公司篮子等取数目标；
3. 对事件型问题确认事件事实、产业机制与 A 股暴露证据；
4. 选择候选观点；
5. 生成并返回 HTML/PDF 报告；
6. 询问是否编辑、导出、进入 OptionHelper 或结束。

每个需要分析师判断的节点都会提出一个明确问题。确认后，Skill 会自动继续到下一个节点。

## 7. 文件保存位置

运行记录、材料与报告默认保存在当前用户的 Research Helper 数据目录中：

- Windows：`C:\Users\<用户名>\AppData\Local\ResearchHelper\output`
- macOS：`~/Library/Application Support/ResearchHelper/output`
- Linux：`${XDG_DATA_HOME:-~/.local/share}/ResearchHelper/output`

本 ZIP 不包含任何用户凭证、客户材料、历史报告、行情缓存或 OptionHelper 报价结果。

## 8. 更新 Skill

下载新版本 ZIP 后：

1. 关闭 Codex；
2. 备份或移走旧的 `research-helper` Skill 文件夹；
3. 将新 ZIP 解压到 `.codex/skills`；
4. 重新安装或更新 `requirements-skill.txt` 中的依赖；
5. 运行环境检查；
6. 重启 Codex。

用户配置和历史报告位于 Research Helper 数据目录，不在 Skill 安装目录中，正常替换 Skill 不会删除这些内容。

## 9. 常见问题

### Codex 没有识别到 Skill

确认 `SKILL.md` 的实际位置为 `.codex/skills/research-helper/SKILL.md`，然后重启 Codex。

### 提示缺少 iFinD 或 DeepSeek 凭证

确认 `config.local.json` 位于当前用户的数据目录中，而不是 Skill 文件夹或原开发项目目录中。修改后重新运行环境检查。

### Windows 提示无法导入 iFinDPy

确认已经安装官方 iFinD Python SDK，并检查 `IFIND_SDK_PATH` 是否指向包含 `iFinDPy.py` 和 `ShellExport.dll` 的目录。

### 没有 Tavily 是否能使用

可以。公开资料搜索会使用 Bing RSS 备用入口，但事件型证据检索的覆盖率可能下降。

### 是否等同于 Research Helper 桌面应用

核心研究规则与报告运行时来自同一项目版本，但 Skill 通过 Codex 对话操作，不提供桌面应用的完整图形界面。

### 是否可以直接完成正式衍生品报价

Research Helper 负责研究、证据和报告。正式结构推荐与报价需要单独安装并配置 OptionHelper，由分析师明确选择后进入。
