#!/usr/bin/env python3
"""
Build: AI Token Cost vs Intelligence & Energy / DC-Capacity workbook.
Every quantitative cell that can be derived is a LIVE FORMULA so the user can
trace and adjust. Sources are attached per-row. Measured vs estimate is flagged.
"""
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, NamedStyle
from openpyxl.chart import ScatterChart, BarChart, Reference, Series
from openpyxl.chart.label import DataLabelList
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.hyperlink import Hyperlink

# ---------- palette (from validated dataviz reference) ----------
BLUE   = "2A78D6"   # closed / frontier
GREEN  = "008300"   # open weights
ORANGE = "EB6834"
YELLOW = "EDA100"
DKBLUE = "1C5CAB"   # header fill
NAVY   = "104281"
GREY   = "52514E"
LGREY  = "F2F2F0"
INPUTF = "FFF3CD"   # adjustable input cells (soft yellow)
MEASF  = "E5F3E5"   # measured (soft green)
ESTF   = "FDF0DD"   # estimate (soft orange)
WHITE  = "FFFFFF"

thin = Side(style="thin", color="D0D0CC")
med  = Side(style="medium", color=DKBLUE)
box  = Border(left=thin, right=thin, top=thin, bottom=thin)

def font(sz=11, b=False, color="0B0B0B", italic=False):
    return Font(name="Calibri", size=sz, bold=b, color=color, italic=italic)
def fill(hex_):
    return PatternFill("solid", fgColor=hex_)
def center(wrap=False):
    return Alignment(horizontal="center", vertical="center", wrap_text=wrap)
def left(wrap=True):
    return Alignment(horizontal="left", vertical="center", wrap_text=wrap)

wb = openpyxl.Workbook()

def style_header(ws, row, cols, height=22):
    ws.row_dimensions[row].height = height
    for c in cols:
        cell = ws[f"{c}{row}"]
        cell.fill = fill(DKBLUE); cell.font = font(11, True, WHITE)
        cell.alignment = center(True); cell.border = box

def title_block(ws, title, subtitle, span="A1:H1"):
    ws.merge_cells(span)
    a = span.split(":")[0]
    ws[a] = title
    ws[a].font = font(16, True, NAVY); ws[a].alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 28
    r2 = span.replace("1", "2")
    ws.merge_cells(r2)
    a2 = r2.split(":")[0]
    ws[a2] = subtitle
    ws[a2].font = font(10, False, GREY, italic=True); ws[a2].alignment = left()
    ws.row_dimensions[2].height = 16

def link_cell(ws, coord, url, text="link"):
    c = ws[coord]
    c.value = text
    c.hyperlink = url
    c.font = font(9, False, "1155CC")
    c.alignment = left()

# =====================================================================
# SHEET 0: README
# =====================================================================
ws = wb.active
ws.title = "README"
ws.sheet_view.showGridLines = False
title_block(ws, "AI Models: Cost vs Intelligence vs Energy — Data Pack",
            "Built 2026-07-19 for a slide comparing token cost & intelligence, and the DC-capacity implications of a shift to open-weight models.", "A1:H1")

readme = [
 ("", ""),
 ("HOW TO READ THIS WORKBOOK", ""),
 ("Tab", "What's in it"),
 ("1. Intelligence vs Cost", "Your Artificial Analysis v4.1 table + computed intelligence-per-dollar. Scatter chart. (SOURCE = your table; not independently re-verified — see note on tab.)"),
 ("2. Energy per Query", "Verified per-query electricity figures (Google, OpenAI, Epoch). Every row cites a primary source."),
 ("3. Energy per Token", "Per-token energy + open-weight vs frontier efficiency benchmarks (measured on GPU meters)."),
 ("4. DC Capacity — Baseline", "Current & projected AI data-center power (GW / TWh), US and global."),
 ("5. Open-Shift Scenario", "Parametric model: what happens to aggregate DC power if inference shifts to open-weight models. LIVE formulas — change the yellow cells."),
 ("6. Sources", "Every URL, publisher, date, quality rating, and verification status."),
 ("7. Assumptions & Method", "Every assumption and formula written out in plain English."),
 ("", ""),
 ("COLOUR / FLAG LEGEND", ""),
 ("Yellow cell", "Adjustable input / assumption — change it and dependent formulas update."),
 ("MEASURED", "Directly measured (e.g. GPU power meters, production telemetry)."),
 ("ESTIMATE", "Modeled or self-reported estimate — treat as indicative, not audited."),
 ("", ""),
 ("TOP-LINE CAVEATS", ""),
 ("1", "Per-query energy is falling fast (Google cut its median-prompt energy 33x in one year), so absolute Wh numbers date quickly."),
 ("2", "Google's 0.24 Wh and OpenAI's 0.34 Wh are vendor self-reported and EXCLUDE model-training energy."),
 ("3", "MoE / open-weight energy advantages were measured on single GPUs; real-world gaps at hyperscaler batch sizes are unproven."),
 ("4", "The Artificial Analysis Intelligence Index scores in tab 1 could NOT be machine-verified in this pass (site not crawlable). Confirm against artificialanalysis.ai."),
 ("5", "The open-shift scenario (tab 5) is an ILLUSTRATIVE parametric model, not a forecast. Its job is to show which levers decide the direction."),
]
r = 3
for a, b in readme:
    ws[f"A{r}"] = a; ws[f"C{r}"] = b
    if a in ("HOW TO READ THIS WORKBOOK","COLOUR / FLAG LEGEND","TOP-LINE CAVEATS"):
        ws[f"A{r}"].font = font(12, True, NAVY)
    elif a == "Tab":
        ws[f"A{r}"].font = font(10, True, WHITE); ws[f"A{r}"].fill = fill(DKBLUE)
        ws[f"C{r}"].font = font(10, True, WHITE); ws[f"C{r}"].fill = fill(DKBLUE)
    else:
        ws[f"A{r}"].font = font(10, True)
        ws[f"C{r}"].font = font(10)
    ws[f"C{r}"].alignment = left()
    ws.merge_cells(f"C{r}:H{r}")
    r += 1
