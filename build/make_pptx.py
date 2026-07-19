#!/usr/bin/env python3
"""Assemble the slide deck from rendered charts."""
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
import os

IMG="/home/user/Cement/build/img"
NAVY=RGBColor(0x10,0x42,0x81); INK=RGBColor(0x0b,0x0b,0x0b); SEC=RGBColor(0x52,0x51,0x4e)
BLUE=RGBColor(0x2a,0x78,0xd6); GREEN=RGBColor(0x00,0x83,0x00); ORANGE=RGBColor(0xeb,0x68,0x34)
RED=RGBColor(0xe3,0x49,0x48); MUT=RGBColor(0x89,0x87,0x81); WHITE=RGBColor(0xff,0xff,0xff)
LGREY=RGBColor(0xf5,0xf5,0xf3)

prs = Presentation()
prs.slide_width = Inches(13.333); prs.slide_height = Inches(7.5)
BLANK = prs.slide_layouts[6]
SW, SH = prs.slide_width, prs.slide_height

def add_slide():
    return prs.slides.add_slide(BLANK)

def rect(slide, l,t,w,h, color, line=None):
    sp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, l,t,w,h)
    sp.fill.solid(); sp.fill.fore_color.rgb = color
    if line is None: sp.line.fill.background()
    else: sp.line.color.rgb=line
    sp.shadow.inherit=False
    return sp

def txt(slide, l,t,w,h, text, size=18, color=INK, bold=False, align=PP_ALIGN.LEFT,
        anchor=MSO_ANCHOR.TOP, italic=False, font="Calibri", line_spacing=1.0):
    tb = slide.shapes.add_textbox(l,t,w,h); tf=tb.text_frame; tf.word_wrap=True
    tf.vertical_anchor=anchor
    tf.margin_left=0; tf.margin_right=0; tf.margin_top=0; tf.margin_bottom=0
    lines = text.split("\n")
    for i,ln in enumerate(lines):
        p = tf.paragraphs[0] if i==0 else tf.add_paragraph()
        p.alignment=align; p.line_spacing=line_spacing
        r=p.add_run(); r.text=ln
        r.font.size=Pt(size); r.font.bold=bold; r.font.italic=italic
        r.font.color.rgb=color; r.font.name=font
    return tb

def header(slide, kicker, title):
    rect(slide, 0,0, SW, Inches(0.14), NAVY)          # top accent bar
    txt(slide, Inches(0.6), Inches(0.34), Inches(12), Inches(0.3), kicker.upper(),
        size=12, color=BLUE, bold=True)
    txt(slide, Inches(0.6), Inches(0.62), Inches(12.1), Inches(0.75), title,
        size=25, color=NAVY, bold=True, line_spacing=0.98)

def footer(slide, text):
    rect(slide, 0, SH-Inches(0.36), SW, Inches(0.36), LGREY)
    txt(slide, Inches(0.6), SH-Inches(0.335), Inches(12.1), Inches(0.3), text,
        size=8.5, color=MUT, italic=True, anchor=MSO_ANCHOR.MIDDLE)

def pic(slide, path, l,t,w):
    return slide.shapes.add_picture(path, l,t, width=w)

def bullets(slide, l,t,w,h, items, size=14, gap=6):
    tb=slide.shapes.add_textbox(l,t,w,h); tf=tb.text_frame; tf.word_wrap=True
    for i,(lead,rest,col) in enumerate(items):
        p=tf.paragraphs[0] if i==0 else tf.add_paragraph()
        p.space_after=Pt(gap); p.line_spacing=1.05
        r=p.add_run(); r.text="▍ "; r.font.color.rgb=col; r.font.size=Pt(size); r.font.bold=True
        r2=p.add_run(); r2.text=lead+" "; r2.font.bold=True; r2.font.size=Pt(size); r2.font.color.rgb=INK
        if rest:
            r3=p.add_run(); r3.text=rest; r3.font.size=Pt(size); r3.font.color.rgb=SEC
    return tb

