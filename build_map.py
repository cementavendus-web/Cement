#!/usr/bin/env python3
"""Build a self-contained interactive choropleth of world 'military burden'
(military expenditure as a share of GDP), recreating the SIPRI Military
Expenditure Database world map.

Geometry: Natural Earth 1:110m (bundled with GeoPandas).
Data: SIPRI Military Expenditure Database (share of GDP). The 40 largest
spenders use the exact 2025 GDP-share figures from the accompanying workbook
(SIPRI_Military_Expenditure_2025.xlsx); remaining countries use SIPRI
database GDP-share figures (latest available year). Countries without a
usable SIPRI figure are drawn in grey ("No data"), matching the source map.
"""
import json
import math
import warnings

import geopandas as gpd
import openpyxl

warnings.filterwarnings("ignore")

# ----------------------------------------------------------------------------
# 1. Military burden (military expenditure as % of GDP), keyed by ISO-A3.
#    Exact top-40 values are loaded from the workbook below; this table
#    supplies the rest of the world from the SIPRI database.
# ----------------------------------------------------------------------------
BURDEN = {
    # --- additional countries (SIPRI database, share of GDP) ---
    "ALB": 2.0, "AGO": 1.5, "ARG": 0.8, "ARM": 5.0, "AUT": 0.9, "AZE": 4.6,
    "BHS": 0.7, "BGD": 1.1, "BLR": 1.5, "BLZ": 1.2, "BEN": 0.7, "BOL": 1.4,
    "BIH": 0.8, "BWA": 2.9, "BRN": 3.0, "BGR": 1.9, "BFA": 5.0, "BDI": 2.0,
    "KHM": 2.2, "CMR": 1.2, "CAF": 1.5, "TCD": 2.5, "CHL": 1.9, "COG": 2.5,
    "HRV": 1.8, "CYP": 2.0, "CIV": 1.1, "COD": 1.1, "DJI": 3.5, "DOM": 0.7,
    "ECU": 2.4, "EGY": 1.2, "SLV": 1.0, "EST": 3.4, "ETH": 0.8, "FJI": 1.0,
    "GAB": 1.6, "GMB": 1.0, "GEO": 1.6, "GHA": 0.6, "GTM": 0.4, "GIN": 1.6,
    "GNB": 1.6, "GUY": 0.9, "HND": 1.7, "HUN": 2.1, "IRL": 0.2, "JAM": 1.4,
    "JOR": 4.7, "KAZ": 0.6, "KEN": 1.1, "KGZ": 1.4, "LVA": 3.2, "LBN": 3.0,
    "LSO": 1.8, "LBR": 0.6, "LTU": 2.9, "LUX": 0.7, "MDG": 0.6, "MWI": 0.8,
    "MYS": 1.0, "MLI": 4.0, "MRT": 2.4, "MDA": 0.6, "MNG": 0.7, "MNE": 1.6,
    "MAR": 4.3, "MOZ": 1.1, "MMR": 3.0, "NAM": 3.0, "NPL": 1.3, "NZL": 1.3,
    "NIC": 0.7, "NER": 2.5, "NGA": 0.9, "MKD": 1.7, "OMN": 5.6, "PAN": 0.0,
    "PNG": 0.5, "PRY": 1.0, "PER": 1.2, "PHL": 1.0, "PRT": 1.6, "RWA": 1.4,
    "SEN": 1.6, "SRB": 2.3, "SLE": 0.8, "SVK": 2.0, "SVN": 1.3, "ZAF": 0.7,
    "LKA": 1.6, "SUR": 0.6, "TZA": 1.0, "THA": 1.3, "TLS": 1.5, "TGO": 2.0,
    "TTO": 0.8, "TUN": 2.7, "UGA": 2.4, "URY": 1.9, "ZMB": 1.2, "ZWE": 0.9,
    "SWZ": 1.8, "CRI": 0.0, "ISL": 0.0, "SLB": 0.0, "VUT": 0.0,
}

# Countries with no national armed forces -> "No spending" bucket (0.0 above).
NO_SPENDING = {"CRI", "ISL", "PAN", "SLB", "VUT"}

