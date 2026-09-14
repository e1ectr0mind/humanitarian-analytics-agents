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

### SharePoint / OneDrive

    haa connect sharepoint --site https://<tenant>.sharepoint.com/sites/<name> \
        --tenant <tenant>.onmicrosoft.com --folder "Shared Documents/5W"

Sign-in is the standard Microsoft device-code flow: the CLI prints a code, you
enter it at microsoft.com/devicelogin and log in with your normal work account
(password and MFA stay on Microsoft's page — the tool only receives tokens,
stored in the OS credential store). Use `--site onedrive` for the personal
drive. Pulls download `.xlsx`/`.csv` files byte-for-byte into
`workspace/data/`; a pasted SharePoint file link also works as the pull target.
If the tenant blocks the default client id, pass your own with `--client-id`
(ask IT/HQ to approve one — read-only Files/Sites scopes).

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

## Reports

Ask: *"Build the indicator progress report"* — the reporter agent runs a
deterministic engine over every indicator in `workspace/indicators.yaml` that
has a `measure` block and writes `workspace/reports/indicators_<date>.md` and
`.xlsx`: target, actual, % progress, and a breakdown per disaggregation column.
Indicators it cannot compute are listed with the reason — nothing is estimated.

Ask: *"Make a 5W matrix"* — the reporter proposes `workspace/5w.yaml` (which
columns are Where, When, What and Whom) from the dataset profile, then writes
`workspace/reports/5w_<date>.md` and `.xlsx`. Edit `5w.yaml` by hand and rebuild
any time.

Both reports prefer `<dataset>_clean.xlsx` when the cleaner has produced one,
take an optional reporting period, and also run without the LLM (no API key):

    uv run haa report indicators --period 2026-06-01..2026-08-31 --date-field submission_date
    uv run haa report 5w --period 2026-06-01..2026-08-31

## Architecture

    CLI
      ├─ `haa chat` ─► AnalyticsSession ──ClaudeAgentOptions──► Claude Agent SDK
      │        ├─ orchestrator (main loop) ──delegates──►┬─ analyst subagent
      │        │                                         ├─ cleaner subagent
      │        │                                         ├─ designer subagent
      │        │                                         └─ reporter subagent
      │        ├─ PreToolUse PII hook (deny raw-data reads)
      │        ├─ telemetry (JSONL: every turn, tool call, code snippet, cost)
      │        └─ four in-process MCP servers:
      │             ├─ data    — list_datasets / profile_dataset / run_analysis /
      │             │             list_project_docs / read_project_doc
      │             ├─ sources — list_connections / list_remote_forms / pull_form
      │             ├─ forms   — list_local_forms / load_form / save_form /
      │             │             read_indicators / save_indicators
      │             └─ reports — compute_indicators / build_indicator_report /
      │                           read_5w_mapping / save_5w_mapping / build_5w
      │                  │
      │                  ▼
      │        local sandbox subprocess (no network, workspace-only,
      │        PII-stripping load_dataset, output guard) — used by the
      │        analyst and cleaner via `run_analysis`
      └─ `haa report` ─► the same deterministic reports engine directly,
               no LLM/API key involved, exit code 0/1 for scripting

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

**Completed:** Core + analyst agent, connectors/cleaner, indicator registry + XLSForm designer, SharePoint connector, deterministic reports (indicator progress, 5W).
**Remaining:** narrative reports (donor narrative, dataset summary as docx), Power BI engineer, web UI. Design docs live
in `docs/superpowers/specs/`.
