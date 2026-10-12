---
name: research-helper
description: Analyze Chinese investment-research requests, confirm the research data scope, retrieve iFinD and public evidence, build auditable viewpoints, and generate Research Helper HTML/PDF reports. Use for Research Helper workflows, event-driven evidence research, ETF or company-basket scope confirmation, and report generation. Do not use it as a substitute for OptionHelper formal derivative pricing.
metadata:
  short-description: 可审计的投研取数、证据与报告流程
---

# Research Helper

Use the bundled entry point instead of reimplementing research logic:

```bash
python scripts/tool_entry.py --list
python scripts/environment_check.py
```

Run commands from this skill directory. Every command returns one JSON object on standard output. On Windows, write Chinese input to a UTF-8 JSON file and pass it with `--input`; do not pipe Chinese JSON through PowerShell.

## Guided conversation contract

This Skill is an analyst-guided workflow, not a collection of disconnected commands.

- Continue automatically while no analyst judgment is required. Do not end a turn merely because one tool action finished.
- At a decision checkpoint, summarize the result briefly and end with exactly one concrete question. Give the recommended choice and the accepted reply format.
- After the analyst answers, continue to the next checkpoint automatically. Never say only “本轮到此停止”, “已完成该步骤”, or “下一步想做什么”.
- Use `next_action` and `next_prompt` returned by the tool. If a structured user-input UI is available, use it; otherwise ask the question plainly.
- Do not repeat `environment-check` after it has passed in the same chat unless configuration, Python, or the installed Skill changed.
- Treat workflow instructions such as “先解析需求”“让我确认后继续” and the `$research-helper` mention as control text. Pass only the customer's investment-research question to `analyze-request` and `run-research`.

Required checkpoints are: target confirmation; event-evidence confirmation for an explicit external event; viewpoint selection; and the post-report action. Never infer an answer at these checkpoints.

## Workflow

1. Run `environment-check`. Stop for missing imports or `missing_required_credentials`. Tavily is optional: when it is absent, disclose that public-evidence discovery will use the Bing RSS fallback with lower coverage. Never invent data as a fallback.
2. Call `analyze-request` with the customer's complete original request. Preserve that text verbatim.
3. Present the returned research scope and candidates to the analyst. Every study requires explicit target confirmation before data retrieval.
4. Call `verify-target` with the returned `brief` and the analyst's confirmation. Do not modify rejected confirmations silently.
5. For an explicit external event, collect and confirm event facts, industry mechanism, A-share exposure, and the combined transmission chain. Follow [event evidence](references/event-evidence.md).
6. Call `prepare-viewpoints` with the verified `brief` and confirmed event evidence. Show all candidates, highlight the suggested combination, and ask the analyst to select 2–3 viewpoints or explicitly accept the suggestion.
7. Call `run-research` with `logic_pick_ids` from that confirmation. Automatic selection is allowed only after the analyst explicitly requests it; then set `auto_select_viewpoints: true`.
8. Report the generated artifact paths, actual PDF page count, and delivery status, then ask one post-report question offering: open/edit, export, enter OptionHelper, or finish. Reports may paginate naturally; successful native PDF export is not blocked by page count.

Use [tool schemas](references/tool-schemas.md) for request and response fields. Use [workflow and boundaries](references/workflow.md) when deciding between a standard industry, analyst-selected basket, and theme ETF route.
For a new machine, follow [local setup](references/setup.md) before the first research run.

## Non-negotiable controls

- Keep research scope, quotation underlyings, and final product pricing as separate decisions.
- Use LLM output to structure questions and hypotheses. Use iFinD, official disclosures, confirmed source text, and deterministic checks for securities facts.
- Do not accept a concept-list hit as proof of core business relevance. Require company-level association evidence before preselecting a theme-basket company.
- Do not turn an unverified web result, price page, or model recollection into evidence.
- Use only the HTML/PDF produced by the bundled Research Helper runtime as a Research Helper report. Never recreate, redesign, or replace a failed native report with generic HTML, document libraries, or an independently authored template. A multi-page native PDF is a valid deliverable; report its actual page count without treating it as a failure.
- Never include API keys, passwords, refresh tokens, customer materials, cached market data, or prior reports in a packaged skill.
- Leave formal structure selection and quote calculation to OptionHelper. Research Helper may prepare context and invoke the configured bridge only when the analyst explicitly requests it.

## Runtime locations

The source skill finds the adjacent Research Helper project automatically. A packaged skill uses `scripts/runtime_source/`.

Configuration is read from `RESEARCH_HELPER_DATA_ROOT` when set. Otherwise the source skill uses the project directory, while a packaged skill reuses the current user's standard `ResearchHelper` data directory. This allows credentials to remain local and outside the skill package.

