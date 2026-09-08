# Humanitarian Analytics Agents (haa)

A multi-agent data-analytics assistant for humanitarian information management,
built on the Claude Agent SDK. Ask questions about your survey data
(KoboToolbox-style exports) in plain language; an analyst agent writes pandas
code, runs it locally, and answers with validated aggregates and charts —
**raw beneficiary data never leaves your machine**.

## Why this exists

Humanitarian datasets are full of personal data (names, phones, GPS). Sending
them to an LLM API is not acceptable. This project demonstrates an architecture
where agents analyze data they cannot see: they work through schemas,
statistics, and locally executed code.

## The PII boundary (threat model)

We protect against *accidental* leakage of personal data into API calls, not
against a malicious agent. Four enforced layers:

1. **Prompt discipline** — agents are instructed to work with aggregates only.
2. **PreToolUse hook** — direct file reads of `workspace/data/` are denied at
   the framework level.
3. **Sandbox loader** — `load_dataset()` strips auto-detected PII columns
   before code ever touches a DataFrame.
4. **Output guard** — sandbox output is redacted (phones, GPS) and truncated
   before it reaches the model; every executed snippet is logged for audit.

Known limitation: sandbox code could read files directly, bypassing the loader
(layer 4 and the audit log exist for exactly that case). `workspace/project_docs/`
is a deliberate exception — project documentation (logframes, proposals) is
*meant* to be read by the model. Never put beneficiary data there.

## Quickstart

    uv sync
    uv run haa demo --workspace workspace     # synthetic dataset, fake PII
    export ANTHROPIC_API_KEY=sk-ant-...
    uv run haa chat --workspace workspace

Try: *"How many unique households per oblast? Disaggregate by sex of head."*
or *"How are we progressing toward the Indicator 1.1 target?"*

## Connecting live sources (KoboToolbox / Ona)

    uv run haa connect kobo        # asks for server URL and API token once;
                                   # the token goes into the OS credential store
    uv run haa pull kobo --form "Household Survey"

Or just ask in the chat: *"Pull fresh submissions of Household Survey from Kobo"*.
Pulled files land in `workspace/data/` and are cleaned/analyzed like any local
export. Only a summary (form name, row count) ever reaches the model.

## Cleaning a dataset

Ask: *"Clean the beneficiaries dataset."* The cleaner agent produces
`beneficiaries_clean.xlsx` plus `workspace/reports/beneficiaries_cleaning_report.md`
(duplicates, impossible values, category normalization — with before/after
numbers). The raw file is never modified.

## Indicators and forms

Ask: *"Extract the indicators from the logframe"* — the designer agent reads
`workspace/project_docs/`, builds `workspace/indicators.yaml` (code, bilingual
name, definition, target, disaggregation, and — when unambiguous — a
machine-readable `measure` block), and validates it against a schema. The
analyst consults that registry when you ask about progress toward a target.

Ask: *"Design a post-distribution monitoring form"* — you get
`workspace/forms/<name>.form.yaml` (the editable source of truth) and
`workspace/forms/<name>.xlsx` (an XLSForm, Ukrainian + English, compiled with
pyxform before it is saved). Upload the .xlsx to Kobo or Ona yourself — this
tool does not deploy forms. Editing works the same way: *"make the phone
question optional in pdm"* re-renders the workbook from the updated model.

## Architecture

    CLI (rich REPL)
      └─ AnalyticsSession  ──ClaudeAgentOptions──►  Claude Agent SDK
           ├─ orchestrator (main loop) ──delegates──► analyst subagent
           ├─ PreToolUse PII hook (deny raw-data reads)
           ├─ in-process MCP server: list_datasets / profile_dataset /
           │    run_analysis / list_project_docs / read_project_doc
           └─ telemetry (JSONL: every turn, tool call, code snippet, cost)
                     │
                     ▼
           local sandbox subprocess (no network, workspace-only,
           PII-stripping load_dataset, output guard)

## Cost control

Sessions carry a budget (`max_budget_usd`, default $2). Spend is tracked from
API result messages and written to the session log; `/cost` shows it live.

## Development

    uv run pytest          # unit tests, no API key needed
    uv run pytest -m api   # smoke evals against the demo dataset (costs money)
    uv run ruff check .

Everything except `tests/test_smoke_api.py` runs offline. CI runs lint + unit
tests on every push.

## Roadmap

**Completed:** Core + analyst agent, connectors/cleaner, indicator registry + XLSForm designer.
**Remaining:** SharePoint connector, reporting agent (5W/MEAL), Power BI engineer, web UI. Design docs live
in `docs/superpowers/specs/`.