# ---------------- Slide 1: Title ----------------
s=add_slide()
rect(s,0,0,SW,SH,WHITE)
rect(s,0,0,Inches(0.28),SH,NAVY)
rect(s, Inches(0.6), Inches(2.55), Inches(2.4), Inches(0.08), BLUE)
txt(s, Inches(0.6), Inches(1.5), Inches(12), Inches(1.0),
    "The Cost, Intelligence & Energy of AI Models", size=40, color=NAVY, bold=True, line_spacing=0.98)
txt(s, Inches(0.62), Inches(2.75), Inches(11.5), Inches(1.2),
    "Token cost vs. intelligence — and what a shift to open-weight models could mean for data-center capacity",
    size=18, color=SEC)
# three stat chips
chips=[("~0.24–0.34 Wh","per AI query today (~10× below old estimates)",BLUE),
       ("−25.8% J/token","open MoE (GPT-OSS) vs dense (Qwen3) — measured",GREEN),
       ("5 → >50 GW","US AI power, today → 2030 (Epoch/EPRI)",ORANGE)]
cx=Inches(0.6); cw=Inches(3.95); gap=Inches(0.19); cy=Inches(4.35)
for i,(big,small,col) in enumerate(chips):
    x=Emu(int(cx)+i*(int(cw)+int(gap)))
    card=rect(s, x, cy, cw, Inches(1.65), LGREY)
    rect(s, x, cy, Inches(0.08), Inches(1.65), col)
    txt(s, Emu(int(x)+Inches(0.28)), Emu(int(cy)+Inches(0.22)), Emu(int(cw)-Inches(0.4)), Inches(0.6),
        big, size=23, color=col, bold=True)
    txt(s, Emu(int(x)+Inches(0.28)), Emu(int(cy)+Inches(0.92)), Emu(int(cw)-Inches(0.45)), Inches(0.7),
        small, size=11.5, color=SEC, line_spacing=1.0)
txt(s, Inches(0.6), SH-Inches(0.7), Inches(12), Inches(0.4),
    "Prepared 19 Jul 2026  ·  Intelligence data: Artificial Analysis v4.1 (your table)  ·  Energy/capacity data: primary sources, verified", size=10, color=MUT, italic=True)

# ---------------- Slide 2: Intelligence vs Cost ----------------
s=add_slide()
header(s, "Cost vs Intelligence", "Frontier intelligence is converging — and open weights are inside the pack")
pic(s, f"{IMG}/scatter.png", Inches(0.5), Inches(1.55), Inches(8.5))
bullets(s, Inches(9.25), Inches(1.75), Inches(3.7), Inches(4.5), [
  ("Top 8 models span just 44–60 on the index —","the intelligence gap is narrowing.",BLUE),
  ("Kimi K3 (open, 2.8T)","scores 57 at $0.94/task — matching closed frontier models.",GREEN),
  ("Cost varies ~13×","across similar-intelligence models ($0.21 → $2.75).",ORANGE),
  ("Cheapest capable tier","(Luna, Grok, Terra) clusters under $0.55/task.",SEC),
])
footer(s, "Source: Artificial Analysis Intelligence Index v4.1 (your table). Not independently re-verified in this pass — confirm at artificialanalysis.ai. $/task = AA blended cost metric.")

# ---------------- Slide 3: Intelligence per $ ----------------
s=add_slide()
header(s, "Cost-efficiency", "Intelligence per dollar: cheap closed models and open weights lead")
pic(s, f"{IMG}/intper.png", Inches(0.5), Inches(1.55), Inches(8.5))
bullets(s, Inches(9.25), Inches(1.75), Inches(3.7), Inches(4.5), [
  ("Intelligence-per-$ = AAII ÷ $/task.","A pure efficiency ratio.",BLUE),
  ("GPT-5.6 Luna & Grok 4.5","deliver the most intelligence per dollar of the closed models.",ORANGE),
  ("Kimi K2.6 (open)","is highly cost-efficient — open weights compete hard on price.",GREEN),
  ("Premium models","(Fable 5, Opus 4.8) trade $/task for top-end capability.",SEC),
])
footer(s, "Derived live in the workbook (tab 1). Ratio uses your AAII v4.1 scores and $/task figures verbatim.")