ws.column_dimensions["A"].width = 26
ws.column_dimensions["B"].width = 2
for col in "CDEFGH":
    ws.column_dimensions[col].width = 15

# =====================================================================
# SHEET 1: Intelligence vs Cost
# =====================================================================
ws = wb.create_sheet("1. Intelligence vs Cost")
ws.sheet_view.showGridLines = False
title_block(ws, "Intelligence vs Cost per Task",
            "Source: your Artificial Analysis Intelligence Index v4.1 table. NOT independently re-verified (see README caveat 4). Intelligence-per-$ is computed live.", "A1:H1")

# columns: Model | Type | AAII v4.1 | $/task | Released | Intelligence per $ | Notes
hdr = ["Model", "Type", "AAII v4.1", "$ / task", "Released", "Intelligence per $ (=C/D)"]
hr = 4
for i, h in enumerate(hdr):
    ws.cell(hr, i+1, h)
style_header(ws, hr, [get_column_letter(i+1) for i in range(len(hdr))])

# data: name, type(open/closed), aaii, cost, released
rows = [
 ("Claude Fable 5 (max)",             "Closed frontier", 60,  2.75, "Jun 2026"),
 ("GPT-5.6 Sol (max)",                "Closed frontier", 59,  1.04, "9 Jul 2026"),
 ("Kimi K3 (open weights, 2.8T)",     "Open weights",    57,  0.94, "16 Jul 2026"),
 ("GPT-5.6 Terra (max)",              "Closed frontier", 55,  0.55, "9 Jul 2026"),
 ("Grok 4.5 (high)",                  "Closed frontier", 54,  0.31, "Jul 2026"),
 ("Claude Opus 4.8 (max)",            "Closed frontier", 54,  1.80, "—"),   # ~54*
 ("GPT-5.6 Luna (max)",               "Closed frontier", 51,  0.21, "9 Jul 2026"),
 ("Kimi K2.6 (open weights)",         "Open weights",    44,  0.33, "Apr 2026"),
]
start = hr + 1
for j, (name, typ, aaii, cost, rel) in enumerate(rows):
    r = start + j
    ws.cell(r, 1, name).font = font(10, True)
    tcell = ws.cell(r, 2, typ)
    tcell.font = font(10, True, GREEN if typ == "Open weights" else BLUE)
    ws.cell(r, 3, aaii).alignment = center()
    ws.cell(r, 4, cost).number_format = '"$"0.00'
    ws.cell(r, 4).alignment = center()
    ws.cell(r, 5, rel).alignment = center()
    ws.cell(r, 6).value = f"=C{r}/D{r}"          # live formula
    ws.cell(r, 6).number_format = '0.0'
    ws.cell(r, 6).alignment = center()
    for col in range(1, 7):
        ws.cell(r, col).border = box
ws.cell(start+5, 3).comment = None
# note the ~54* for Opus
n = start + len(rows) + 1
ws.cell(n, 1, "* Claude Opus 4.8 shown as ~54 (estimated placement pending full v4.1 scoring in your table).").font = font(9, italic=True, color=GREY)
ws.merge_cells(f"A{n}:F{n}")

ws.column_dimensions["A"].width = 30
ws.column_dimensions["B"].width = 16
for col in "CDEF":
    ws.column_dimensions[col].width = 15
ws.column_dimensions["F"].width = 22

# scatter: x = $/task (cost), y = AAII. Bubble-ish via two series (open vs closed) would need split;
# openpyxl scatter can't color per-point, so build one series but we annotate in the slide instead.
chart = ScatterChart()
chart.title = "Intelligence (AAII v4.1) vs Cost ($/task)"
chart.x_axis.title = "Cost per task (US$)  — cheaper is left"
chart.y_axis.title = "Intelligence (AAII v4.1)"
chart.height = 9; chart.width = 16
chart.x_axis.majorGridlines = None
xref = Reference(ws, min_col=4, min_row=start, max_row=start+len(rows)-1)
yref = Reference(ws, min_col=3, min_row=start, max_row=start+len(rows)-1)
s = Series(yref, xref, title="Models")
s.marker.symbol = "circle"; s.marker.size = 9
s.graphicalProperties.line.noFill = True
chart.series.append(s)
chart.x_axis.delete = False; chart.y_axis.delete = False
ws.add_chart(chart, "H4")

