# Fetch log

Every URL attempted, whether it succeeded, and why it failed.

**Summary: 0 primary documents retrieved.** Every external document host was
refused by the session's egress proxy with HTTP 403 at the CONNECT stage
(organization egress-policy denial). `curl` and the `WebFetch` tool fail
identically, so this is a network-policy condition, not a tool or bot-detection
condition. Per `/root/.ccr/README.md`, 403/407 policy denials are reported
rather than retried or routed around — so each host below was attempted, logged,
and abandoned.

`WebSearch` runs over a separate server-side channel and **did** succeed. It was
used solely to locate document URLs, never as the source of a figure.

## Diagnostic evidence

`$HTTPS_PROXY/__agentproxy/status` → `recentRelayFailures[]` records, per host:

```
kind:   "connect_rejected"
detail: "gateway answered 403 to CONNECT (policy denial or upstream failure)"
```

Confirmed for `www.google.com`, `web.archive.org`, `nsearchives.nseindia.com`,
`www.sansera.in`, `www.icra.in`, `s29.q4cdn.com` among others.

Control tests establishing scope of the block:

| URL | Result | Note |
|---|---|---|
| `https://github.com/cementavendus-web/Cement` | **SUCCESS** | GitHub is on the allowlist |
| `https://en.wikipedia.org/wiki/Aerospace_manufacturer` | FAIL | EGRESS_BLOCKED — not an India-specific block |
| `https://www.google.com` | FAIL | proxy 403 |
| `https://example.com` | FAIL | proxy 403 |

## Tier 1 — Company IR sites

| URL | Result | Reason |
|---|---|---|
| https://omnitecheng.com/infrastructure/ | FAIL | EGRESS_BLOCKED (403) |
| https://unimechaerospace.com/wp-content/uploads/2024/12/Red-Herring-Prospectus-Unimech.pdf | FAIL | EGRESS_BLOCKED (403) — RHP, primary |
| https://unimechaerospace.com/wp-content/uploads/2024/08/Draft-Red-herring-prospectus-Unimech-Aerospace-and-Manufacturing-Limited.pdf | FAIL | EGRESS_BLOCKED (403) — DRHP, primary |
| https://mtar.in/our-units/ | FAIL | EGRESS_BLOCKED (403) |
| https://mtar.in/wp-content/uploads/2025/11/Investors-Presentation-30.09.2025.pdf | FAIL | EGRESS_BLOCKED (403) — deck as of 30-09-2025 |
| https://mtar.in/wp-content/uploads/2025/03/Annual-Report-FY-2024-Aug-17-2024.pdf | FAIL | EGRESS_BLOCKED (403) |
| https://sansera.in/wp-content/uploads/2025/05/Investors-Presentation_Q4FY25.pdf | FAIL | EGRESS_BLOCKED (403) |
| https://sansera.in/wp-content/uploads/2025/11/Investors-presentation-Q2H1FY26.pdf | FAIL | EGRESS_BLOCKED (403) |
| https://sansera.in/wp-content/uploads/2025/08/Investors-Presentation_Q1FY26.pdf | FAIL | EGRESS_BLOCKED (403) |
| https://sansera.in/wp-content/uploads/2024/09/Annual-Report-2023-24-1.pdf | FAIL | EGRESS_BLOCKED (403) |
| https://sansera.in/wp-content/uploads/2025/06/Press-Release.pdf | FAIL | EGRESS_BLOCKED (403) |
| https://dynamatics.com/annual-reports | FAIL | EGRESS_BLOCKED (403) |
| https://dynamatics.com/UploadImages/EarningsPresentation/Q4-and-Full-Year-FY2025-Earnings-Presentation5124-earning_presentation.pdf | FAIL | EGRESS_BLOCKED (403) |
| https://indo-mim.com/wp-content/uploads/2025/09/INDO-MIM-Limited-DRHP.pdf | FAIL | EGRESS_BLOCKED (403) — DRHP dated 26-09-2025, primary |
| https://ptcil.com (investor relations, credit-ratings PDF) | FAIL | EGRESS_BLOCKED (403) |
| https://azadengineering.com (investor relations) | FAIL | EGRESS_BLOCKED (403) |
| https://aequs.com | FAIL | EGRESS_BLOCKED (403) |

## Tier 2 — NSE archives

| URL | Result | Reason |
|---|---|---|
| https://nsearchives.nseindia.com/corporate/OMNI_07082026.pdf | FAIL | EGRESS_BLOCKED (403) |
| https://nsearchives.nseindia.com/corporate/SANSERA_11082025225719_earningsrelease.pdf | FAIL | EGRESS_BLOCKED (403) |
| https://nsearchives.nseindia.com/corporate/DYNAMATICH_20052026121424_Presentation_19052026.pdf | FAIL | EGRESS_BLOCKED (403) |

## Tier 3 — SEBI

| URL | Result | Reason |
|---|---|---|
| https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=3&ssid=15&smid=10 | FAIL | EGRESS_BLOCKED (403) |
| https://www.sebi.gov.in/filings/public-issues/oct-2021/sansera-engineering-limited-rhp_53147.html | FAIL | EGRESS_BLOCKED (403) |
| https://www.sebi.gov.in/filings/public-issues/oct-2021/sansera-engineering-limited-prospectus_53146.html | FAIL | EGRESS_BLOCKED (403) |

## Tier 4 — BRLM sites

| URL | Result | Reason |
|---|---|---|
| https://www.axiscapital.co.in/ | FAIL | EGRESS_BLOCKED (403) |
| https://www.iiflcap.com/OfferDocument/OfferDocumentSELTD | FAIL | EGRESS_BLOCKED (403) |
| https://simplehai.axisdirect.in/app/index.php/market/Equity/downloadProspectus/file/IPO_RHP_MTAR.pdf | FAIL | EGRESS_BLOCKED (403) — MTAR RHP 22-02-2021 |
| https://d2un9pqbzgw43g.cloudfront.net/main/Sansera-Engineering-Limited-RHP.pdf | FAIL | DNS ENOTFOUND, then EGRESS_BLOCKED |
| jmfl.com, icicisecurities.com, sbicaps.com (entry pages) | FAIL | EGRESS_BLOCKED (403) |

## Tier 5 — Credit rating agencies

| URL | Result | Reason |
|---|---|---|
| https://www.careratings.com/ | FAIL | EGRESS_BLOCKED (403) |
| https://www.crisil.com/ | FAIL | EGRESS_BLOCKED (403) |
| https://www.icra.in/Rating/ShowRationalReportFilePdf/125868 | FAIL | EGRESS_BLOCKED (403) — Sansera rationale 28-02-2024 |
| https://www.icra.in/Rationale/ShowRationaleReport/?Id=118068 | FAIL | EGRESS_BLOCKED (403) |

## Not attempted, by instruction

- `bseindia.com/xml-data/corpfiling/` — the brief flags these as bot-blocked;
  no attempts spent. (The `bseindia.com` root was incidentally confirmed
  egress-blocked as well, so the NSE/company-site fallback was already moot.)

## Other

| URL | Result | Reason |
|---|---|---|
| https://web.archive.org/... (Wayback mirrors of the above) | FAIL | Refused by the fetch tool itself, in addition to egress policy |
| Broker notes on public S3/CDN buckets (Anand Rathi, Master Trust) | PARTIAL | Some objects reachable — but these are broker notes, barred as figure sources; not used |
| WebSearch (many queries, all 9 companies) | SUCCESS | Used only to locate the URLs above |
