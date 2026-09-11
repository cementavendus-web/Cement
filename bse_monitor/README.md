# BSE Filings Monitor

A three-layer system for anticipating **share sales in Indian listed companies**.

1. **Calendar** — a forward register of statutory deadlines (anchor and promoter
   lock-in expiries, MPS compliance, QIP resolution expiry, post-results trading
   window reopen). These are knowable in advance and carry the most signal.
2. **Events** — every new Regulation 30 filing is classified by a weighted-lexicon
   rule engine and, for the ones that matter, read by **Claude Haiku** into strict
   JSON (`event_class, holder, holder_type, stake_pct, effective_date, confidence`).
3. **Model** — a company × holder × week panel labelled from a decade of realised
   sell events, ranked by LightGBM against a transparent baseline, evaluated
   walk-forward.

The daily output is a ranked CSV emailed at **08:00 IST**: company, holder, stake
in ₹cr, days to the nearest hard trigger, and the top-3 model reasons.

```
python -m bse_monitor.main daily-brief
```

| Layer | Table | Command |
|---|---|---|
| 1 Calendar | `upcoming_triggers` | `calendar refresh` · `calendar list` |
| 2 Events | `llm_extractions` | `llm status` · `llm dry-run` |
| 3 Model | `disposals`, `daily_quotes` | `model panel` · `model train` · `model evaluate` |
| Output | — | `daily-brief` |

---

## 1. Architecture

```
                    ┌──────────────────────────────────────────────────┐
                    │  SOURCES                                         │
                    │  BSE AnnSubCategoryGetData API  (primary)        │
                    │  BSE AttachLive / AttachHis PDFs                 │
                    │  NSE corporate-announcements    (enrichment)     │
                    │  SEBI public-issue filings      (enrichment)     │
                    └────────────────────┬─────────────────────────────┘
                                         │  RawFiling
        ┌────────────────────────────────▼─────────────────────────────┐
        │ 1. INGEST        scraper/                                    │
        │    · rate-limited HTTP, jittered exponential backoff         │
        │    · Playwright fallback when the cookie wall trips          │
        │    · incremental window from the last checkpoint             │
        └────────────────────────────────┬─────────────────────────────┘
        ┌────────────────────────────────▼─────────────────────────────┐
        │ 2. NORMALISE     parser/ + database/repository               │
        │    · markup stripping, ligature/punctuation repair           │
        │    · company resolution (BSE code → ISIN → normalised name)  │
        │    · content_hash = sha256(company|headline|date) → dedupe   │
        └────────────────────────────────┬─────────────────────────────┘
        ┌────────────────────────────────▼─────────────────────────────┐
        │ 3. ENRICH        pdf_processor/                              │
        │    · content-addressed download (sha256 filenames)           │
        │    · pdfplumber → PyMuPDF → Tesseract OCR escalation         │
        │    · table extraction → allottee / shareholding rows         │
        └────────────────────────────────┬─────────────────────────────┘
        ┌────────────────────────────────▼─────────────────────────────┐
        │ 4. CLASSIFY      classifier/ + parser/entities               │
        │    · weighted-lexicon rules → category + confidence          │
        │    · negation, boilerplate and disambiguation guards         │
        │    · optional TF-IDF + LogReg model, conservatively blended  │
        │    · entities: ₹ amounts, shares, price, %, dates, investors │
        │    · Opportunity Score 0-100 with a stored breakdown         │
        └────────────────────────────────┬─────────────────────────────┘
        ┌────────────────────────────────▼─────────────────────────────┐
        │ 5. PERSIST & ACT  database/ · reports/ · alerts/              │
        │    · PostgreSQL upsert (idempotent)                          │
        │    · Daily Events / Fund Raise / Sell-Down pipelines         │
        │    · queue → deliver alerts (retry-safe)                     │
        └──────────────────────────────────────────────────────────────┘
```

### Layout