# =====================================================================
# SHEET 2: Energy per Query
# =====================================================================
ws = wb.create_sheet("2. Energy per Query")
ws.sheet_view.showGridLines = False
title_block(ws, "Electricity per Query",
            "Per-prompt energy for leading assistants. All figures EXCLUDE training energy. Flagged measured vs estimate.", "A1:I1")

hdr = ["Model / source", "Wh per query", "Joules per query (=B*3600)", "Type", "Flag", "Date", "Note", "Source"]
hr = 4
for i, h in enumerate(hdr):
    ws.cell(hr, i+1, h)
style_header(ws, hr, [get_column_letter(i+1) for i in range(len(hdr))], height=30)

# name, wh, type, flag, date, note, url
eq = [
 ("Google Gemini — median prompt (comprehensive, full-stack)", 0.24, "Google", "MEASURED", "Aug 2025",
  "Full stack: TPU 58% + host CPU/RAM 25% + idle/failover 10% + DC overhead 8%; PUE 1.09.",
  "https://cloud.google.com/blog/products/infrastructure/measuring-the-environmental-impact-of-ai-inference"),
 ("Google Gemini — median prompt (accelerator-only)", 0.10, "Google", "MEASURED", "Aug 2025",
  "Chip-only method; Google says it understates true operating footprint (2.4x below comprehensive).",
  "https://arxiv.org/abs/2508.15734"),
 ("OpenAI ChatGPT — average query", 0.34, "OpenAI", "ESTIMATE", "Jun 2025",
  "Sam Altman, 'The Gentle Singularity'. 'Average query' undefined; methodology unpublished.",
  "https://blog.samaltman.com/the-gentle-singularity"),
 ("Epoch AI — typical GPT-4o query", 0.30, "Epoch AI", "ESTIMATE", "Feb 2025",
  "Modeled: ~100B active params (MoE) x 2 x 500 tokens = 1e14 FLOP; ~1s H100 x 1500W x 70% util.",
  "https://epoch.ai/gradient-updates/how-much-energy-does-chatgpt-use"),
 ("Older widely-cited estimate (for contrast)", 3.00, "various", "ESTIMATE", "2023-24",
  "The ~3 Wh figure that the 2025 estimates revised down ~10x.",
  "https://epoch.ai/gradient-updates/how-much-energy-does-chatgpt-use"),
]
start = hr + 1
for j, (name, wh, typ, flag, date, note, url) in enumerate(eq):
    r = start + j
    ws.cell(r, 1, name).font = font(10, True)
    ws.cell(r, 1).alignment = left()
    ws.cell(r, 2, wh).number_format = '0.00'; ws.cell(r, 2).alignment = center()
    ws.cell(r, 3).value = f"=B{r}*3600"; ws.cell(r, 3).number_format = '0'; ws.cell(r, 3).alignment = center()
    ws.cell(r, 4, typ).alignment = center()
    fc = ws.cell(r, 5, flag); fc.alignment = center()
    fc.fill = fill(MEASF if flag == "MEASURED" else ESTF)
    fc.font = font(9, True, GREEN if flag == "MEASURED" else ORANGE)
    ws.cell(r, 6, date).alignment = center()
    ws.cell(r, 7, note).alignment = left()
    link_cell(ws, f"H{r}", url, "source")
    for col in range(1, 9):
        ws.cell(r, col).border = box
widths = [42, 12, 14, 10, 11, 40, 40, 9]
for i, w in enumerate(widths):
    ws.column_dimensions[get_column_letter(i+1)].width = w

# bar chart of Wh per query
bar = BarChart(); bar.type = "bar"; bar.title = "Electricity per query (Wh)"
bar.height = 8; bar.width = 16
data = Reference(ws, min_col=2, min_row=hr, max_row=start+len(eq)-1)
cats = Reference(ws, min_col=1, min_row=start, max_row=start+len(eq)-1)
bar.add_data(data, titles_from_data=True); bar.set_categories(cats)
bar.legend = None
bar.dataLabels = DataLabelList(); bar.dataLabels.showVal = True
ws.add_chart(bar, "A12")

# =====================================================================
# SHEET 3: Energy per Token / open vs frontier
# =====================================================================
ws = wb.create_sheet("3. Energy per Token")
ws.sheet_view.showGridLines = False
title_block(ws, "Energy per Token & Open-Weight vs Frontier Efficiency",
            "Measured on GPU power meters unless flagged. Key levers: model sparsity (MoE), inference engine, batch size.", "A1:I1")

hdr = ["Finding", "Value", "Unit", "Flag", "Date", "Detail", "Source"]
hr = 4
for i, h in enumerate(hdr):
    ws.cell(hr, i+1, h)
style_header(ws, hr, [get_column_letter(i+1) for i in range(len(hdr))], height=28)

