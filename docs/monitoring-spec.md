# Spec: Monitoring & Security Detection for TalentPulse

**Status:** specification for research + implementation. Nothing is built yet.
**Read first:** `architect.md`. The design below only makes sense once you know that
the VPS does not run the pipeline.
**Researched:** 2026-07-12. Every number in §3 is cited — re-check them, free tiers move.

---

## 1. Why this exists

Nobody can currently answer any of these:

- Did the crawl run this morning?
- Did last night's backup actually succeed?
- Was Postgres OOM-killed at 3am?
- Is anyone brute-forcing the login page?
- Is someone burning our OpenRouter budget?

That is the problem. Not "we lack a SIEM" — we lack the ability to answer a handful of
specific questions about a small system.

**The trap to avoid.** TalentPulse is 2 VPSs and ~58 users, operated by one person. A
Security Operations Centre is built for hundreds of endpoints and a rotating staff.
Deployed here it produces alert fatigue for an audience of one, and the alerts get muted
within a week. The goal is the 20% of SOC practice that pays for itself — and an explicit
refusal of the rest.

---

## 2. Constraints (these kill most of the obvious answers)

1. **RAM is the binding constraint.** The warehouse box is 4 GB and already runs Postgres
   (1 GB) + Metabase (1 GB) + Prefect (512 MB). Anything needing ≥500 MB is disqualified
   on arrival.
2. **A monitor running on the box cannot report that the box is down.** This rules out
   *self-hosted-only* uptime monitoring, however light.
3. **The interesting signals are not publicly reachable.** Postgres and Prefect are bound
   to `127.0.0.1` and exposed only over Tailscale, so an external SaaS prober cannot see
   them — and therefore cannot answer "is the data fresh".
4. **We already own two thirds of the plumbing.** GitHub Actions gives us free ephemeral
   compute that already reaches the VPS over Tailscale, and the Telegram bot is an alert
   channel users already trust. A design that ignores these is probably wrong.

Constraints 2 and 3 point in **opposite directions**. That tension is the heart of the
design: no single tool satisfies both, so stop looking for one.

---

## 3. Options researched

| Option | RAM on our VPS | Free tier | Verdict |
|---|---|---|---|
| **Wazuh** (SIEM/XDR) | 4 GB *minimum*; realistically 8 GB + 50 GB disk | free, self-hosted | **Rejected.** Wants more RAM than the entire warehouse box has. Also: a SIEM with one analyst is a log archive with extra steps. |
| **Uptime Kuma** (self-hosted) | ~100 MB idle, 80–200 MB typical | free | **Rejected as the alarm.** Cheap, but breaks constraint 2 — it cannot tell you the host it lives on has died. Reconsider later as a *status page*. |
| **Grafana Cloud Free** | **0** | 10k active series · **50 GB logs/month** · 14-day retention · 3 users · no credit card | **Strong candidate for logs.** 50 GB/month dwarfs our volume. The 14-day retention is the real constraint — see §6. |
| **UptimeRobot Free** | 0 | 50 monitors, 5-min interval | Candidate for external uptime. A 3-minute outage can be invisible at 5-min checks. |
| **Better Stack Free** | 0 | 10 monitors, 3-min interval, status page, incident timeline | Candidate. We need ~4 monitors, so the lower count costs us nothing. |
| **GitHub Actions as the agent** | 0 | already have it | **Recommended for internal checks.** The only option that reaches the tailnet-only services *and* costs nothing. |