```
bse_monitor/
├── scraper/         BSE / NSE / SEBI readers, HTTP client, retry + rate limit
├── parser/          text normalisation, entity extraction
├── pdf_processor/   download, text extraction with OCR fallback, tables
├── classifier/      rule engine, optional ML layer, opportunity scoring
├── database/        SQLAlchemy models, session, repository, schema.sql
├── alerts/          formatting, channels (email/slack/teams/csv), dispatcher
├── reports/         daily events, fund-raise and sell-down pipelines
├── configs/         config.yaml, keywords.yaml, investors.yaml
├── examples/        sample filings + generated example outputs
├── tests/           141 offline tests
├── docker/          Dockerfile, docker-compose.yml
├── pipeline.py      orchestration
├── scheduler.py     APScheduler jobs (+ equivalent crontab)
└── main.py          CLI
```

---

## 2. Data flow, and how detection actually works

### 2.1 Ingest

BSE's announcements page is a thin shell over a JSON endpoint:

```
https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w
  ?pageno=1&strCat=Company+Update&strPrevDate=20260909&strToDate=20260911
  &strType=C&strSearch=P&subcategory=-1
```

It rejects requests without a `Referer: https://www.bseindia.com/` and,
intermittently, without a session cookie minted by loading the HTML page. The
scraper warms up the session, pages per category, and — only when the API keeps
refusing — re-issues the same XHR from inside a real Chromium context via
Playwright. The endpoint has shipped at least three response envelopes
(`Table`, `Data`, bare list), so parsing is shape-tolerant and every field
lookup is case-insensitive.

The window is incremental: it starts from the previous successful run's
`to_date` minus a one-day overlap, because BSE occasionally back-dates a
dissemination timestamp and a zero-overlap window would lose those filings.

### 2.2 Deduplication

The same announcement arrives repeatedly — listed under two BSE categories,
re-disseminated with a corrected timestamp, filed with NSE as well. The identity
key is therefore content-based, not id-based:

```
content_hash = sha256(normalised_company | normalised_headline | filing_date)
```

Company names are normalised by dropping corporate suffixes, so `Reliance
Industries Ltd.` and `RELIANCE INDUSTRIES LIMITED` collapse to one key; headlines
drop the `- XBRL` suffix BSE appends to structured duplicates. A re-scrape of an
already-enriched filing short-circuits before the PDF download.

### 2.3 PDF processing

Most of the detail lives in the attachment, not the headline. The chain
escalates on **quality**, not just on exceptions: if the text layer yields fewer
than ~120 characters per page, the document is treated as scanned and sent to
OCR even though extraction nominally "succeeded" — signed board resolutions are
routinely scans. Files are stored as `<sha256>.pdf`, so an attachment linked
from two filings is fetched and parsed exactly once.

### 2.4 Detecting capital raises and block deals

Two independent signals per filing.

**What happened — the rule engine.** Each category carries a weighted lexicon
(strong 10 / medium 5 / weak 2). The engine sums the weight of each distinct
matched term, normalises by a per-category saturation constant, and turns the
result into comparable confidences. Three guards keep it honest:

| Guard | Problem it solves |
|---|---|
| **Longest-match consumption** | `qualified institutions placement` must not also credit bare `qip` |
| **Negation** | *"The Board **did not approve** the proposed fund raise"* → demoted to `Other` |
| **Boilerplate stripping** | Regulation 30 footers stop contributing evidence |
| **Disambiguation rules** | An anchor lock-in notice necessarily recites *"allotted in the Initial Public Offering"*; the rule demotes `IPO` and promotes `InvestorExit`, and records that it did so |

Filings are formulaic legal prose written to a SEBI template, so the signal is
lexical and stable — which is why rules lead and the model follows. Every
verdict carries the terms that produced it, so a wrong call is diagnosable
rather than mysterious.

**How actionable it is — the scoring model.**

```
bonus    = certainty + deal_size + marquee_investor + promoter + stake% + lock_in
headroom = 100 - base(category)
score    = base + headroom × (bonus / max_bonus) − recency_decay − low_confidence
```

Bonuses are projected into the headroom *above* the base rather than added to
it. Adding them raw pushes every high-base category (promoter sale 95, QIP 90)
straight to a clamped 100, collapsing exactly the distinctions the score exists
to make.

| Signal | Effect |
|---|---|
| Base: promoter sale 95 · OFS 92 · QIP 90 · investor exit 90 · block deal 88 · fund raise 85 · lock-in 90-band | sets the band |
| Certainty: completed / **approved** / intended | +6 / +8 / +3 |
| Deal size ≥ ₹5,000 / 1,000 / 250 / 50 Cr | +10 / +7 / +4 / +2 |
| Marquee investor (tracked PE/VC/sovereign) | +5 |
| Promoter actually transacting | +5 |
| Stake > 5% of equity | +5 |
| Lock-in expiring within 45 days | +4 |
| Filing age | −1.5/day, capped at −12 |