et = [
 ("Marginal energy per output token (decode-dominated)", "~1.72", "J / token", "MEASURED", "2025",
  "Each output token = one forward pass; decode dominates, prefill negligible. Near-linear in output tokens.",
  "https://aclanthology.org/2025.acl-long.1563.pdf"),
 ("Llama 3.1 8B on H100, batch 64", "0.12–0.20", "J / token", "MEASURED", "2025",
  "ML.ENERGY benchmark, 40 model architectures x 6 tasks. Accelerator-only.",
  "https://arxiv.org/html/2505.06371v1"),
 ("MoE vs dense: GPT-OSS-20B vs Qwen3-32B (energy)", "-25.8%", "J / 1k tok", "MEASURED", "Aug 2025",
  "GPT-OSS-20B = 3.61B of 20.9B params active (17.3%). Lower J/1k generated tokens at 2048/64 context.",
  "https://arxiv.org/pdf/2508.16700"),
 ("MoE vs dense: GPT-OSS-20B vs Qwen3-32B (throughput)", "+34.2%", "tokens / Watt", "MEASURED", "Aug 2025",
  "Same test: markedly higher per-active-parameter efficiency than dense baselines.",
  "https://arxiv.org/pdf/2508.16700"),
 ("Mixtral-8x7B vs dense of similar capability", "2–3x lower", "J / token", "ESTIMATE", "2025",
  "13B of 46.7B active. Asserted in preprint; not independently measured.",
  "https://arxiv.org/abs/2401.04088"),
 ("Inference engine: vLLM / TensorRT-LLM vs HF Transformers", "-25 to -40%", "J / token", "MEASURED", "Dec 2025",
  "TokenPowerBench (AAAI'26). Engine choice alone. Larger reduction under high concurrency.",
  "https://arxiv.org/html/2512.03024v1"),
 ("Scaling within family: Llama3 1B → 70B", "7.3x", "energy / token", "MEASURED", "Dec 2025",
  "70x more parameters raised per-token energy only 7.3x — sublinear.",
  "https://arxiv.org/html/2512.03024v1"),
]
start = hr + 1
for j, (name, val, unit, flag, date, detail, url) in enumerate(et):
    r = start + j
    ws.cell(r, 1, name).font = font(10, True); ws.cell(r, 1).alignment = left()
    ws.cell(r, 2, val).alignment = center(); ws.cell(r, 2).font = font(10, True, NAVY)
    ws.cell(r, 3, unit).alignment = center()
    fc = ws.cell(r, 4, flag); fc.alignment = center()
    fc.fill = fill(MEASF if flag == "MEASURED" else ESTF)
    fc.font = font(9, True, GREEN if flag == "MEASURED" else ORANGE)
    ws.cell(r, 5, date).alignment = center()
    ws.cell(r, 6, detail).alignment = left()
    link_cell(ws, f"G{r}", url, "source")
    for col in range(1, 8):
        ws.cell(r, col).border = box
widths = [40, 12, 14, 11, 9, 50, 9]
for i, w in enumerate(widths):
    ws.column_dimensions[get_column_letter(i+1)].width = w

cav = start + len(et) + 1
ws.cell(cav, 1, "Caveat: MoE advantages measured on single GPUs with dense-optimized runtimes. At hyperscaler batch sizes (tokens routed across many experts) per-token savings shrink, and all expert weights must sit in VRAM despite few being active.")
ws.merge_cells(f"A{cav}:G{cav}")
ws.cell(cav, 1).font = font(9, italic=True, color=ORANGE); ws.cell(cav, 1).alignment = left()
ws.row_dimensions[cav].height = 28

print("sheets 0-3 done")

# =====================================================================
# SHEET 4: DC Capacity baseline
# =====================================================================
ws = wb.create_sheet("4. DC Capacity - Baseline")
ws.sheet_view.showGridLines = False
title_block(ws, "AI Data-Center Power — Baseline & Projections",
            "Anchor figures for the scenario model on the next tab. GW = continuous power; TWh/yr = annual energy.", "A1:H1")

hdr = ["Metric", "Value", "Unit", "Flag", "Horizon", "Scope", "Source"]
hr = 4
for i, h in enumerate(hdr):
    ws.cell(hr, i+1, h)
style_header(ws, hr, [get_column_letter(i+1) for i in range(len(hdr))], height=24)

