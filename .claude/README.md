# Claude Code harness — `pipeline_data`

Project-local harness. It activates only when Claude Code runs from
`D:\TalentPulse\pipeline_data`. The workspace-root ECC harness
(`D:\TalentPulse\.claude`, 67 agents / 92 commands / 28 hook matchers) sits
*above* this directory and does **not** apply here — running from the repo root
gives you ECC, running from this repo gives you this.

Everything here is tracked in git so the team shares one harness. Per-developer
overrides go in `.claude/settings.local.json` (gitignored).

## Skills (`skills/`)

Auto-surfaced by description; no need to invoke them by name.

| Skill | Covers |
|---|---|
| `dbt-warehouse` | layer/schema conventions, the `fct_jobs_daily` incremental rule, the two-pass `dbt run` order, seeds, the fact that `dbt test` is never run in the pipeline |
| `prefect-flows` | task/flow structure, retry+timeout budget ordering, the `PREFECT_DEPLOY` run-once split, markdown artifacts |
| `warehouse-io` | batching over the tailnet, `execute_values` traps, connection reuse, `raw.crawl_log` queue semantics, upsert/reject rules |
| `add-crawler-source` | the seven touch points for adding a job board, end to end |
| `sync-to-web` | the warehouse→web-box sync and every failure mode it has already hit |
| `pipeline-cicd` | GitHub Actions layout, the `run-flow` composite action, cron/timeout ordering, secrets vs vars, git push rules |

Each skill records *why* a rule exists, usually with the incident behind it.
When a rule changes because reality changed, update the skill in the same commit.

## Hook (`hooks/post_edit.py`)

One `PostToolUse` hook on `Edit|Write|MultiEdit`. **Never blocks** — it always
exits 0 and passes feedback through `hookSpecificOutput.additionalContext`.

- `*.py` → `ruff check --fix` (safe fixes only), then reports anything left.
- `*.py` under `src/` or `orchestration/` → flags `print()` outside the
  `__main__` block, since only `logger` output is captured under Prefect/GHA.
- `dbt_transform/**/*.{sql,yml}` → runs `dbt parse` (~9 s) and reports a
  compilation failure immediately instead of at the next pipeline run.

Binaries are resolved from `.venv/Scripts` first, then `PATH`; if a tool is
missing that check is skipped silently.

**`ruff format` is opt-in.** This codebase is not ruff-formatted — 43 of 63 files
would be rewritten and the hand-aligned collections would explode to one item per
line. Once someone formats the whole repo in a single dedicated commit, set
`TP_HOOK_RUFF_FORMAT=1` to turn it on.

## Plugins

From the official `claude-code-plugins` marketplace
(`https://github.com/anthropics/claude-code.git`), installed at project scope:

- `code-review` — multi-agent review with confidence scoring
- `pr-review-toolkit` — reviewers for tests, error handling, type design, simplification
- `security-guidance` — warns on command injection / unsafe patterns while editing

Deliberately not installed: `frontend-design`, `agent-sdk-dev`,
`plugin-dev`, `ralph-wiggum`, the output-style plugins — nothing in this repo
needs them.

## Permissions

`settings.json` pre-allows read-only git/gh/docker inspection plus `pytest`,
`ruff`, and non-mutating `dbt` subcommands, to cut permission prompts. `dbt run`
and `dbt seed` are intentionally **not** pre-allowed: they write to the shared
warehouse. Force-push is denied.

## Maintenance

- Add a skill: `skills/<name>/SKILL.md` with `name` + `description` frontmatter.
  The description is what triggers it — write it in terms of when to reach for it.
- Change the hook: edit `hooks/post_edit.py`, test it with
  `echo '{"tool_input":{"file_path":"src/foo.py"}}' | python .claude/hooks/post_edit.py`.
- Settings changes apply from the **next** Claude Code session.