Every component is stored in `score_breakdown`, so any ranking decision is
explainable after the fact.

**Sell-down detection** keys on intent verbs (`intends to sell`, `proposes to
divest`, `sell-down`, `offer for sale`) co-occurring with a promoter or
investor entity, plus lock-in expiry dates extracted from anchor-allotment
filings. That is how a company enters the Sell-Down Pipeline *before* the block
prints, rather than after.

### 2.5 Entity extraction

Regex-first, spaCy-second. Indian filings express money in a small, regular set
of shapes (`Rs. 1,200 crore`, `INR 1,200 Cr`, `₹ 12,00,00,000`), and a tuned
regex beats a general NER model on both precision and cost. Two traps handled
explicitly:

* **The largest number is usually not the deal.** Cue matching is scoped to the
  sentence containing the figure, so *"authorised share capital stands at
  Rs. 5,000 crore"* does not outrank *"aggregating up to Rs. 1,200 crore"*.
* **A per-share price is not a deal size.** `Rs. 745 per equity share` is
  rejected as a value; when only a price and a share count exist, the deal value
  is *derived* (`2,50,00,000 × 745 = ₹1,862.5 Cr`) and flagged as derived.

spaCy is used only for the open-ended part — discovering institutional names not
in the registry — and falls back to a capitalised-n-gram scan plus an
institutional-suffix filter when the model is not installed.

---

## 3. Database schema

PostgreSQL, 11 tables. Full DDL in [`database/schema.sql`](database/schema.sql);
ORM in [`database/models.py`](database/models.py).

| Table | Purpose |
|---|---|
| `companies` | Master record; BSE code / NSE symbol / ISIN / normalised name |
| `event_filings` | One announcement: category, confidence, score, breakdown, priority, `content_hash` (unique) |
| `filing_documents` | Attachments: sha256, extraction method, OCR flag, text, tables |
| `investors` | Tracked + discovered institutions, marquee flag, aliases |
| `promoters` | Per-company promoters and promoter group |
| `filing_investors` / `filing_promoters` | Mentions, with context and confidence |
| `transactions` | Shares, price, amount, stake %, issue size, lock-in / board-meeting / record dates |
| `daily_alerts` | Alert queue with per-(filing, channel) dedupe key and delivery state |
| `scrape_runs` | Audit trail **and** incremental checkpoint store |
| `audit_log` | Append-only record of every create/update |

The schema stays SQLite-compatible (JSON variant types, CHECK constraints rather
than native enums) so the whole test suite runs in-memory with no services.

---

## 4. Quick start

```bash
pip install -r bse_monitor/requirements.txt
python -m spacy download en_core_web_sm          # optional
playwright install --with-deps chromium          # optional

export BSE_DATABASE_URL="postgresql+psycopg2://bse:bse@localhost:5432/bse_monitor"
python -m bse_monitor.main init-db
python -m bse_monitor.main run --days 2
python -m bse_monitor.main report selldown
```

**No database or network?** The whole pipeline runs offline against the bundled
sample filings:

```bash
BSE_DATABASE_URL="sqlite:///demo.db" BSE_PDF_ENABLED=false \
  python -m bse_monitor.main seed-demo
```

### Docker

```bash
cp bse_monitor/docker/.env.example bse_monitor/docker/.env   # then edit
docker compose -f bse_monitor/docker/docker-compose.yml up -d
docker compose -f bse_monitor/docker/docker-compose.yml logs -f monitor
```

The image includes `tesseract-ocr`, the spaCy model and (optionally) Chromium.
`init` creates the schema and exits; `monitor` runs the scheduler.

---

## 5. CLI