dc = [
 ("US AI power demand — today", 5, "GW", "ESTIMATE", "2025", "US, AI training+inference",
  "https://epoch.ai/blog/power-demands-of-frontier-ai-training"),
 ("US AI power demand — 2030", 50, "GW", "ESTIMATE", "2030", "US, AI training+inference (>5% of US generation)",
  "https://epoch.ai/blog/power-demands-of-frontier-ai-training"),
 ("Largest single training run — 2028", 1.5, "GW", "ESTIMATE", "2028", "One frontier run",
  "https://epoch.ai/blog/power-demands-of-frontier-ai-training"),
 ("Largest single training run — 2030", 10, "GW", "ESTIMATE", "2030", "One frontier run (range 4-16 GW)",
  "https://epoch.ai/blog/power-demands-of-frontier-ai-training"),
 ("Global data-center electricity — 2024", 415, "TWh/yr", "REPORTED*", "2024", "All data centers (not only AI)",
  "https://www.iea.org/reports/energy-and-ai/energy-demand-from-ai"),
 ("Global data-center electricity — 2030", 945, "TWh/yr", "REPORTED*", "2030", "All data centers, ~3% of global electricity",
  "https://www.iea.org/reports/energy-and-ai/energy-demand-from-ai"),
]
start = hr + 1
for j, (name, val, unit, flag, hz, scope, url) in enumerate(dc):
    r = start + j
    ws.cell(r, 1, name).font = font(10, True); ws.cell(r, 1).alignment = left()
    ws.cell(r, 2, val).alignment = center(); ws.cell(r, 2).font = font(10, True, NAVY)
    ws.cell(r, 3, unit).alignment = center()
    fc = ws.cell(r, 4, flag); fc.alignment = center()
    fc.fill = fill(ESTF); fc.font = font(9, True, ORANGE)
    ws.cell(r, 5, hz).alignment = center()
    ws.cell(r, 6, scope).alignment = left()
    link_cell(ws, f"G{r}", url, "source")
    for col in range(1, 8):
        ws.cell(r, col).border = box
widths = [34, 10, 10, 12, 10, 40, 9]
for i, w in enumerate(widths):
    ws.column_dimensions[get_column_letter(i+1)].width = w
note = start + len(dc) + 1
ws.cell(note, 1, "* IEA 'Energy and AI' figures could not be machine-fetched in this research pass (site returned no crawlable content). Confirm on iea.org before citing. All others verified 3-0.")
ws.merge_cells(f"A{note}:G{note}")
ws.cell(note, 1).font = font(9, italic=True, color=ORANGE); ws.cell(note, 1).alignment = left()
ws.row_dimensions[note].height = 26
conv = note + 2
ws.cell(conv, 1, "Unit helper: 1 TWh/yr = 0.11416 GW continuous  (=1e12 Wh / 8760 h / 1e9).").font = font(9, italic=True, color=GREY)
ws.merge_cells(f"A{conv}:G{conv}")

# =====================================================================
# SHEET 5: Open-Shift Scenario Model  (LIVE formulas)
# =====================================================================
ws = wb.create_sheet("5. Open-Shift Scenario")
ws.sheet_view.showGridLines = False
title_block(ws, "Open-Source Shift → DC Capacity: Parametric Model",
            "ILLUSTRATIVE, not a forecast. Change the yellow input cells; everything else recomputes. See tab 7 for the logic.", "A1:J1")

# ---- INPUTS block ----
ws.cell(4, 1, "INPUTS (adjust the yellow cells)").font = font(12, True, NAVY)
inp_hdr = 5
for c, h in zip("ABCD", ["Parameter", "Symbol", "Value", "Unit / note"]):
    cell = ws[f"{c}{inp_hdr}"]; cell.value = h
style_header(ws, inp_hdr, list("ABCD"))

inputs = [
 # label, symbol, value, unit, numfmt
 ("Baseline 2030 US AI *inference* power (all closed/hyperscaler)", "P0", 25, "GW  (assume ~50% of Epoch's >50 GW total AI load is inference)", "0"),
 ("Open-weight model energy factor vs frontier (per token)", "k", 0.75, "x  (0.75 = 25% lower; from GPT-OSS vs Qwen3 measured)", "0.00"),
 ("Deployment overhead — closed on hyperscaler (reference)", "Mc", 1.00, "x  (high batch, ~70% utilisation)", "0.00"),
 ("Deployment overhead — open on optimised cloud", "Mo", 1.15, "x  (vLLM/TensorRT, decent batch)", "0.00"),
 ("Deployment overhead — open self-hosted (low utilisation)", "Ms", 3.00, "x  (~15-25% util; up to ~10x at 10%)", "0.00"),
 ("Induced-demand (Jevons) multiplier on open workload", "J", 1.50, "x  (1.0 = none; used only in scenario C; 1.5-2.0 = cheaper access drives more use)", "0.00"),
]
irow = inp_hdr + 1
sym_row = {}
for k, (label, sym, val, unit, nf) in enumerate(inputs):
    r = irow + k
    ws.cell(r, 1, label).font = font(10); ws.cell(r, 1).alignment = left()
    ws.cell(r, 2, sym).alignment = center(); ws.cell(r, 2).font = font(10, True)
    vc = ws.cell(r, 3, val); vc.alignment = center(); vc.number_format = nf
    vc.fill = fill(INPUTF); vc.font = font(11, True); vc.border = box
    ws.cell(r, 4, unit).font = font(9, italic=True, color=GREY); ws.cell(r, 4).alignment = left()
    sym_row[sym] = r
    for col in (1,2,4):
        ws.cell(r, col).border = box
P0=f"$C${sym_row['P0']}"; K=f"$C${sym_row['k']}"; MC=f"$C${sym_row['Mc']}"
MO=f"$C${sym_row['Mo']}"; MS=f"$C${sym_row['Ms']}"; J=f"$C${sym_row['J']}"

