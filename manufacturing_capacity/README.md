# Manufacturing-capacity dataset — BLOCKED, no figures extracted

**Status: the dataset could not be built in this environment. Zero capacity
figures were extracted. Nothing in `capacity.csv` is a real number.**

This directory contains the audit trail, not the dataset. Read this file before
using anything here.

## What happened

The task required fetching all source documents from the web. This session's
network egress proxy is **default-deny**: it answered HTTP 403 to the CONNECT
for every external document host attempted, across all four tiers of the
source hierarchy.

Verified blocked (both `curl` and the `WebFetch` tool, which fail identically):

| Source tier | Hosts attempted | Result |
|---|---|---|
| 1. Company IR | omnitecheng.com, mtar.in, sansera.in, dynamatics.com, unimechaerospace.com, indo-mim.com, ptcil.com, azadengineering.com, aequs.com | 403 / EGRESS_BLOCKED |
| 2. NSE archives | nsearchives.nseindia.com | 403 / EGRESS_BLOCKED |
| 3. SEBI | sebi.gov.in | 403 / EGRESS_BLOCKED |
| 4. BRLM sites | axiscapital.co.in, iiflcap.com, jmfl.com, icicisecurities.com, sbicaps.com | 403 / EGRESS_BLOCKED |
| 5. Rating agencies | careratings.com, crisil.com, icra.in | 403 / EGRESS_BLOCKED |

The block is not specific to Indian filing sites — `en.wikipedia.org`,
`www.google.com` and `example.com` are equally refused. The allowlist appears to
permit only GitHub and language package registries. `web.archive.org` is
additionally refused by the fetch tool itself.

Diagnosis came from the proxy's own status endpoint
(`$HTTPS_PROXY/__agentproxy/status`), whose `recentRelayFailures` log records
`connect_rejected — gateway answered 403 to CONNECT (policy denial or upstream
failure)` for each host. Per `/root/.ccr/README.md`, a 403 from this proxy is an
organization egress-policy denial that must be **reported, not retried or routed
around**. No route-around was attempted.

## Why the files are empty of figures

`WebSearch` (a separate, server-side channel) *does* work, and was used — but
only for its permitted purpose: **locating** documents. The brief forbids search
snippets as the source of a figure and forbids filling gaps from model training
knowledge. Both prohibitions were honoured. Every metric is therefore recorded
as `UNREACHABLE` or `NOT_FOUND` against the primary URL that *would* answer it.

Search did surface real capacity numbers in aggregator snippets and two
broker notes. **These were deliberately not recorded as figures.** They are
unverified against any primary document, and writing them in would have produced
a dataset that looks complete and is not. Where such a number exists, `gaps.md`
raises it as a question for IR instead.

## What is here

| File | Contents |
|---|---|
| `capacity.csv` | Metric rows for 4 of 9 companies, all valued `UNREACHABLE`, each carrying the located primary URL + doc title to retry |
| `conventions.csv` | Task 2 (capacity convention footnote) — not obtainable; no company's footnote could be read |
| `normalise.csv` | Task 3 — `normalised_hours` left **blank** throughout, per the "never estimate" rule (no machine count was verifiable) |
| `forward.csv` | Task 4 — located forward-capex/objects-of-issue documents, no stated amounts verified |
| `gaps.md` | Every NOT_FOUND / UNREACHABLE phrased as a question for IR |
| `fetch_log.md` | Every URL attempted, success/failure, and reason |

Coverage is partial (MTAR, Sansera, Dynamatic, Indo-MIM). Research on Aequs,
Azad, Omnitech, Unimech and PTC was halted once the blocker was confirmed
environment-wide rather than company-specific — continuing would have produced
more placeholder rows and no more information.

## To actually build this dataset

Re-run with egress permitted to the hosts in the table above. The environment's
network policy is set when the environment is created — see
https://code.claude.com/docs/en/claude-code-on-the-web. A policy allowing those
document hosts (or an unrestricted one) is sufficient; no change to the research
method is needed. `fetch_log.md` carries the located primary URLs, so a re-run
starts from document retrieval rather than from document discovery.
