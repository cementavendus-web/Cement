# Year-ago (previous-year same quarter) backlog — sources

Source document for each hyperscaler's **year-ago same-quarter** backlog / RPO figure used in the
backlog trend. Two are mirrored into `year-ago-backlog/` (hosts reachable); two are link-only
(SEC EDGAR and q4cdn were blocked by the session egress policy, 403).

| Company | Year-ago quarter | Figure | Exact source wording | Source document | In repo? |
|---|---|---|---|---|---|
| **Microsoft** | FY2025 Q4 (Jun 30, 2025) | **$368B** commercial RPO | *"remaining performance obligation increased to $368 billion, up 37% and 35% in constant currency"* (Amy Hood) | Microsoft FY25 Q4 earnings call, Jul 30 2025 | ✅ `year-ago-backlog/Microsoft_FY2025-Q4_earnings-call_2025-07-30.html` |
| **Oracle** | FY2025 Q4 (May 31, 2025) | **$138B** total RPO | *"Remaining Performance Obligations up 41% to $138 billion"* | Oracle FY25 Q4 press release, Jun 11 2025 | ✅ `year-ago-backlog/Oracle_Q4-FY2025_press-release_2025-06-11.html` |
| **Amazon** | Q2 2025 (Jun 30, 2025) | **~$195B** AWS backlog (+25% YoY) | AWS "commitments not yet recognized" for contracts >1 yr | Amazon Q2 2025 Form 10-Q | ❌ link only |
| **Alphabet** | Q2 2025 (Jun 30, 2025) | **$106B** (Google Cloud) / **$108.2B** total revenue backlog | *"$108.2 billion of remaining performance obligations… primarily related to Google Cloud"* | Alphabet Q2 2025 Form 10-Q | ❌ link only |

## Verified figures vs. earlier estimates

- **Microsoft $368B** and **Oracle $138B** were previously shown as *derived* (from the stated YoY
  growth). They are now **confirmed** against the official documents above — the derivation was
  correct ($678B ÷ 1.84 ≈ $368B; $638B ÷ 4.63 ≈ $138B). `backlog-trend.md` has been updated to
  drop the "derived" flag on these two.
- **Alphabet**: the $106B used in the trend is the management-cited Google Cloud backlog; the
  Q2 2025 10-Q reports **$108.2B** total revenue backlog (primarily Google Cloud). Both are noted.

## Link-only sources (egress policy blocked download — 403)

- **Amazon Q2 2025 10-Q** (quarter ended Jun 30, 2025), "Commitments and Contingencies" note —
  AWS backlog ~$195B (contracts >1 yr, +25% YoY). Direct filing:
  `https://www.sec.gov/Archives/edgar/data/1018724/000101872425000086/amzn-20250630.htm`
  (accession `0001018724-25-000086`, filed Jul 31 2025). Filings list:
  `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001018724&type=10-Q`
  (search the document text for "performance obligations"). Not mirrored — sec.gov blocked (403).
- **Alphabet Q2 2025 10-Q** (quarter ended Jun 30, 2025) —
  SEC EDGAR: `https://www.sec.gov/Archives/edgar/data/1652044/…/goog-20250630.htm`
  · PDF mirror: `https://s206.q4cdn.com/479360582/files/doc_financials/2025/q2/goog-10-q-q2-2025.pdf`

To mirror these two into the repo as well, upload the 10-Q PDFs (like the transcripts) or run the
download from an environment whose egress policy allows `sec.gov` / `q4cdn.com`.
