#!/usr/bin/env bash
#
# Download every PIB source document behind Exhibit 31
# (Evolution of India's Defence Acquisition Procedure, DAP) as PDF.
#
# Run this in an environment WITH open web access to pib.gov.in.
# It could NOT be run in the Claude Code session that generated it because
# that session's egress policy denied outbound access to pib.gov.in.
#
# Each document is rendered to a PDF in ./downloads/ using headless Chromium
# (PIB's .aspx pages are dynamic, so a print-to-PDF render is more faithful
# than a raw HTML fetch). Falls back to wkhtmltopdf, then to saving raw HTML.
#
# Usage:  bash download_all.sh
#
set -u
OUT="$(dirname "$0")/downloads"
mkdir -p "$OUT"
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"

# --- locate a headless-capable Chromium/Chrome ---------------------------
CHROME=""
for c in "${CHROME_BIN:-}" /opt/pw-browsers/chromium google-chrome google-chrome-stable \
         chromium chromium-browser /usr/bin/chromium /usr/bin/chromium-browser; do
  command -v "$c" >/dev/null 2>&1 && { CHROME="$c"; break; }
  [ -x "$c" ] 2>/dev/null && { CHROME="$c"; break; }
done 2>/dev/null

to_pdf () {  # to_pdf <outfile.pdf> <url>
  local out="$1"; local url="$2"
  echo ">> $out  <-  $url"
  if [ -n "$CHROME" ]; then
    "$CHROME" --headless=new --disable-gpu --no-sandbox --hide-scrollbars \
      --run-all-compositor-stages-before-draw --virtual-time-budget=10000 \
      --user-agent="$UA" --print-to-pdf="$OUT/$out" "$url" >/dev/null 2>&1 \
      && { echo "   ok (chromium)"; return; }
  fi
  if command -v wkhtmltopdf >/dev/null 2>&1; then
    wkhtmltopdf --custom-header "User-Agent" "$UA" "$url" "$OUT/$out" >/dev/null 2>&1 \
      && { echo "   ok (wkhtmltopdf)"; return; }
  fi
  # last resort: save the raw HTML alongside so nothing is lost
  local html="${out%.pdf}.html"
  curl -fsSL --retry 3 --retry-delay 2 -A "$UA" "$url" -o "$OUT/$html" \
    && echo "   saved HTML fallback ($html) — no PDF engine reachable" \
    || echo "   FAILED: $url"
}

# --- 2001: post-Kargil GoM report (context for 2002 row) -----------------
to_pdf "2001_GoM_national_security.pdf" "https://archive.pib.gov.in/archive/releases98/lyr2001/rmay2001/23052001/r2305200110.html"

# --- 2002 / 2003: DPP recap (no original release online) -----------------
to_pdf "2002-2003_DPP_recap_relid95274.pdf" "https://www.pib.gov.in/newsite/PrintRelease.aspx?relid=95274"
to_pdf "2002_DPP_recap_relid133030.pdf"     "https://www.pib.gov.in/newsite/PrintRelease.aspx?relid=133030"

# --- 2005: Kelkar Committee + offset-policy history ----------------------
to_pdf "2005_Kelkar_committee_relid8386.pdf" "https://www.pib.gov.in/newsite/erelcontent.aspx?relid=8386"
to_pdf "2005_offset_policy_relid107868.pdf"  "https://www.pib.gov.in/newsite/PrintRelease.aspx?relid=107868"

# --- 2006: DPP-2006 release speech (Pranab Mukherjee) --------------------
to_pdf "2006_DPP2006_speech_relid20385.pdf"  "https://www.pib.gov.in/newsite/erelcontent.aspx?relid=20385&reg=3&lang=2"

# --- 2008: RFI / AoN->RFP description ------------------------------------
to_pdf "2008_procurement_of_defence_equipment_relid68464.pdf" "https://www.pib.gov.in/newsite/PrintRelease.aspx?relid=68464"

# --- 2011: offset expansion references -----------------------------------
to_pdf "2011_defence_offset_policy_relid107868.pdf" "https://www.pib.gov.in/newsite/PrintRelease.aspx?relid=107868"
to_pdf "2011_defence_procurement_policy_relid83718.pdf" "https://www.pib.gov.in/newsite/PrintRelease.aspx?relid=83718"

# --- 2013: DPP-2013 categorisation + delegation --------------------------
to_pdf "2013_DPP2013_relid95274.pdf" "https://www.pib.gov.in/newsite/PrintRelease.aspx?relid=95274&reg=3&lang=2"

# --- 2016: DPP-2016 / IDDM / Make-I&II -----------------------------------
to_pdf "2016_new_weapons_procurement_policy_PRID1519140.pdf" "https://www.pib.gov.in/Pressreleaseshare.aspx?PRID=1519140"

# --- 2020: DAP-2020 launch + draft ---------------------------------------
to_pdf "2020_DAP2020_launch_PRID1659746.pdf" "https://www.pib.gov.in/PressReleasePage.aspx?PRID=1659746"
to_pdf "2020_DAP2020_draft_PRID1607400.pdf"  "https://www.pib.gov.in/PressReleasePage.aspx?PRID=1607400"

# --- 2020: FDI 49%->74% --------------------------------------------------
to_pdf "2020_FDI_defence_pressnote4_PRID1656082.pdf" "https://www.pib.gov.in/Pressreleaseshare.aspx?PRID=1656082"
to_pdf "2020_FDI_defence_MoD_PRID1654091.pdf"        "https://www.pib.gov.in/PressReleasePage.aspx?PRID=1654091"

# --- 2020: negative import list ------------------------------------------
to_pdf "2020_negative_import_list_PRID1644570.pdf" "https://www.pib.gov.in/PressReleasePage.aspx?PRID=1644570"

echo
echo "Done. PDFs in: $OUT"
[ -z "$CHROME" ] && echo "NOTE: no Chromium/Chrome found — install one (or wkhtmltopdf) for real PDFs; HTML was saved as fallback."
echo "If any FAILED, open the URL in a browser (PIB sometimes blocks non-browser clients)."
