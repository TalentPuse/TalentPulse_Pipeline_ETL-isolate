# Telegram Alerts — User-Facing Job Notifications

**Status**: Phase 1 + 2 MVP (Oct 2026)
**Audience**: end users (job seekers); not ops alerting.

End users subscribe via a Telegram bot to filters like `python hcmc 20m`. After every
`dbt build`, a matcher job scans newly-posted jobs and DMs each subscriber the matches
that hit their filter — at most once per (subscription, job).

---

## 1. Architecture at a glance

```
                            ┌────────────────────────┐
   user → @TalentPulseBot ──┤ src.telegram_bot.bot   │  (long-running)
                            │  /add /list /pause ... │
                            └─────────┬──────────────┘
                                      │  writes
                                      ▼
                   ┌──────────────────────────────────┐
                   │ Postgres schema  user_alerts.*   │
                   │  subscribers / subscriptions     │
                   │  alert_log / pending_links       │
                   └─────────┬─────────────────┬──────┘
                             │ reads           │ idempotency dedup
                             ▼                 ▲
   cron (after dbt build) → src.alerts.match ──┘
                             │  sends
                             ▼
                       Telegram Bot API
```

Two processes, one schema:

| Process | Lifecycle | What it does |
|---|---|---|
| `src.telegram_bot.bot` | long-running daemon | handles user commands, manages subscriptions |
| `src.alerts.match` | cron (after `dbt build`) | finds new matches, sends DMs, dedups via `alert_log` |

---

## 2. One-time setup

### 2.1 Get a bot token

1. Open Telegram → DM **@BotFather**
2. `/newbot` → pick name (e.g. `TalentPulse Jobs`) and username (`TalentPulseJobsBot`)
3. Copy the token — `123456789:AAH...`
4. Recommended: `/setdescription`, `/setabouttext`, `/setcommands`:
   ```
   start - Register and link account
   add - Add a job filter (e.g. python hcmc 20m)
   list - List your active subscriptions
   delete - Delete a subscription by id
   pause - Pause alerts for N days (default 7)
   resume - Resume alerts immediately
   stop - Delete account and all subscriptions
   help - Show command reference
   ```

### 2.2 Apply the schema migration

```bash
docker exec -i talentpulse-postgres psql -U admin -d warehouse \
  < migrations/001_user_alerts_schema.sql
```

The migration is idempotent (`CREATE TABLE IF NOT EXISTS`). It creates schema
`user_alerts` with four tables:

| Table | Purpose | Notes |
|---|---|---|
| `subscribers` | one row per Telegram user | `chat_id` PK, optional `paused_until` |
| `subscriptions` | filter rows (M:1 to subscriber) | arrays for skills/cities/levels/companies + `min_salary_vnd` |
| `alert_log` | dedup ledger | `UNIQUE (subscription_id, source_job_id)` |
| `pending_links` | dashboard → bot deep-link tokens | TTL 10 min |

Skill/city/level/company columns are Postgres `text[]`; GIN-indexed for `&&` overlap
queries.

### 2.3 Set environment variables

In `.env` (or your secret manager):

```
TELEGRAM_BOT_TOKEN=123456789:AAH...
DATABASE_URL=postgresql://admin:password@localhost:5432/warehouse
LOOKBACK_HOURS=24                # how far back the matcher looks
MAX_ALERTS_PER_USER=50           # per-run cap to avoid spam
```

---

## 3. Local smoke test

Terminal 1 — start the bot:

```bash
PYTHONPATH=. .venv/Scripts/python.exe -m src.telegram_bot.bot
```

Terminal 2 — DM your bot:

```
/start
/add python hcmc 20m
/list
```

Terminal 3 — run the matcher manually:

```bash
PYTHONPATH=. LOOKBACK_HOURS=8760 .venv/Scripts/python.exe -m src.alerts.match
```

(Use a high `LOOKBACK_HOURS` for the smoke test so you actually hit the seeded jobs.)
You should receive one DM per matching job, capped at `MAX_ALERTS_PER_USER`.

---

## 4. The `/add` filter grammar

Tokens are space-separated; commas inside a token mean "OR within the same dimension".
Each token is **classified by heuristic** rather than parsed by a strict grammar — this
keeps the UX forgiving.

| Token shape | Classified as | Examples |
|---|---|---|
| city alias | `cities` | `hcmc`, `saigon`, `tphcm`, `hanoi`, `hn`, `danang`, `dn` |
| level alias | `job_levels` | `intern`, `fresher`, `entry`, `junior`, `senior`, `lead`, `manager` |
| number + suffix | `min_salary_vnd` | `20m`, `25M`, `30tr`, `500k`, `1b`, `15.5m` |
| bare number 1–10000 | `min_salary_vnd` (assumed millions) | `20` → 20M VND |
| bare number > 10000 | falls through to `skills` | `10000` → skill literal |
| anything else | `skills` | `python`, `airflow`, `dbt` |

Multi-value: `python,sql,spark hcmc,hanoi senior 30m` is fully supported. When two
salary tokens appear, **max wins** (so `20m 30m` → 30M VND floor).

Empty arrays are normalized to SQL `NULL` ("any") in `to_subscription_args()` — the
matcher treats `NULL` as "no filter on this dimension".

