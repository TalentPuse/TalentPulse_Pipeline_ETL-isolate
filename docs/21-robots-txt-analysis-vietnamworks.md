# 21 — VietnamWorks `robots.txt` Analysis

**Source:** `https://www.vietnamworks.com/robots.txt`
**Fetched at:** 2026-04-22
**Prepared for:** TalentPulse Pipeline — crawler compliance & scope decision for Phase 3 (detail-page crawling)

---

## 1. Raw content (verbatim)

```
User-agent: *

Disallow: /my-profile
Disallow: /my-profile/
Disallow: /ho-so/

Disallow: /my-career-center
Disallow: /my-career-center/
Disallow: /quan-ly-nghe-nghiep
Disallow: /quan-ly-nghe-nghiep/

Disallow: /dang-nhap/?*
Disallow: /login/?*

# Block all robots from restricted areas
Disallow: /jobseekers/apply_online.php?*
Disallow: /viec-lam/nop-ho-so-truc-tuyen/
Disallow: /jobs/apply-job-online/
Disallow: /jobseekers/apply_on_oneclick.php

# Block all robots from internal actions
Disallow: /jobseekers/jobdetail_print.php?*
Disallow: /jobseekers/open_authenticate.php?*
Disallow: /jobseekers/checkAuthenticate.php*
Disallow: /company/preview/*

# Block all robots from AJAX actions
Disallow: /jobseekers/ajax.php?*

# Block all robots from advertisement
Disallow: /vclick/index.php?*
Disallow: /wow-cv/render/

# Block all robots from hrinsider author
Disallow: /hrinsider/author/*
Disallow: /hrinsider/category/*/page/*
Disallow: /hrinsider/podcast-categories/*/page/*

Sitemap: https://www.vietnamworks.com/sitemap/sitemap.xml
```

---

## 2. Executive summary

| Question | Answer |
|---|---|
| Is there a blanket block on crawlers? | **No.** `User-agent: *` is used but with selective `Disallow` rules only. |
| Is the job listing search page allowed? | **Yes** — `/job-search`, `/viec-lam` and friends are not listed under `Disallow`. |
| Are job detail pages (`/{slug}-{jobId}-jv`) allowed? | **Yes** — no rule covers the JD detail URL pattern. |
| Is the internal search API (`ms.vietnamworks.com/job-search/v1.0/search`) covered? | **Out of scope** — `robots.txt` on `www.` subdomain does not govern the `ms.` subdomain. Each host has its own robots file. |
| Is there a `Crawl-delay` directive? | **No.** No rate guidance declared — we must choose a polite value ourselves. |
| Is there a sitemap? | **Yes** — `https://www.vietnamworks.com/sitemap/sitemap.xml` (1.5 KB index, HTTP 200). |
| Is our current pipeline compliant? | ✅ **Yes** (listing crawler uses `ms.` API; not listed here). |
| Is the planned **detail crawler** compliant? | ✅ **Yes** (fetches `/{slug}-{jobId}-jv` which is not disallowed). |

---

## 3. Disallow rules — categorised

### 3.1 User account / authenticated area
```
/my-profile, /my-profile/, /ho-so/
/my-career-center, /my-career-center/, /quan-ly-nghe-nghiep, /quan-ly-nghe-nghiep/
/dang-nhap/?*, /login/?*
```
**Purpose:** Block indexing of logged-in candidate dashboards and login flows.
**Relevance to us:** None — we never authenticate.

### 3.2 Apply / submission endpoints
```
/jobseekers/apply_online.php?*
/viec-lam/nop-ho-so-truc-tuyen/
/jobs/apply-job-online/
/jobseekers/apply_on_oneclick.php
```
**Purpose:** Prevent bots from triggering application submission paths.
**Relevance to us:** None — we only read listings and JDs.

### 3.3 Internal/legacy PHP actions
```
/jobseekers/jobdetail_print.php?*
/jobseekers/open_authenticate.php?*
/jobseekers/checkAuthenticate.php*
/company/preview/*
/jobseekers/ajax.php?*
```
**Purpose:** Legacy admin / AJAX utility endpoints.
**Relevance to us:** None.

### 3.4 Ads / tracking
```
/vclick/index.php?*
/wow-cv/render/
```
**Purpose:** Prevent crawlers from hitting click-tracking & render endpoints.
**Relevance to us:** None.

### 3.5 HR Insider (blog) pagination & author pages
```
/hrinsider/author/*
/hrinsider/category/*/page/*
/hrinsider/podcast-categories/*/page/*
```
**Purpose:** SEO hygiene — avoid indexing thin author/paginated pages.
**Relevance to us:** None.

---

## 4. What is **NOT** disallowed (and therefore permitted)

| Path pattern | Our usage |
|---|---|
| `/job-search?...` | Human-facing search page (not used — we hit the API). |
| `/viec-lam-*` | Vietnamese listing landing pages (not used). |
| `/{slug}-{jobId}-jv` | **JD detail page — target of Phase 3 detail crawler.** |
| `/nha-tuyen-dung/*`, `/cong-ty/*` | Company profile pages (optional future scope). |
| `/sitemap/sitemap.xml` + sub-sitemaps | Allowed — can be used for job ID discovery. |
| `/hrinsider/*` (article pages) | Allowed — not our scope. |

