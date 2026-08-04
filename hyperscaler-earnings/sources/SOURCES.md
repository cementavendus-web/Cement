# Source documents

Primary sources for the hyperscaler earnings briefings, capex extracts, demand extracts, and
backlog trend. Files mirrored into this folder are marked **[included]**; documents that the
session's egress policy blocked from mirroring (SEC EDGAR, some IR hosts) are **[linked]** with
their URL; figures computed from a stated growth rate are **[derived]**.

## Included files

### `transcripts/` — official earnings-call transcripts (primary source for current-quarter data)
These back every *current-quarter* figure: latest capex guidance, the data-center demand quotes,
and the current backlog/RPO numbers spoken on the call.

| File | Company | Call | Date |
|---|---|---|---|
| `Amazon_Q2-2026_earnings-call_2026-07-30.pdf` | Amazon | Q2 2026 | 2026-07-30 |
| `Microsoft_FY26-Q4_earnings-call_2026-07-29.pdf` | Microsoft | FY2026 Q4 | 2026-07-29 |
| `Meta_Q2-2026_earnings-call_2026-07-29.pdf` | Meta | Q2 2026 | 2026-07-29 |
| `Alphabet_Q2-2026_earnings-call_2026-07-22.pdf` | Alphabet | Q2 2026 | 2026-07-22 |
| `Oracle_FY26-Q4_earnings-call_2026-06-10.pdf` | Oracle | FY2026 Q4 | 2026-06-10 |

### `press-releases/` — official Oracle earnings press releases
| File | Backs |
|---|---|
| `Oracle_Q4-FY2026_press-release_2026-06-10.html` | Oracle RPO **$638B** (+363% YoY, +$85B QoQ), FY26 revenue, capex |
| `Oracle_Q3-FY2026_press-release_2026-03-10.html` | Oracle RPO **$553B** (prior quarter), FY26 ~$50B capex guide |

> Verified: the Q4 release states *“Remaining Performance Obligations grew $85 billion in Q4 from
> $553 billion to $638 billion.”*

## Data-point → source map

### Current capex guidance & demand quotes — [included] transcripts
Amazon $220B · Microsoft ~$175B · Meta $130–145B · Alphabet $195–205B · Oracle ~$70B net FY27, and
all demand quotes ($496B / $678B / $514B backlog, "demand exceeds supply", 1.2 GW, etc.) are
verbatim from the transcript PDFs above.

### Previous capex guidance (prior-quarter calls)
| Company | Figure | Source | Status |
|---|---|---|---|
| Amazon | ~$200B (2026) | Q4 2025 earnings call, 2026-02-05 | [linked] ir.aboutamazon.com |
| Microsoft | ~$190B (CY2026) | FY2026 Q3 earnings call, 2026-04-29 | [linked] microsoft.com/investor |
| Meta | $125–145B | Q1 2026 earnings call, 2026-04-29 | [linked] investor.atmeta.com |
| Alphabet | $180–190B | Q1 2026 earnings call, 2026-04-29 | [linked] abc.xyz/investor |
| Oracle | ~$50B (FY26) | Q3 FY2026 press release/call, 2026-03-10 | **[included]** |

### Backlog / RPO — previous quarter & year-ago
| Company | Metric | Prev quarter | Year-ago | Source | Status |
|---|---|---|---|---|---|
| Amazon | AWS backlog | Q1’26 $364B | Q2’25 ~$195B | 10-Q (2026-03-31), reporting | [linked] SEC EDGAR |
| Microsoft | Commercial RPO | Q3 FY26 $627B | Q4 FY25 ≈$368B | FY26 Q3 call; ÷1.84 | [linked] / [derived] |
| Alphabet | Cloud backlog | Q1’26 $462B | Q2’25 $106B | 10-Q (2026-03-31), reporting | [linked] SEC EDGAR |
| Oracle | Total RPO | Q3 FY26 $553B | Q4 FY25 ≈$139B | Q3 press release; ÷4.63 | **[included]** / [derived] |
| Meta | — | n/a | n/a | no public cloud → no backlog disclosed | — |

## Linked source URLs (not mirrored — egress policy blocked)

The session's egress policy returned **403** for `www.sec.gov`, `s23.q4cdn.com`, and `abc.xyz`, so
those documents could not be saved into the repo. They are public at:

- Amazon 10-Q, Q2 2026 (Jun 30) — sec.gov/…/amzn-20260630.htm; Q1 2026 (Mar 31) — amzn-20260331.htm
- Alphabet 10-Q, Q1 2026 (Mar 31) — sec.gov/…/goog-20260331.htm
- Microsoft 10-K FY2026 — sec.gov/…/msft-20260630.htm; 10-Q Q3 FY2026 — msft-20260331.htm
- Amazon IR (transcripts/press releases) — https://ir.aboutamazon.com/
- Microsoft IR — https://www.microsoft.com/en-us/investor/
- Alphabet IR — https://abc.xyz/investor/
- Meta IR — https://investor.atmeta.com/
- Oracle IR — https://investor.oracle.com/

To mirror the blocked filings too, either provide them as uploads (like the transcripts) or run
the download from an environment whose egress policy allows `sec.gov`.