Sources:
[Grafana Cloud free tier](https://grafana.com/products/cloud/free-tier/) ·
[Wazuh install requirements](https://documentation.wazuh.com/current/installation-guide/wazuh-server/index.html) ·
[Uptime Kuma](https://github.com/louislam/uptime-kuma) ·
[Better Stack vs UptimeRobot](https://betterstack.com/community/comparisons/better-stack-vs-uptimerobot/)

---

## 4. Proposed design — four layers, ~0 MB on the VPS

```
  L1  EXTERNAL PROBE  (SaaS, free)            "is the site up?"
      Better Stack / UptimeRobot ─────────▶   https://talentpuse.io.vn
      Lives OUTSIDE our infra, so it still works when our infra doesn't.

  L2  INTERNAL CHECKS  (GitHub Actions, every 15 min, over Tailscale)
      ├─ Postgres reachable
      ├─ dbt_dev_gold.fct_jobs_daily: max(snapshot_date) < 2 days old
      ├─ Prefect API alive
      ├─ backend /health alive            (exists: backend main.py:119, mcp server.py:31)
      └─ newest object in s3://talentpulse-backup/db/ is < 26h old
      Reaches what SaaS cannot. Costs no VPS RAM.

  L3  HOST CHECKS  (self-hosted runner — it ALREADY runs on the warehouse box)
      ├─ RAM / swap pressure
      ├─ disk %
      ├─ OOM kills in dmesg
      └─ any container not Up
      No agent to install, no port to open.

  L4  LOGS + DETECTION  (Grafana Cloud Free, 0 MB on VPS)
      backend (JSON) + postgres + docker ──▶ Loki ──▶ alert rules
                                                       │
  ALL ALERTS ──────────────────────────────────────────┴──▶ Telegram bot (already exists)
```

**Why L3 is the elegant part:** the self-hosted GitHub Actions runner (`talentpulse_pa`)
is *already installed on the warehouse box* to run deploys. A workflow job targeting it
reads `free`, `df`, `dmesg`, `docker ps` locally — no monitoring agent, no new port, no
new RAM. It falls out of the existing architecture for free, and it is easy to miss.

---

## 5. What to detect, and why each earns its place

Ops and security are not two lists here. Several of the highest-value security signals
are operational signals wearing a different hat.

| Signal | Ops meaning | Security meaning |
|---|---|---|
| No new backup in R2 for >26h | the job broke | **someone is attacking our ability to recover** |
| Objects *deleted* from the backup bucket | — | **active attack on recovery.** Page immediately |
| Gold data not refreshed in >2 days | crawl broke / source blocking us | scraper detected, or pipeline compromised |
| **OpenRouter spend spikes** | more users | **someone is burning our LLM budget** — and there is no rate limit today |
| N login failures on one account in 5 min | — | brute force |
| `is_admin` flips true on any account | — | **privilege escalation** |
| New device joins the tailnet | — | the tailnet is the *only* path to the DB |
| SSH login from a new IP | — | host compromise |
| Postgres OOM-killed | data loss mid-write | resource-exhaustion attack |

**Ship the top five first.** A rule that fires and gets muted is worse than no rule.

---

## 6. Open questions for the researcher

The decisions I could not make from a desk, in priority order.

1. **Log retention vs the 14-day free-tier ceiling.** Grafana Cloud Free keeps logs 14
   days. Breach investigations routinely reach further back — and Vietnam's **Decree
   13/2023/NĐ-CP** on personal data applies to us: we store CVs with names, emails, phone
   numbers and work history. Determining the *scope* of a breach is an obligation.
   **Is 14 days enough to meet it?** If not, the cheap fix is to also ship raw logs to R2
   (we already have it, and at our volume it is nearly free) and treat Grafana as the
   *query* layer over a longer archive. **Verify the legal requirement — do not guess.**
2. **Where does OpenRouter spend come from?** Is there a usage API or webhook, or must we
   instrument the backend to count tokens per request? This is the cost-DoS detector, and
   the answer decides how much work it is.
3. **UptimeRobot (50 monitors / 5 min) vs Better Stack (10 / 3 min).** We need ~4 monitors,
   so the count is irrelevant; the interval and the incident tooling decide it. Does the
   2-minute difference actually matter to us? Probably not — say so, and pick one.
4. **Alert routing.** Everything into the one Telegram bot, or split ops from security?
   One channel is simpler and far more likely to be read. Recommend one, with severity in
   the message text.
5. **Does Metabase go behind Tailscale?** It is currently a public admin panel on port
   3000 over **plain HTTP** — credentials in cleartext. This is arguably more urgent than
   any detection work: monitoring an unlocked door leaves it unlocked.

---

## 7. Explicit non-goals

Saying no is most of the value here.

- **No SIEM** (Wazuh, Elastic, Splunk). Needs more RAM than the box has, and generates an
  alert volume one person cannot triage.
- **No EDR / host agents.** Nothing to defend at this scale.
- **No threat hunting, no Tier 1/2/3 rota.** There is no rota. There is one person.
- **No self-hosted Prometheus + Loki + Grafana on the VPS.** ~500 MB+, and it dies
  alongside the box it is meant to be watching.

Revisit all of it at a few thousand users, or when someone is paid to watch it.

---

## 8. Sequencing

Detection is worthless without the ability to act, and monitoring a system with no rate
limiting is a burglar alarm on a house with no door. Therefore:

1. **Split the R2 token.** (Cloudflare, ~10 minutes, human task.) One token currently
   covers the CV bucket, the raw bucket **and the backup bucket** — and it lives in the
   backend, the most exposed component we have. Compromise the backend and the attacker
   deletes the backups. This blocks the unrecoverable scenario, so it goes first.
2. **Metabase off the public internet; SSH hardening** (`fail2ban`, key-only auth,
   `unattended-upgrades`).
3. **Close the backend holes** — rate limiting, HTML escaping in Telegram alerts, CV
   upload validation + parse timeout. (Detailed in the security review; not repeated here.)
4. **Then** L1–L3 monitoring — roughly a day of work.
5. **Then** L4 logs plus the first five detection rules.
6. **Then** four incident playbooks: leaked JWT secret, leaked R2 key, compromised VPS,
   CV data breach. Half a page each. **A detection with no playbook just means you find
   out sooner and still don't know what to do.**

---

## 9. Acceptance criteria

Done when these can be answered without logging into anything:

- [ ] Site went down at 3am → paged within 5 minutes.
- [ ] Backup silently stopped running → knew within 26 hours.
- [ ] Someone deleted objects from the backup bucket → paged immediately.
- [ ] The crawl stopped producing fresh data → knew within 2 days.
- [ ] Postgres was OOM-killed → paged, and we know how close to the ceiling we run.
- [ ] Someone tried 50 passwords on one account → paged.
- [ ] LLM spend tripled overnight → paged.

And for every one of them: **a written playbook saying what to do next.**