# ---- SCENARIO MATRIX ----
mrow = irow + len(inputs) + 2
ws.cell(mrow-1, 1, "RESULT — aggregate US AI inference power (GW) by share shifted to open weights").font = font(12, True, NAVY)
ws.merge_cells(f"A{mrow-1}:J{mrow-1}")

# columns: Share shifted | A cloud GW | A %chg | B self GW | B %chg | C self+Jevons GW | C %chg
mh = ["Share shifted to open →", "A: efficient open, cloud-served", "", "B: fragmented self-hosted", "", "C: self-hosted + induced demand", ""]
for i, h in enumerate(mh):
    ws.cell(mrow, i+1, h)
style_header(ws, mrow, [get_column_letter(i+1) for i in range(7)], height=30)
sub = mrow+1
for c, t in zip("BCDEFG", ["GW","Δ%","GW","Δ%","GW","Δ%"]):
    ws[f"{c}{sub}"] = t; ws[f"{c}{sub}"].font = font(9, True, GREY); ws[f"{c}{sub}"].alignment = center()
ws["A"+str(sub)] = ""

shares = [0.0, 0.25, 0.50, 0.75, 1.0]
first_data = sub+1
for k, s in enumerate(shares):
    r = first_data + k
    sc = ws.cell(r, 1, s); sc.number_format = '0%'; sc.alignment = center(); sc.font = font(10, True)
    sc.fill = fill(INPUTF); sc.border = box
    Scell = f"$A{r}"
    # multiplier = (1-s) + s*regime_factor ; regime A: J=1,M=Mo ; B: J=1,M=Ms ; C: J,M=Ms
    # A cloud
    ws.cell(r,2).value = f"={P0}*((1-{Scell})+{Scell}*1*{K}*{MO})"
    ws.cell(r,3).value = f"={P0}*((1-{Scell})+{Scell}*1*{K}*{MO})/{P0}-1"
    # B self
    ws.cell(r,4).value = f"={P0}*((1-{Scell})+{Scell}*1*{K}*{MS})"
    ws.cell(r,5).value = f"={P0}*((1-{Scell})+{Scell}*1*{K}*{MS})/{P0}-1"
    # C self + jevons
    ws.cell(r,6).value = f"={P0}*((1-{Scell})+{Scell}*{J}*{K}*{MS})"
    ws.cell(r,7).value = f"={P0}*((1-{Scell})+{Scell}*{J}*{K}*{MS})/{P0}-1"
    for col in (2,4,6):
        ws.cell(r,col).number_format = '0.0" GW"'; ws.cell(r,col).alignment = center(); ws.cell(r,col).font = font(10, True, NAVY)
    for col in (3,5,7):
        ws.cell(r,col).number_format = '+0%;-0%'; ws.cell(r,col).alignment = center(); ws.cell(r,col).font = font(9)
    for col in range(1,8):
        ws.cell(r,col).border = box

# effective per-token comparison mini-table
er = first_data + len(shares) + 2
ws.cell(er-1, 1, "Effective energy per token by regime (relative to closed=1.00)").font = font(11, True, NAVY)
ws.merge_cells(f"A{er-1}:D{er-1}")
for c,h in zip("ABC", ["Regime","Formula","Relative energy/token"]):
    ws[f"{c}{er}"] = h
style_header(ws, er, list("ABC"))
regimes = [
 ("Closed frontier, hyperscaler", f"=Mc", f"={MC}"),
 ("Open weights, optimised cloud", f"=k*Mo", f"={K}*{MO}"),
 ("Open weights, self-hosted (low util)", f"=k*Ms", f"={K}*{MS}"),
]
for k2,(lab,frm,val) in enumerate(regimes):
    r = er+1+k2
    ws.cell(r,1,lab).font=font(10); ws.cell(r,1).alignment=left()
    ws.cell(r,2,frm.replace('=','')).font=font(10, italic=True, color=GREY); ws.cell(r,2).alignment=center()
    ws.cell(r,3).value=val; ws.cell(r,3).number_format='0.00"x"'; ws.cell(r,3).alignment=center(); ws.cell(r,3).font=font(10,True,NAVY)
    for col in range(1,4): ws.cell(r,col).border=box

takeaway = er + len(regimes) + 2
ws.cell(takeaway,1,"READ-OUT: With efficient open models served on optimised cloud (col A), a shift can slightly LOWER aggregate power. "
        "But fragmented low-utilisation self-hosting (col B) RAISES it, and induced demand (col C) raises it further. "
        "The direction is decided by DEPLOYMENT UTILISATION and INDUCED DEMAND — not by the weights being 'open'.").font = font(10, True, NAVY)
ws.merge_cells(f"A{takeaway}:J{takeaway+2}")
ws.cell(takeaway,1).alignment = left()

for col,w in zip("ABCDEFGHIJ",[30,16,8,16,8,16,8,4,4,4]):
    ws.column_dimensions[col].width = w