See `src/telegram_bot/filter_parser.py` for the full alias table.

---

## 5. The match SQL

Located in `src/alerts/match.py` as `MATCH_SQL`. Three CTEs:

1. **`job_skills`** — aggregate `silver_skill_long` into a `text[]` per job, lower-cased.
2. **`new_jobs`** — `silver_job_detail` filtered to `posted_at > now() - $1 hours` and
   `is_active`, joined to skills.
3. **outer SELECT** — cross join subscriptions × new jobs, apply per-dimension filters
   using the `IS NULL OR ...` pattern (NULL = "any"), then `NOT EXISTS` against
   `alert_log` for dedup.

Filter operators by dimension:

| Sub column | Operator | Notes |
|---|---|---|
| `skills` (text[]) | `&&` (overlap) | matches if **any** subscribed skill is in the job's skill array |
| `cities`, `job_levels`, `companies` | `= ANY(...)` | exact match against canonical value |
| `min_salary_vnd` | `>= ` | jobs without a parsed salary are excluded when this filter is set |

The matcher also respects `subscribers.paused_until` — paused users get nothing until
their pause expires.

---

## 6. Dedup & rate limiting

### 6.1 Dedup
`alert_log` has `UNIQUE (subscription_id, source_job_id)`. The matcher both:
- **excludes** already-logged matches via `NOT EXISTS` in the SELECT, and
- **inserts** with `ON CONFLICT (subscription_id, source_job_id) DO NOTHING` after each
  send attempt.

This means the matcher is safe to rerun — at worst it's a no-op.

### 6.2 Rate limiting
- Telegram global limit: 30 msg/sec
- Per-chat limit: 1 msg/sec
- We `asyncio.sleep(1.1)` between sends to the same `chat_id`
- `MAX_ALERTS_PER_USER` (default 50) caps a single run; overflow rows are inserted with
  status `skipped_rate_limit` so they aren't retried next run

### 6.3 Exit codes
- `0` — success (zero or more matches sent)
- `1` — config error (missing `TELEGRAM_BOT_TOKEN`)
- `2` — DB connection failure
- `3` — partial failure (some sends failed but were logged)

Use `3` to gate Slack/Sentry on the cron output.

---

## 7. Bot commands (user reference)

| Command | What it does |
|---|---|
| `/start [token]` | Register; if `[token]` provided, link from dashboard |
| `/help` | Command reference (HTML) |
| `/add <filter>` | Add a subscription, e.g. `/add python hcmc 20m` |
| `/list` | Show all your subscriptions with id + status |
| `/delete <id>` | Delete subscription by id (chat_id-scoped) |
| `/pause [days]` | Pause alerts for N days (default 7) |
| `/resume` | Clear pause |
| `/stop` | Inline-confirm → delete account + cascade subscriptions |

---

## 8. Production deployment

### 8.1 Bot service (long-running)
Add to `docker-compose.prod.yml`:

```yaml
  telegram-bot:
    build: .
    command: python -m src.telegram_bot.bot
    environment:
      DATABASE_URL: postgresql://admin:${POSTGRES_PASSWORD}@postgres:5432/warehouse
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}
    depends_on: [postgres]
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 200M
```

Long-polling works fine for a single replica. For HA, switch to webhook mode behind
Caddy (out of scope for MVP).

### 8.2 Matcher cron
After every `dbt build`, run the matcher. Two options:

**Option A — systemd timer** (recommended for slim VPS):
```
# /etc/systemd/system/talentpulse-match.service
[Service]
Type=oneshot
WorkingDirectory=/opt/talentpulse/pipeline_data
EnvironmentFile=/opt/talentpulse/.env
ExecStart=/opt/talentpulse/.venv/bin/python -m src.alerts.match

# /etc/systemd/system/talentpulse-match.timer
[Timer]
OnCalendar=*:0/30          # every 30 min
Persistent=true
[Install]
WantedBy=timers.target
```

**Option B — chained with dbt** (when an orchestrator is present):
```
dbt build && python -m src.alerts.match
```

Either way, `LOOKBACK_HOURS` should comfortably exceed your run cadence (default 24h
for a 30-min cron is generous).

---

## 9. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Bot doesn't respond | wrong token / not started | check `TELEGRAM_BOT_TOKEN`, tail bot logs |
| No matches but jobs exist | `LOOKBACK_HOURS` too low | bump it; check `silver_job_detail.posted_at` |
| `column "skill_name" does not exist` | wrong column name | the silver column is `skill_name_norm` |
| Same job sent twice | `alert_log` row missing | check unique constraint, verify INSERT ran |
| Telegram 429 | rate limit | confirm `PER_CHAT_DELAY_SEC` ≥ 1.0; lower `MAX_ALERTS_PER_USER` |
| `/add 20` becomes salary not skill | bare-number heuristic | use `/add "20"` … or document for users |

---

## 10. Out of scope (next iterations)

- **Webhook mode + HA** — needed past ~10k DAU
- **Inline filter editing** — currently delete + re-add
- **Per-subscription quiet hours** — only global pause for now
- **Digest mode** — bundle matches into one daily message (currently one DM per match)
- **Match-quality scoring** — surface "top 3 best matches" rather than chronological
- **Dashboard deep-link** — `pending_links` table is in place but UI not built
