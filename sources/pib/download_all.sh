#!/usr/bin/env bash
#
# Download every PIB source document behind Exhibit 31
# (Evolution of India's Defence Acquisition Procedure, DAP).
#
# Run this in an environment WITH open web access to pib.gov.in.
# It could NOT be run in the Claude Code session that generated it because
# that session's egress policy denied outbound access to pib.gov.in.
#
# Saves each document as HTML into ./downloads/ . Some URLs are alternate
# formats for the same PRID/relid; all are tried so at least one succeeds.
#
# Usage:  bash download_all.sh
#
set -u
OUT="$(dirname "$0")/downloads"
mkdir -p "$OUT"
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"

fetch () {  # fetch <outfile> <url>
  local out="$1"; local url="$2"
  echo ">> $out  <-  $url"
  curl -fsSL --retry 3 --retry-delay 2 -A "$UA" "$url" -o "$OUT/$out" \
    && echo "   ok" || echo "   FAILED: $url"
}

# --- 2001: post-Kargil GoM report (context for 2002 row) -----------------
fetch "2001_GoM_national_security.html" "https://archive.pib.gov.in/archive/releases98/lyr2001/rmay2001/23052001/r2305200110.html"

# --- 2002 / 2003: DPP recap (no original release online) -----------------
fetch "2002-2003_DPP_recap_relid95274.html" "https://www.pib.gov.in/newsite/PrintRelease.aspx?relid=95274"
fetch "2002_DPP_recap_relid133030.html"     "https://www.pib.gov.in/newsite/PrintRelease.aspx?relid=133030"

# --- 2005: Kelkar Committee + offset-policy history ----------------------
fetch "2005_Kelkar_committee_relid8386.html" "https://www.pib.gov.in/newsite/erelcontent.aspx?relid=8386"
fetch "2005_offset_policy_relid107868.html"  "https://www.pib.gov.in/newsite/PrintRelease.aspx?relid=107868"

# --- 2006: DPP-2006 release speech (Pranab Mukherjee) --------------------
fetch "2006_DPP2006_speech_relid20385.html"  "https://www.pib.gov.in/newsite/erelcontent.aspx?relid=20385&reg=3&lang=2"

# --- 2008: RFI / AoN->RFP description ------------------------------------
fetch "2008_procurement_of_defence_equipment_relid68464.html" "https://www.pib.gov.in/newsite/PrintRelease.aspx?relid=68464"

# --- 2011: offset expansion references -----------------------------------
fetch "2011_defence_offset_policy_relid107868.html" "https://www.pib.gov.in/newsite/PrintRelease.aspx?relid=107868"
fetch "2011_defence_procurement_policy_relid83718.html" "https://www.pib.gov.in/newsite/PrintRelease.aspx?relid=83718"

# --- 2013: DPP-2013 categorisation + delegation --------------------------
fetch "2013_DPP2013_relid95274.html" "https://www.pib.gov.in/newsite/PrintRelease.aspx?relid=95274&reg=3&lang=2"

# --- 2016: DPP-2016 / IDDM / Make-I&II -----------------------------------
fetch "2016_new_weapons_procurement_policy_PRID1519140.html" "https://www.pib.gov.in/Pressreleaseshare.aspx?PRID=1519140"
fetch "2016_new_weapons_procurement_policy_iframe_PRID1519140.html" "https://pib.gov.in/PressReleaseIframePage.aspx?PRID=1519140"

# --- 2020: DAP-2020 launch + draft ---------------------------------------
fetch "2020_DAP2020_launch_PRID1659746.html" "https://www.pib.gov.in/PressReleasePage.aspx?PRID=1659746"
fetch "2020_DAP2020_draft_PRID1607400.html"  "https://www.pib.gov.in/PressReleasePage.aspx?PRID=1607400"

# --- 2020: FDI 49%->74% --------------------------------------------------
fetch "2020_FDI_defence_pressnote4_PRID1656082.html" "https://www.pib.gov.in/Pressreleaseshare.aspx?PRID=1656082"
fetch "2020_FDI_defence_MoD_PRID1654091.html"        "https://www.pib.gov.in/PressReleasePage.aspx?PRID=1654091"

# --- 2020: negative import list ------------------------------------------
fetch "2020_negative_import_list_PRID1644570.html" "https://www.pib.gov.in/PressReleasePage.aspx?PRID=1644570"

echo
echo "Done. Files in: $OUT"
echo "If any FAILED, open the URL in a browser (PIB sometimes blocks non-browser clients)."