# chart: grouped bars of the three scenarios at each share
bar = BarChart(); bar.type="col"; bar.grouping="clustered"
bar.title = "US AI inference power by share shifted to open (GW)"
bar.height=9; bar.width=18
data = Reference(ws, min_col=2, min_row=mrow, max_row=first_data+len(shares)-1)
# pick GW columns only (B,D,F) -> build manually
for col_i, name in [(2,"A: cloud open"),(4,"B: self-hosted"),(6,"C: self+Jevons")]:
    ref = Reference(ws, min_col=col_i, min_row=first_data, max_row=first_data+len(shares)-1)
    ser = Series(ref, title=name)
    bar.series.append(ser)
cats = Reference(ws, min_col=1, min_row=first_data, max_row=first_data+len(shares)-1)
bar.set_categories(cats)
ws.add_chart(bar, "E4")

# =====================================================================
# SHEET 6: Sources
# =====================================================================
ws = wb.create_sheet("6. Sources")
ws.sheet_view.showGridLines = False
title_block(ws, "Sources", "Primary sources prioritised. 24 of 25 extracted claims verified by 3-way adversarial check.", "A1:F1")
hdr = ["#", "Publisher / title", "Date", "Quality", "Supports", "URL"]
hr = 4
for i,h in enumerate(hdr): ws.cell(hr,i+1,h)
style_header(ws, hr, [get_column_letter(i+1) for i in range(6)])
srcs = [
 ("Google Cloud — Measuring the environmental impact of AI inference", "Aug 21 2025", "Primary", "Gemini 0.24/0.10 Wh, PUE 1.09, breakdown, 33x/yr",
  "https://cloud.google.com/blog/products/infrastructure/measuring-the-environmental-impact-of-ai-inference"),
 ("arXiv 2508.15734 — Google technical paper", "Aug 21 2025", "Primary", "Gemini energy methodology & figures",
  "https://arxiv.org/abs/2508.15734"),
 ("Sam Altman — The Gentle Singularity", "Jun 2025", "Primary", "ChatGPT ~0.34 Wh avg query",
  "https://blog.samaltman.com/the-gentle-singularity"),
 ("Epoch AI — How much energy does ChatGPT use?", "Feb 2025", "Primary", "GPT-4o ~0.3 Wh estimate & FLOP model",
  "https://epoch.ai/gradient-updates/how-much-energy-does-chatgpt-use"),
 ("Epoch AI / EPRI — Power demands of frontier AI training", "Aug 11 2025", "Primary", "US 5→50 GW; 4-16 GW training runs",
  "https://epoch.ai/blog/power-demands-of-frontier-ai-training"),
 ("ML.ENERGY Benchmark (arXiv 2505.06371, NeurIPS 2025)", "May 2025", "Primary", "0.12-0.20 J/tok Llama-8B; 40 models",
  "https://arxiv.org/html/2505.06371v1"),
 ("arXiv 2508.16700 — GPT-OSS vs dense MoE energy", "Aug 2025", "Primary", "MoE -25.8% J/1k tok, +34.2% tok/W",
  "https://arxiv.org/pdf/2508.16700"),
 ("TokenPowerBench (arXiv 2512.03024, AAAI'26)", "Dec 2025", "Primary", "Engine 25-40%; 7.3x scaling",
  "https://arxiv.org/html/2512.03024v1"),
 ("ACL 2025 long paper — inference energy vs tokens", "2025", "Primary", "~1.72 J/token marginal; decode dominates",
  "https://aclanthology.org/2025.acl-long.1563.pdf"),
 ("Mixtral of Experts (arXiv 2401.04088)", "2024", "Primary", "Mixtral active-param basis (2-3x claim = estimate)",
  "https://arxiv.org/abs/2401.04088"),
 ("DataCenterDynamics — Gemini 0.24 Wh coverage", "Aug 2025", "Secondary", "Corroborates Google figures",
  "https://www.datacenterdynamics.com/en/news/google-median-gemini-prompt-uses-024-watt-hours-of-power-and-consumes-026ml-of-water/"),
 ("Hannah Ritchie — AI footprint (Aug 2025)", "Aug 2025", "Secondary", "Corroborates 0.24 & 0.34 Wh",
  "https://hannahritchie.substack.com/p/ai-footprint-august-2025"),
 ("IEA — Energy and AI: Energy demand from AI", "2025", "Reported*", "Global DC 415→945 TWh (NOT re-verified)",
  "https://www.iea.org/reports/energy-and-ai/energy-demand-from-ai"),
 ("Artificial Analysis Intelligence Index v4.1", "2026", "Not fetchable", "Tab 1 scores/costs (confirm manually)",
  "https://artificialanalysis.ai/"),
]
start = hr+1
for j,(pub,date,qual,supp,url) in enumerate(srcs):
    r = start+j
    ws.cell(r,1,j+1).alignment=center()
    ws.cell(r,2,pub).font=font(10, True); ws.cell(r,2).alignment=left()
    ws.cell(r,3,date).alignment=center()
    qc=ws.cell(r,4,qual); qc.alignment=center()
    qc.font=font(9, True, GREEN if qual=="Primary" else (GREY if qual=="Secondary" else ORANGE))
    ws.cell(r,5,supp).alignment=left()
    link_cell(ws, f"F{r}", url, "open")
    for col in range(1,7): ws.cell(r,col).border=box