# Exact 2025 GDP-share figures for the 40 largest spenders, matched by name.
TOP40_NAME_TO_ISO = {
    "United States": "USA", "China": "CHN", "Russia": "RUS", "Germany": "DEU",
    "India": "IND", "United Kingdom": "GBR", "Ukraine": "UKR",
    "Saudi Arabia": "SAU", "France": "FRA", "Japan": "JPN", "Israel": "ISR",
    "Italy": "ITA", "South Korea": "KOR", "Poland": "POL", "Spain": "ESP",
    "Canada": "CAN", "Australia": "AUS", "Turkiye": "TUR", "Netherlands": "NLD",
    "Algeria": "DZA", "Brazil": "BRA", "Taiwan": "TWN", "Singapore": "SGP",
    "Norway": "NOR", "Sweden": "SWE", "Indonesia": "IDN", "Denmark": "DNK",
    "Belgium": "BEL", "Colombia": "COL", "Mexico": "MEX", "Pakistan": "PAK",
    "Viet Nam": "VNM", "Romania": "ROU", "Greece": "GRC", "Kuwait": "KWT",
    "Finland": "FIN", "Switzerland": "CHE", "Iran": "IRN", "Czechia": "CZE",
    "Iraq": "IRQ",
}

wb = openpyxl.load_workbook("SIPRI_Military_Expenditure_2025.xlsx", data_only=True)
ws = wb["Top 40 Spenders 2025"]
for row in ws.iter_rows(min_row=2, values_only=True):
    if isinstance(row[0], int) and row[2] in TOP40_NAME_TO_ISO:
        BURDEN[TOP40_NAME_TO_ISO[row[2]]] = float(row[6])

# ----------------------------------------------------------------------------
# 2. SIPRI legend: ordered bins + colours (blue = high burden, gold = low).
# ----------------------------------------------------------------------------
BINS = [
    ("≥ 9.0%",   9.0, math.inf, "#1c3f5f"),
    ("5.0%–9.0%", 5.0, 9.0,     "#4f74a3"),
    ("4.0%–5.0%", 4.0, 5.0,     "#6d93bd"),
    ("3.0%–4.0%", 3.0, 4.0,     "#93b4d6"),
    ("2.0%–3.0%", 2.0, 3.0,     "#c4d8e8"),
    ("1.0%–2.0%", 1.0, 2.0,     "#dc9a34"),
    ("< 1%",           0.001, 1.0,   "#ecd992"),
    ("No spending",    0.0, 0.001,   "#f5efd8"),
]
NODATA_COLOR = "#d5d5d5"


def classify(v):
    if v is None:
        return None
    for i, (label, lo, hi, _c) in enumerate(BINS):
        if label == "No spending":
            if v == 0.0:
                return i
        elif lo <= v < hi:
            return i
    return None


# ----------------------------------------------------------------------------
# 3. Web-Mercator projection (matches SIPRI's map), Antarctica cropped.
# ----------------------------------------------------------------------------
LAT_MAX, LAT_MIN = 84.0, -58.0


def merc_y(lat):
    lat = max(min(lat, LAT_MAX), LAT_MIN)
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


Y_TOP, Y_BOT = merc_y(LAT_MAX), merc_y(LAT_MIN)
X_MIN, X_MAX = -180.0, 180.0
W = 1000.0
H = W * (Y_TOP - Y_BOT) / (X_MAX - X_MIN)


def project(lon, lat):
    x = (lon - X_MIN) / (X_MAX - X_MIN) * W
    y = (Y_TOP - merc_y(lat)) / (Y_TOP - Y_BOT) * H
    return x, y


def ring_to_path(coords):
    pts = []
    for lon, lat in coords:
        x, y = project(lon, lat)
        pts.append(f"{x:.1f},{y:.1f}")
    if not pts:
        return ""
    return "M" + "L".join(pts) + "Z"