# ---------------- Slide 4: Energy per query ----------------
s=add_slide()
header(s, "Electricity per query", "A single query now costs ~0.24–0.34 Wh — about 10× less than the old myth")
pic(s, f"{IMG}/energy.png", Inches(0.5), Inches(1.55), Inches(8.4))
bullets(s, Inches(9.15), Inches(1.75), Inches(3.8), Inches(4.6), [
  ("Google Gemini: 0.24 Wh","full-stack (measured); 0.10 Wh chip-only.",BLUE),
  ("OpenAI: 0.34 Wh","average query (Altman, estimate).",ORANGE),
  ("Google cut this 33×","in a single year (May '24 → May '25).",GREEN),
  ("Per token: ~1.7 J","measured; decode dominates. 70× params → only 7.3× energy.",SEC),
  ("Excludes training energy","and reasoning/long queries (which run higher).",MUT),
])
footer(s, "Sources: Google Cloud & arXiv:2508.15734 (Aug 2025); Altman 'Gentle Singularity' (Jun 2025); Epoch AI (Feb 2025); ML.ENERGY / TokenPowerBench. Blue = measured, orange = estimate.")

# ---------------- Slide 5: Open-shift scenario ----------------
s=add_slide()
header(s, "Open-source shift → DC capacity", "The data-center impact depends on HOW open models are served")
pic(s, f"{IMG}/scenario.png", Inches(0.5), Inches(1.55), Inches(8.5))
bullets(s, Inches(9.25), Inches(1.7), Inches(3.75), Inches(4.7), [
  ("Open weights ≠ automatically greener.","The deployment decides.",NAVY),
  ("A — cloud-served, efficient:","−14% power. MoE sparsity wins at high utilisation.",GREEN),
  ("B — fragmented self-hosting:","+125%. Idle low-utilisation GPUs still draw power.",ORANGE),
  ("C — self-hosted + induced demand:","+238%. Cheaper access drives more usage (Jevons).",RED),
  ("Levers that decide direction:","utilisation & induced demand — not the license.",SEC),
])
footer(s, "Illustrative parametric model (workbook tab 5): P0=25 GW, k=0.75, M=1.15/3.0, J=1.5. Baseline anchored to Epoch/EPRI >50 GW US AI by 2030. Change the inputs to test your own assumptions.")

# ---------------- Slide 6: Sources & caveats ----------------
s=add_slide()
header(s, "Sources & method", "What's measured, what's estimated, what to double-check")
# two columns
col1=[("VERIFIED — primary sources","",NAVY),
 ("Google Cloud + arXiv 2508.15734","Gemini 0.24 Wh, PUE 1.09, 33×/yr (Aug 2025)",BLUE),
 ("Epoch AI","GPT-4o ~0.3 Wh; US 5→50 GW by 2030",BLUE),
 ("ML.ENERGY (NeurIPS'25)","0.12–0.20 J/token, 40 models — measured",GREEN),
 ("arXiv 2508.16700","GPT-OSS MoE −25.8% J/1k tok — measured",GREEN),
 ("TokenPowerBench (AAAI'26)","engine 25–40%; 7.3× scaling — measured",GREEN),
]
col2=[("TREAT WITH CARE","",NAVY),
 ("AA Intelligence Index v4.1","not machine-verifiable — confirm on the live site",ORANGE),
 ("IEA 415→945 TWh global DC","fetch failed this pass — confirm at iea.org",ORANGE),
 ("Vendor self-reported Wh","Google/OpenAI; exclude training energy",ORANGE),
 ("MoE energy edge","measured single-GPU; may shrink at hyperscaler batch",ORANGE),
 ("Scenario model","illustrative, not a forecast — shows the levers",ORANGE),
]
bullets(s, Inches(0.6), Inches(1.7), Inches(6.0), Inches(5), col1, size=13, gap=7)
bullets(s, Inches(6.9), Inches(1.7), Inches(6.0), Inches(5), col2, size=13, gap=7)
footer(s, "Full source list, URLs and dates in the workbook (tab 6). 24 of 25 extracted claims passed 3-way adversarial verification.")

out="/home/user/Cement/AI_Cost_Intelligence_Energy_Slides.pptx"
prs.save(out)
print("SAVED deck:", out, "| slides:", len(prs.slides._sldIdLst))
