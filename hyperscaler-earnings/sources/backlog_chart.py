# Generate a theme-aware inline-SVG line chart of 5-year backlog history.
years=[2021,2022,2023,2024,2025,2026]
series=[
 ("Amazon","#FF9900",[80,110,156,172,244,496]),
 ("Microsoft","#0078D4",[147,189,222,298,625,678]),
 ("Alphabet","#4285F4",[51,64,74,93,243,514]),
 ("Oracle","#C74634",[37,61,65,97,523,638]),
]
W,H=780,440
ml,mr,mt,mb=54,120,28,44
pw,ph=W-ml-mr,H-mt-mb
ymax=700
def x(i): return ml+pw*i/(len(years)-1)
def y(v): return mt+ph*(1-v/ymax)
grid=""
for gv in range(0,701,100):
    yy=y(gv)
    grid+=f'<line x1="{ml}" y1="{yy:.1f}" x2="{ml+pw}" y2="{yy:.1f}" class="grid"/>'
    grid+=f'<text x="{ml-8}" y="{yy+3.5:.1f}" class="ytick">${gv}</text>'
xticks=""
for i,yr in enumerate(years):
    lbl=str(yr) if yr!=2026 else "Jun\u201926"
    xticks+=f'<text x="{x(i):.1f}" y="{H-mb+20}" class="xtick">{lbl}</text>'
paths=""
for name,col,vals in series:
    pts=" ".join(f"{x(i):.1f},{y(v):.1f}" for i,v in enumerate(vals))
    paths+=f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2.6" stroke-linjoin="round" stroke-linecap="round"/>'
    for i,v in enumerate(vals):
        paths+=f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="3" fill="{col}"/>'
    ex,ey=x(len(vals)-1),y(vals[-1])
    paths+=f'<text x="{ex+8:.1f}" y="{ey+3.5:.1f}" class="lbl" fill="{col}">{name} ${vals[-1]}B</text>'
svg=f'''<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Backlog history 2021-2026">
{grid}{xticks}{paths}</svg>'''

html=f'''<title>Hyperscaler Backlog — 5-Year History</title>
<style>
 :root{{--bg:#fff;--ink:#33383d;--strong:#1c2024;--muted:#8c949b;--grid:#ECEEF0;--rule:#E6EAED}}
 @media (prefers-color-scheme:dark){{:root{{--bg:#0f1416;--ink:#c6cdd2;--strong:#eef2f4;--muted:#828b92;--grid:#20272b;--rule:#232b2f}}}}
 :root[data-theme=light]{{--bg:#fff;--ink:#33383d;--strong:#1c2024;--muted:#8c949b;--grid:#ECEEF0;--rule:#E6EAED}}
 :root[data-theme=dark]{{--bg:#0f1416;--ink:#c6cdd2;--strong:#eef2f4;--muted:#828b92;--grid:#20272b;--rule:#232b2f}}
 *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font-family:ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}}
 .wrap{{max-width:860px;margin:0 auto;padding:52px 26px 72px}}
 .eyebrow{{font-size:11.5px;letter-spacing:.15em;text-transform:uppercase;color:#0E7C6F;font-weight:640;margin:0 0 9px}}
 h1{{font-size:clamp(21px,3.3vw,27px);line-height:1.2;letter-spacing:-.015em;color:var(--strong);font-weight:680;margin:0 0 8px;text-wrap:balance}}
 .sub{{font-size:14.5px;color:var(--muted);margin:0 0 18px;max-width:64ch}}
 .card{{border:1px solid var(--rule);border-radius:14px;padding:14px 12px 6px;background:var(--bg)}}
 .grid{{stroke:var(--grid);stroke-width:1}}
 .ytick{{fill:var(--muted);font-size:10.5px;text-anchor:end;font-variant-numeric:tabular-nums}}
 .xtick{{fill:var(--muted);font-size:11px;text-anchor:middle;font-variant-numeric:tabular-nums}}
 .lbl{{font-size:11.5px;font-weight:680}}
 .note{{font-size:12.5px;color:var(--muted);margin-top:14px;line-height:1.6}}
 .note b{{color:var(--ink)}}
</style>
<div class="wrap">
 <p class="eyebrow">Hyperscaler earnings · contracted backlog</p>
 <h1>Backlog / RPO, 5-year history — the AI inflection</h1>
 <p class="sub">Contracted backlog (remaining performance obligations), $ billions, normalized to <b>Dec year-end</b> (2021&#8211;2025) and <b>Jun 2026</b>. Metrics differ (Amazon &amp; Oracle company-wide RPO, Microsoft all-commercial, Alphabet cloud/total) — read as trajectories, not a strict apples-to-apples level.</p>
 <div class="card">{svg}</div>
 <p class="note"><b>Calendar-normalized:</b> Dec 31 for 2021&#8211;2025, Jun 2026 for the last point. Microsoft shown at its Dec-31 (fiscal Q2) reading; Oracle has no Dec/Jun close, so it&#8217;s Nov-30 for year-ends and May-31 for 2026. <b>Meta</b> is omitted — it discloses no cloud backlog. After years of ~15&#8211;45%/yr compounding, all four inflect sharply in 2025&#8211;2026 on multi-year AI-compute contracts (Oracle ~4.6&#215; in one year; Amazon +$132B in a single quarter). Backlog leads revenue and capex — a demand signal, not booked revenue.</p>
</div>'''
open("/home/user/Cement/hyperscaler-earnings/backlog-history-chart.html","w").write(html)
print("wrote chart", len(html), "bytes")