def geom_to_path(geom):
    parts = []
    if geom.geom_type == "Polygon":
        polys = [geom]
    elif geom.geom_type == "MultiPolygon":
        polys = list(geom.geoms)
    else:
        return ""
    for poly in polys:
        parts.append(ring_to_path(list(poly.exterior.coords)))
        for interior in poly.interiors:
            parts.append(ring_to_path(list(interior.coords)))
    return "".join(p for p in parts if p)


# ----------------------------------------------------------------------------
# 4. Build SVG paths.
# ----------------------------------------------------------------------------
g = gpd.read_file(gpd.datasets.get_path("naturalearth_lowres"), engine="pyogrio")
# Crop Antarctica and a few tiny outlying territories; everything else is
# drawn (grey "No data" where SIPRI has no figure) so there are no gaps.
SKIP = {"ATA", "ATF", "FLK", "PRI", "NCL", "ATC"}

paths = []
covered = 0
for _, r in g.iterrows():
    iso = r["iso_a3"]
    name = r["name"]
    if iso in SKIP:
        continue
    d = geom_to_path(r["geometry"])
    if not d:
        continue
    v = BURDEN.get(iso)
    bin_idx = classify(v)
    color = BINS[bin_idx][3] if bin_idx is not None else NODATA_COLOR
    if bin_idx is not None:
        covered += 1
    if v is None:
        val_str = "No data"
    elif v == 0.0:
        val_str = "No spending"
    else:
        val_str = f"{v:g}% of GDP"
    paths.append({"name": name, "d": d, "c": color, "v": val_str})

print(f"Rendered {len(paths)} countries; {covered} with a burden value.")

# ----------------------------------------------------------------------------
# 5. Emit self-contained HTML.
# ----------------------------------------------------------------------------
NAME_FIX = {
    "United States of America": "United States",
    "Dem. Rep. Congo": "Dem. Rep. of the Congo",
    "Bosnia and Herz.": "Bosnia and Herzegovina",
    "Central African Rep.": "Central African Republic",
    "Dominican Rep.": "Dominican Republic",
    "Eq. Guinea": "Equatorial Guinea",
    "S. Sudan": "South Sudan",
    "W. Sahara": "Western Sahara",
    "N. Cyprus": "Northern Cyprus",
    "Solomon Is.": "Solomon Islands",
    "Fr. S. Antarctic Lands": "French Southern Territories",
    "eSwatini": "Eswatini",
}
for p in paths:
    p["name"] = NAME_FIX.get(p["name"], p["name"])

svg_paths = "\n".join(
    f'<path d="{p["d"]}" fill="{p["c"]}" '
    f'data-name="{p["name"]}" data-val="{p["v"]}"/>'
    for p in paths
)

legend_items = "\n".join(
    f'<div class="lg-item"><span class="sw" style="background:{c}"></span>'
    f'<span>{label}</span></div>'
    for (label, _lo, _hi, c) in BINS
)
legend_items += (
    f'\n<div class="lg-item"><span class="sw" style="background:{NODATA_COLOR}">'
    f'</span><span>No data</span></div>'
)