---

## 5. Subdomain note — important

`robots.txt` is per-host. This file covers only `www.vietnamworks.com`. It does **not** bind:

- `ms.vietnamworks.com` — where our listing search API lives (`/job-search/v1.0/search`).
- `images.vietnamworks.com` — CDN for logos/photos.

We should also fetch:
- `https://ms.vietnamworks.com/robots.txt`
- `https://images.vietnamworks.com/robots.txt`

before treating those hosts as "policy-clear". (Not done in this report — flagged as follow-up.)

---

## 6. Crawl-delay — no directive, so we set our own

Since the site does not declare `Crawl-delay`, we follow community-standard politeness for a Vietnamese jobs portal:

| Operation | Current setting | Recommended setting | Rationale |
|---|---|---|---|
| Listing API (`ms.` host) | `time.sleep(2s)` after each page | Keep 2 s | API calls are cheap for them; 2 s is conservative. |
| Detail HTML (`www.` host) | (not yet built) | **1.5–3 s** between requests, jittered | HTML pages are heavier (~86 KB); stay well under any hidden rate limit. |
| Concurrency | 1 worker | **1 worker** (no parallel per host) | No `User-agent`-specific allowance → single-threaded is safest. |
| Time window | Business-hours crawl | **Off-peak** 02:00–05:00 local | Matches existing DAG (2 AM crawl, 2:30 AM parse). |
| Retry on 429/5xx | (none) | `tenacity` 3 retries, exponential backoff 4 s → 32 s | Back off aggressively if we ever get throttled. |

**Estimated load for MVP:** ~90 detail pages/day × 2 keywords ≈ 180 GETs/day at 2 s interval = ~6 minutes of traffic. Negligible.

---

## 7. Legal & ToS considerations (beyond robots.txt)

`robots.txt` is a **technical advisory**, not a contract. In addition, we must honour:

1. **Terms of Service** of `www.vietnamworks.com` — not reviewed in this report. Action: legal/product review before scaling beyond MVP or monetising.
2. **Personal data** — JDs may contain contact emails/phones of recruiters. Under Vietnam's PDPL 2023 and EU GDPR (if EU users), storing personal data requires lawful basis. Action: **strip contact fields** in the Silver layer unless business-justified.
3. **Copyright** — JD text is the employer's copyrighted content. Safe patterns: store raw for internal analytics only; for public outputs (alerts, insights), summarise or link back — do not republish full text.
4. **Rate limit / fair use** — stay well below thresholds; identify ourselves with a descriptive `User-Agent` including contact email once we scale.

---

## 8. Sitemap — worth using

`Sitemap: https://www.vietnamworks.com/sitemap/sitemap.xml` — verified:
- HTTP 200, 1 495 bytes, `Content-Type: text/xml`.
- This is a **sitemap index** (size suggests it links to sub-sitemaps).

**Opportunity:** The sitemap likely lists all active job URLs. Using it as a discovery source in addition to the search API would:
- Catch jobs whose keyword/location doesn't match `Data Engineer`/`AI Engineer` but is still relevant.
- Provide a stable fallback if the `ms.` search API changes.

**Proposed usage:** Nightly fetch sitemap → extract `*-jv` URLs → feed `crawl_log` → diff against already-crawled set → queue new IDs for detail crawler.

---

## 9. Compliance checklist for Phase 3 detail crawler

| Requirement | Status |
|---|---|
| Target URL pattern (`/{slug}-{jobId}-jv`) not disallowed | ✅ |
| Do not hit any apply/login/ajax endpoint | ✅ (will only GET detail HTML) |
| Crawl-delay ≥ 1.5 s, single worker | ☐ (enforce in code) |
| Descriptive `User-Agent` with contact | ☐ (currently a generic Chrome UA) |
| Respect `429`, `503` with exponential backoff | ☐ (add `tenacity`) |
| Track crawled IDs to avoid re-fetch | ☐ (Phase 3.2 `crawl_log` table) |
| Strip PII (recruiter email/phone) before Silver | ☐ (Phase 4 normaliser) |
| No republishing of full JD text externally | ☐ (policy for alert/insight outputs) |
| Check `ms.vietnamworks.com/robots.txt` separately | ☐ (follow-up) |
| Review VNW Terms of Service before scale-out | ☐ (pre-production blocker) |

---

## 10. Conclusion

`robots.txt` on `www.vietnamworks.com` **permits the planned detail crawler**. All `Disallow` entries cover authenticated flows, apply/submission endpoints, internal PHP actions, ads, and SEO noise — none touch the public job-listing or JD detail paths.

There is **no `Crawl-delay` directive**, so TalentPulse will self-impose a polite **1.5–3 s interval with a single worker** and exponential backoff on errors. The declared **sitemap** is accessible and should be incorporated as a secondary discovery source.

Three items remain outside the scope of `robots.txt` but must be addressed before scale: (1) a review of the `ms.` and `images.` subdomain robots files, (2) VNW **Terms of Service** review, and (3) **PII handling** policy in the Silver layer.

---

*Report end — filed as `docs/21-robots-txt-analysis-vietnamworks.md`.*