| Command | What it does |
|---|---|
| `init-db` | Create the schema |
| `run --days 2 [--sources BSE NSE]` | Scrape → classify → score → persist → alert |
| `backfill --from 2026-08-01 --to 2026-08-31` | Re-cover a window day by day |
| `report daily\|fundraise\|selldown\|watchlist [--csv out.csv]` | Print a reporting view |
| `alerts list\|flush` | Inspect or retry the alert queue |
| `classify "<text>"` | Classify + score ad-hoc text — the debugging path, no DB writes |
| `train-model --min-samples 200` | Train the optional ML layer on rule-labelled filings |
| `schedule [--print-cron]` | Run the scheduler, or emit the equivalent crontab |
| `seed-demo` | Load the bundled sample filings |

---

## 6. Configuration

`configs/config.yaml` holds everything; any key is overridable by an environment
variable named `BSE_<SECTION>_<KEY>`, so the file is safe to commit and only
secrets need injecting:

```bash
export BSE_DATABASE_URL=postgresql+psycopg2://...
export BSE_ALERTS_CHANNELS_SLACK_ENABLED=true
export BSE_ALERTS_CHANNELS_SLACK_WEBHOOK_URL=https://hooks.slack.com/...
export BSE_CLASSIFIER_MIN_CONFIDENCE=0.4
```

* `configs/keywords.yaml` — category lexicons, certainty cues, negations,
  boilerplate, disambiguation rules. **Tune detection here, not in code.**
* `configs/investors.yaml` — tracked PE/VC/sovereign/MF entities with aliases and
  marquee flags, plus the discovery heuristics for untracked names.

---

## 7. Scheduling

Three jobs, mirroring how BSE actually disseminates:

| Job | Default (UTC) | Window |
|---|---|---|
| `intraday` | `*/15 6-16 * * 1-5` | 1 day |
| `evening_sweep` | `30 16 * * 1-5` | 2 days |
| `backfill` | `0 2 * * 6` | 7 days |

The weekly backfill repairs anything a failed intraday run missed; because
ingestion is idempotent it no-ops over filings already stored. If APScheduler is
not installed, `main.py schedule --print-cron` emits an equivalent crontab.

---

## 8. Resilience

| Property | Mechanism |
|---|---|
| Retry | Exponential backoff with full jitter; 403/429/5xx retried, 404 not |
| Rate limiting | Leaky bucket, default 2 req/s |
| Duplicate detection | Content hash + unique constraint + per-channel alert dedupe key |
| Incremental scraping | Window derived from the last **successful** run, with 1-day overlap |
| Checkpointing | `scrape_runs` row written before fetching; a crashed run leaves `RUNNING`, so the next run re-covers the period |
| Error recovery | One bad filing rolls back and is logged; the run continues and ends `PARTIAL` |
| Graceful degradation | Missing Playwright / OCR / spaCy / scikit-learn each disable one capability, never the run |
| Alert durability | Failed delivery stays `PENDING` and is retried next run |
| Audit trail | `audit_log` + JSON-lines logs with a per-run correlation id |

---

## 9. Tests

```bash
python -m pytest bse_monitor/tests -q        # 341 tests, no network, no services
```

Everything runs offline: `anthropic` is an optional import, the LLM client takes
an injected fake, and an autouse fixture scrubs `ANTHROPIC_*` and `BSE_LLM_*`
from the environment — `Config.load()` applies env overrides, so a developer with
`BSE_LLM_ENABLED=true` exported would otherwise turn the suite into a live,
billed run.

Coverage includes: lexicon classification across all ten categories, negation and
disambiguation, score monotonicity and non-saturation, money/share/price/date
extraction (including the authorised-capital and per-share-price traps), scraper
envelope shapes and retry semantics, PDF fallback escalation, table parsing,
repository idempotency and checkpointing, alert queue/retry behaviour, and a
full end-to-end pipeline run over the sample filings.

---

## 10. Example outputs

Generated by `seed-demo` over `examples/sample_filings.json` — see
`examples/output_*.csv` / `.md`.

**Daily Events**

| date | company | event_type | investor | value | score |
|---|---|---|---|---|---|
| 2026-09-11 | Helios Renewable Power Limited | InvestorExit | General Atlantic, ChrysCapital | Rs 1,900.0 Cr | 95 |
| 2026-09-11 | Auralux Speciality Chemicals Limited | InvestorExit | — | — | 91 |
| 2026-09-11 | Zenith Aerospace Systems Limited | IPO | Warburg Pincus | Rs 2,400.0 Cr | 86 |
| 2026-09-11 | Kesar Agro Industries Limited | PreferentialAllotment | — | Rs 105.0 Cr | 85 |
| 2026-09-11 | Northgate Consumer Brands Limited | RightsIssue | — | — | 80 |
| 2026-09-11 | Trident Logistics Corporation Limited | Other | — | — | 10 |

