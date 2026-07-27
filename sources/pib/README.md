# PIB source documents — Exhibit 31 (Evolution of India's Defence Acquisition Procedure)

This folder maps every point in Exhibit 31 (DPP/DAP evolution 2002–2020) to its
best **Press Information Bureau (pib.gov.in)** source document.

## How to get the documents

The Claude Code session that assembled this could **not** download the files: the
session's network policy denied outbound access to `pib.gov.in` at the egress
gateway (HTTP 403 on CONNECT). Only web *search* was available, not web *fetch*.

To pull the actual documents, run the bundled script from any machine with normal
internet access:

```bash
bash download_all.sh      # renders each page to PDF in ./downloads/
```

Each page is printed to **PDF** via headless Chromium (falls back to wkhtmltopdf,
then to saving raw HTML if no PDF engine is present). Chromium/Chrome must be
installed; set `CHROME_BIN` if it's in a non-standard path.

If a URL fails from a script, open it in a browser — PIB occasionally blocks
non-browser clients.

## Verification status

Every URL/ID below was identified from **search-engine index snippets** that
reproduce the page title and text. None were independently fetch-loaded from the
generating session, so confirm each live page before citing. IDs: pre-2014 PIB
releases use `relid=`; 2014+ use `PRID=`.

## Source map

| Year | Point | PIB ID | Date | Primary? | URL |
|---|---|---|---|---|---|
| 2001 | Post-Kargil MoD acquisition structures (DAC, Oct 2001) | archive | 2001-05-23 | ✅ primary (context) | archive.pib.gov.in/archive/releases98/lyr2001/rmay2001/23052001/r2305200110.html |
| 2002 | DPP-2002 consolidated, 'Buy' category | relid 95274 | ~2013 | ❌ recap (no 2002 release online) | pib.gov.in/newsite/PrintRelease.aspx?relid=95274 |
| 2003 | Scope enlarged to Buy & Make (Global)/ToT | relid 95274 | ~2013 | ❌ recap (no 2003 release online) | pib.gov.in/newsite/PrintRelease.aspx?relid=95274 |
| 2005 | First offset policy — groundwork (Kelkar Cttee) | relid 8386 | 2005 | ✅ primary (groundwork) | pib.gov.in/newsite/erelcontent.aspx?relid=8386 |
| 2005 | Offset policy history reference | relid 107868 | ~2014 | ⚠️ recap | pib.gov.in/newsite/PrintRelease.aspx?relid=107868 |
| 2006 | 'Make' category, offset, integrity pact, warship building | relid 20385 | 2006-08-22 | ✅ **strongest early primary** | pib.gov.in/newsite/erelcontent.aspx?relid=20385 |
| 2008 | RFI + AoN→RFP flow | relid 68464 | ~2010-12 | ⚠️ 2010 reply describing DPP-2008 | pib.gov.in/newsite/PrintRelease.aspx?relid=68464 |
| 2008 | Delegation of powers; 2-yr AoN lapse | — | — | ❌ no PIB source — use DPP-2008 doc | — |
| 2011 | Buy & Make (Indian) 50% IC / lead integrator | — | — | ❌ no PIB primary — use DPP-2011 doc | cgda.nic.in/ifa/DPP2011.pdf |
| 2011 | Offset → civil aerospace/internal security/training | relid 107868 | ~2013 | ⚠️ later reference | pib.gov.in/newsite/PrintRelease.aspx?relid=107868 |
| 2011 | Removed complete blacklisting | — | — | ❌ not substantiated | — |
| 2013 | Preferred categorisation + SCAPCHC/DPB delegation | relid 95274 | 2013-04 | ✅ solid PIB primary | pib.gov.in/newsite/PrintRelease.aspx?relid=95274&reg=3&lang=2 |
| 2013 | AoN 2yr→1yr; SQR frozen before AoN | — | 2013-04 | ❌ secondary (spsmai id=2136) | — |
| 2016 | Buy Indian IDDM, category reorg, Make-I/II, offset ↑, 6-mo AoN | PRID 1519140 | 2016 | ✅ strongest 2016 release | pib.gov.in/Pressreleaseshare.aspx?PRID=1519140 |
| 2016 | Penalty instead of blanket blacklisting | — | — | ⚠️ not tied to a PIB URL | — |
| 2020 | DAP-2020 launch; IC raised; Buy (Global-Manufacture in India) | PRID 1659746 | 2020-09-28 | ✅ **definitive launch** | pib.gov.in/PressReleasePage.aspx?PRID=1659746 |
| 2020 | Draft DPP/DAP 2020 (predecessor) | PRID 1607400 | 2020-03 | ✅ draft-stage | pib.gov.in/PressReleasePage.aspx?PRID=1607400 |
| 2020 | FDI 49%→74% | PRID 1656082 | 2020-09-17 | ✅ primary — **DPIIT, not MoD** | pib.gov.in/Pressreleaseshare.aspx?PRID=1656082 |
| 2020 | FDI 74% — MoD-attributed alternate | PRID 1654091 | 2020 | ✅ primary | pib.gov.in/PressReleasePage.aspx?PRID=1654091 |
| 2020 | Negative import / positive indigenisation list | PRID 1644570 | 2020-08-09 | ✅ definitive primary | pib.gov.in/PressReleasePage.aspx?PRID=1644570 |

## Points with NO contemporaneous PIB release (pre-2014 archive gap)

Cite the DPP document itself (MoD / `cgda.nic.in`) for: DPP-2002 launch, the
June-2003 Buy & Make (Global) enlargement, DPP-2008 delegation & 2-year AoN
lapse, and DPP-2011 Buy & Make (Indian) / blacklisting removal.

## Two flags for the exhibit

1. **FDI 74%** is a **DPIIT / Ministry of Commerce** notification (Press Note 4 of
   2020), not a Ministry of Defence / DAP item — it sits outside the DAP document.
2. The 2016 **"penalty instead of blacklisting"** claim could not be tied to any
   PIB URL — verify it against the DPP-2016 text directly.

See `manifest.csv` for the machine-readable version (includes alternate URLs).