for col,w in zip("ABCDEF",[4,46,12,13,40,10]):
    ws.column_dimensions[col].width=w

# =====================================================================
# SHEET 7: Assumptions & Method
# =====================================================================
ws = wb.create_sheet("7. Assumptions & Method")
ws.sheet_view.showGridLines = False
title_block(ws, "Assumptions & Method", "Everything I assumed or derived, in plain English, so you can challenge each step.", "A1:H1")
blocks = [
 ("Intelligence-per-dollar (tab 1)", [
   "= AAII v4.1 score ÷ $/task. A pure ratio; higher = more intelligence per dollar spent.",
   "AAII scores and $/task are taken verbatim from your table — I did not re-verify them.",
   "Claude Opus 4.8 entered as 54 (your table shows ~54*).",
 ]),
 ("Per-token energy anchor (tabs 3 & 5)", [
   "Google's comprehensive 0.24 Wh ÷ 500 tokens = 1.73 J/token, which lines up with the independently-",
   "measured ~1.72 J/token marginal decode cost. So 'closed frontier on hyperscaler' ≈ 1.72 J/token, full-stack.",
   "Open-weight model factor k = 0.75 comes from the measured GPT-OSS-20B vs Qwen3-32B gap (25.8% lower).",
 ]),
 ("Deployment overhead multipliers (tab 5)", [
   "Mc = 1.00  closed on hyperscaler: reference case, high batch, ~70% GPU utilisation (Epoch's assumption).",
   "Mo = 1.15  open on optimised cloud: small penalty for less-tuned serving on shared cloud GPUs.",
   "Ms = 3.00  open self-hosted: idle/low-utilisation GPUs still draw power. Analysts note ~10% utilisation can",
   "           mean ~10x real cost/energy per useful token; 3x is a central, not worst-case, figure.",
   "J = induced-demand (Jevons) multiplier: cheaper, freely-hostable models can drive MORE total queries.",
 ]),
 ("Scenario formula (tab 5)", [
   "Aggregate power = P0 × [ (1 − s) + s × J × k × M ]",
   "  P0 = baseline 2030 US AI inference power, all-closed  (default 25 GW = ~half of Epoch's >50 GW total AI load).",
   "  s  = share of inference shifted to open weights (0–100%).",
   "  k  = open-model per-token energy factor (0.75).",
   "  M  = deployment overhead for the open share (Mo cloud, or Ms self-hosted).",
   "  J  = induced-demand multiplier (1.0 = none).",
   "The (1−s) term stays at closed/hyperscaler efficiency; only the shifted share s carries the open regime.",
 ]),
 ("What this model deliberately ignores", [
   "Training energy (all per-query figures exclude it).",
   "Quality differences — open vs closed models may need more/fewer tokens for the same task.",
   "Network/storage energy, embodied carbon, water.",
   "Region/grid carbon intensity (this is a POWER model, not an emissions model).",
   "It is illustrative: the robust output is the DIRECTION and the sensitivity, not a precise GW forecast.",
 ]),
 ("Unit conversions", [
   "1 Wh = 3600 J.   1 TWh = 3.6e15 J.   1 TWh/yr = 0.11416 GW continuous (1e12 Wh / 8760 h / 1e9).",
 ]),
]
r = 3
for head, lines in blocks:
    ws.cell(r,1,head).font = font(12, True, NAVY); ws.merge_cells(f"A{r}:H{r}")
    ws.row_dimensions[r].height = 18; r += 1
    for ln in lines:
        ws.cell(r,1,ln).font = font(10); ws.cell(r,1).alignment = left()
        ws.merge_cells(f"A{r}:H{r}"); r += 1
    r += 1
ws.column_dimensions["A"].width = 120

# =====================================================================
# SHEET 8: Chart visuals (embedded presentation-ready PNGs, if present)
# =====================================================================
import os
from openpyxl.drawing.image import Image as XLImage
imgdir = "/home/user/Cement/build/img"
if os.path.isdir(imgdir):
    ws = wb.create_sheet("8. Chart visuals")
    ws.sheet_view.showGridLines = False
    title_block(ws, "Chart visuals (presentation-ready)",
                "The same figures used in the slide deck. The interactive/editable versions live on tabs 1, 2 and 5.", "A1:J1")
    anchors = [("scatter.png","A4"),("intper.png","A34"),("energy.png","A64"),("scenario.png","A94")]
    for fn, anc in anchors:
        p = os.path.join(imgdir, fn)
        if os.path.exists(p):
            im = XLImage(p); im.width = im.width*0.62; im.height = im.height*0.62
            ws.add_image(im, anc)

wb.save("/home/user/Cement/AI_Cost_Intelligence_Energy_DataPack.xlsx")
print("SAVED workbook: AI_Cost_Intelligence_Energy_DataPack.xlsx")
print("sheets:", wb.sheetnames)