**Fund Raise Pipeline**

| company | event_type | stage | issue_size | score |
|---|---|---|---|---|
| Vantage Infratech Limited | QIP | Approved | Rs 1,200.0 Cr | 91 |
| Zenith Aerospace Systems Limited | IPO | Disclosed | Rs 2,400.0 Cr | 86 |
| Kesar Agro Industries Limited | PreferentialAllotment | Approved | Rs 105.0 Cr | 85 |
| Northgate Consumer Brands Limited | RightsIssue | Proposed / Under consideration | — | 80 |

**Potential Sell-Down Pipeline**

| company | event_type | seller | stake_pct | trigger | value | score |
|---|---|---|---|---|---|---|
| Solaris Digital Payments Limited | InvestorExit | Blackstone | 8.40% | Investor exit signalled | Rs 5,400.0 Cr | 96 |
| Helios Renewable Power Limited | InvestorExit | General Atlantic, ChrysCapital | 5.60% | Investor exit signalled | Rs 1,900.0 Cr | 95 |
| Meridian Healthcare Services Limited | OFS | Anand Krishnan | 6.20% | OFS announced | Rs 1,862.5 Cr | 95 |
| Auralux Speciality Chemicals Limited | InvestorExit | — | 4.10% | Lock-in expiry 2026-09-30 | — | 91 |

Note the last row: no headline sell-down language at all, flagged purely from an
extracted anchor lock-in expiry date. And `Meridian`'s ₹1,862.5 Cr is derived
from shares × floor price, because the filing states no aggregate.

---

## 11. The three layers in detail

### Layer 1 — the forward calendar

Ten statutory trigger types, each derived from a filing and stored in
`upcoming_triggers` with a **sha256 natural key** over (company, type, anchor
date, subject, rule version).

That key is the design's load-bearing detail. `replace_transactions()` deletes
and recreates transaction rows on every re-extraction, so a calendar hung off
them would lose its identity — and the alert history pointing at it — on every
run. Deadlines are instead updated in place, **cancelled rather than deleted**
when they stop deriving, and **superseded** (not overwritten) when the rule
version moves.

`days_to_trigger` is never stored; it is computed against an injected `as_of`.
Date arithmetic differs between SQLite and PostgreSQL, so a stored or hybrid
column would pass the tests and misbehave in production.

Offsets are configuration, not constants — SEBI has already moved the promoter
minimum-contribution lock-in from 3 years to 18 months and split anchor lock-ins
into 30/90-day tranches. Month arithmetic is hand-rolled with end-of-month
clamping (`31 Aug + 6m → 28 Feb`, `29 Feb + 12m → 28 Feb`); the
`timedelta(days=182)` shortcut is wrong for a statutory deadline and is asserted
against.

**Known gap:** MPS deadlines need current public shareholding, which comes from
Reg-31 patterns — the source deliberately deferred. They are seeded from a
reviewable watchlist rather than derived. Every other trigger type is automatic.

### Layer 2 — Haiku event extraction

Rule-prefiltered and synchronous: only filings the rule engine already finds
interesting, or cannot confidently classify, are sent — a few hundred calls a day
rather than all ~3,000. The trade is explicit: **the rules become the recall
ceiling.**

Dedupe is a database guarantee, not bookkeeping. `announcement_id` is uniquely
constrained and claimed *before* the API call, so a crash mid-flight cannot cause
a re-send and therefore cannot double-charge.

`coerce_event` clamps every model output onto the taxonomy before it reaches the
database. `EventFiling.category` has a CHECK constraint, so an unvalidated
`event_class` fails the INSERT and takes the whole filing down with it. A clamped
verdict also has its confidence zeroed, so a degraded read cannot masquerade as
evidence.

Reconciliation reuses `classifier.ml.blend` rather than inventing a second
policy: a confident rule verdict wins outright, and the model may override only a
low-confidence or `Other` verdict.