html = f"""<title>SIPRI — World Military Burden</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{
    --bg: #ffffff; --ink: #1b2430; --muted: #5b6672;
    --border: #e3e6ea; --sea: #ffffff; --stroke: #ffffff;
    --panel: #f7f8fa;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#12161c; --ink:#e8ecf1; --muted:#9aa6b2;
             --border:#2a3239; --sea:#12161c; --stroke:#12161c;
             --panel:#1a1f26; }}
  }}
  :root[data-theme="light"] {{ --bg:#fff; --ink:#1b2430; --muted:#5b6672;
    --border:#e3e6ea; --sea:#fff; --stroke:#fff; --panel:#f7f8fa; }}
  :root[data-theme="dark"] {{ --bg:#12161c; --ink:#e8ecf1; --muted:#9aa6b2;
    --border:#2a3239; --sea:#12161c; --stroke:#12161c; --panel:#1a1f26; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
    font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }}
  .wrap {{ max-width: 1120px; margin: 0 auto; padding: 22px 20px 40px; }}
  h1 {{ font-size: 20px; font-weight: 650; margin: 0 0 2px; letter-spacing:-.01em; }}
  .sub {{ color: var(--muted); font-size: 13.5px; margin: 0 0 16px; }}
  .hint {{ display:flex; align-items:center; gap:7px; color:var(--muted);
    font-size:12.5px; margin-bottom:8px; }}
  .hint .dot {{ width:9px; height:9px; border-radius:50%; background:#dc9a34; }}
  .figure {{ position: relative; }}
  .mapbox {{ position: relative; overflow-x:auto; }}
  svg {{ display:block; width:100%; height:auto; background:var(--sea); }}
  path {{ stroke: var(--stroke); stroke-width: .4; vector-effect: non-scaling-stroke;
    transition: fill .1s ease; }}
  path:hover {{ stroke:#1b2430; stroke-width:1.1; }}
  @media (prefers-color-scheme: dark) {{ path:hover {{ stroke:#e8ecf1; }} }}
  :root[data-theme="dark"] path:hover {{ stroke:#e8ecf1; }}
  .legend {{ display:flex; flex-wrap:wrap; gap: 4px 20px; margin: 14px 0 0;
    padding: 0; }}
  .lg-item {{ display:flex; align-items:center; gap:8px; font-size:12.5px;
    color: var(--ink); }}
  .sw {{ width:20px; height:13px; border-radius:2px; display:inline-block;
    border:1px solid rgba(0,0,0,.08); }}
  .tip {{ position: fixed; pointer-events:none; z-index:10; opacity:0;
    transform: translate(-50%, -120%); transition: opacity .08s;
    background: var(--ink); color: var(--bg); padding: 6px 10px;
    border-radius: 7px; font-size: 12.5px; white-space:nowrap;
    box-shadow: 0 6px 20px rgba(0,0,0,.28); }}
  .tip b {{ font-weight:650; }}
  .tip .v {{ opacity:.85; }}
  .note {{ margin-top: 22px; padding-top: 14px; border-top:1px solid var(--border);
    color: var(--muted); font-size: 11.5px; line-height:1.55; max-width: 760px; }}
  .note a {{ color: inherit; }}
</style>
<div class="wrap">
  <h1>World military burden, 2025</h1>
  <p class="sub">Military expenditure as a share of gross domestic product (GDP)</p>
  <div class="hint"><span class="dot"></span> Mouseover the countries to see more data</div>
  <div class="figure">
    <div class="mapbox">
      <svg viewBox="0 0 {W:.0f} {H:.0f}" role="img"
           aria-label="Choropleth map of military expenditure as a share of GDP by country, 2025">
        {svg_paths}
      </svg>
    </div>
    <div class="legend">
      {legend_items}
    </div>
  </div>
  <div class="tip" id="tip"></div>
  <div class="note">
    *&nbsp;&lsquo;Military burden&rsquo; is military expenditure as a share of gross
    domestic product (GDP); it represents the relative economic cost of the military.<br>
    <i>Note:</i> The boundaries used in this map do not imply any endorsement or
    acceptance. Countries shown in grey represent areas where data is unavailable
    or not reported.<br>
    <i>Source:</i> SIPRI Military Expenditure Database, Apr.&nbsp;2026. The 40 largest
    spenders use 2025 GDP-share figures; other countries use the latest available
    SIPRI figure. Geometry: Natural Earth (1:110m).
  </div>
</div>
<script>
  const tip = document.getElementById('tip');
  const svg = document.querySelector('svg');
  svg.addEventListener('mousemove', (e) => {{
    const t = e.target;
    if (t.tagName === 'path') {{
      tip.innerHTML = '<b>' + t.dataset.name + '</b><br><span class="v">'
        + t.dataset.val + '</span>';
      tip.style.left = e.clientX + 'px';
      tip.style.top = e.clientY + 'px';
      tip.style.opacity = '1';
    }} else {{ tip.style.opacity = '0'; }}
  }});
  svg.addEventListener('mouseleave', () => {{ tip.style.opacity = '0'; }});
</script>
"""

with open("sipri_military_burden_map.html", "w") as f:
    f.write(html)
print("Wrote sipri_military_burden_map.html")