**On prompt caching, measured rather than assumed:** Haiku 4.5 will not cache a
prefix under **4096 tokens**, and does not error when it declines — it returns
`cache_creation_input_tokens: 0` forever. This prefix is ~3,000. Padding it to
clear the floor buys about **$120/year** at ~200 calls/day, which does not justify
diluting a prompt tuned for accuracy. The markers stay (free, and auto-engage if
the prefix or model changes) and the client logs a warning the first time it sees
a dead cache.

### Layer 3 — labels, panel and model

**Label:** a holder disposing of ≥0.5% of equity **or** ≥₹100 crore within 60
days. Two properties matter more than the thresholds — the window is strictly
forward (a trade on `week_end` belongs to the features, not the label), and rows
are deduplicated before summing.

**Cross-source dedupe** is why `disposals` exists. The same block deal is
published by both exchanges and often filed a third time as a SAST 29(2)
disclosure; keying on the economics rather than the source stops one sale being
counted three times and trebling every label it touches.

**Point-in-time discipline.** Every feature function takes an `as_of` and reads
only rows strictly before it. A test walks the feature module's **AST** for any
`today()`/`now()` call, because a single wall-clock read trains the model on the
future and fails silently — nothing crashes, the metrics just improve. Calendar
features filter on `known_from_date`, not `trigger_date`: a March lock-in expiry
was only knowable from the filing that disclosed the allotment.

**Evaluation** is walk-forward by year with a 60-day embargo matching the label
horizon. `precision@20` is computed per week then averaged, not pooled — the desk
reads a fresh top-20 each week. Median lead time is reported alongside, because a
model that fires the day before a block is accurate and useless.

See [`examples/model_walkforward_report.md`](examples/model_walkforward_report.md).

### Two honest limitations

**LLM features are absent from v1.** The LLM layer only produces rows from
go-live, and backfilling it over the full history is ~7.5M filings. Including
them would leave the historical panel mostly null and the model would learn
`llm_present` as a proxy for `recent`. They enter as a separate block in a later
retrain; `feature_set_version` is stamped on every artefact and **checked on
load with a refusal**, because scoring across feature sets is a wrong answer
rather than an error.

**The holder universe is survivorship-shaped.** Holders are bootstrapped from
their own observed activity, so one sitting on 8% and doing nothing is invisible
until its first trade. `HolderUniverse` is a Protocol precisely so a Reg-31
shareholding-pattern source can replace the bootstrap without touching feature or
training code.

---

## 12. Deployment

```bash
cp bse_monitor/docker/.env.example bse_monitor/docker/.env   # then edit
docker compose -f bse_monitor/docker/docker-compose.yml up -d
```

Jobs, all UTC:

| Job | Cron | Purpose |
|---|---|---|
| intraday | `*/15 6-16 * * 1-5` | scrape + classify |
| calendar | `0 1 * * *` | re-derive deadlines |
| **brief** | `30 2 * * 1-5` | **08:00 IST ranked email** |
| backfill | `0 2 * * 6` | weekly reconciliation |

**First run on your infrastructure**, in order:

```bash
python -m bse_monitor.main init-db --upgrade
python -m bse_monitor.main backfill --from 2015-01-01 --to 2025-12-31   # deals
python -m bse_monitor.main model coverage        # check SAST coverage per year
python -m bse_monitor.main model panel
python -m bse_monitor.main model evaluate --out walkforward.md
python -m bse_monitor.main model train
python -m bse_monitor.main daily-brief --dry-run
```

`init-db --upgrade` matters on an already-deployed database: `create_all()` only
ever creates *missing tables* and never adds a column to an existing one, so
`daily_alerts.trigger_id` needs the explicit additive migration.

---

## 13. Operational notes and limits

* **Respect the source.** Default 2 req/s with backoff. BSE publishes no public
  API terms for this endpoint; check your own compliance position before running
  at higher rates, and prefer the official paid feed for commercial use.
* **The ML layer is off by default.** It needs a corpus first — run rules-only
  until a few hundred confident labels exist, then `train-model`. It can never
  override a confident rule verdict, only rescue a low-confidence one.
* **FX is a static table.** Filings quote USD rarely and the score only needs the
  order of magnitude; a live feed would add a failure mode for no analytic gain.
* **Not investment advice.** The Opportunity Score ranks filings by
  detectability and materiality, not by expected return.
