#!/usr/bin/env python3
"""Render presentation-quality PNG charts using the validated dataviz palette."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np

# palette
BLUE="#2a78d6"; GREEN="#008300"; ORANGE="#eb6834"; RED="#e34948"
NAVY="#104281"; INK="#0b0b0b"; SEC="#52514e"; MUT="#898781"; GRID="#e1e0d9"
SURF="#ffffff"
plt.rcParams.update({
    "font.family":"DejaVu Sans","font.size":12,"text.color":INK,
    "axes.edgecolor":"#c3c2b7","axes.labelcolor":SEC,"xtick.color":MUT,
    "ytick.color":MUT,"axes.linewidth":0.8,"figure.dpi":200,"savefig.dpi":200,
})
OUT="/home/user/Cement/build/img"
import os; os.makedirs(OUT, exist_ok=True)

# ---------- Chart 1: Intelligence vs Cost scatter ----------
models = [
 ("Claude Fable 5", 60, 2.75, "closed"),
 ("GPT-5.6 Sol", 59, 1.04, "closed"),
 ("Kimi K3", 57, 0.94, "open"),
 ("GPT-5.6 Terra", 55, 0.55, "closed"),
 ("Grok 4.5", 54, 0.31, "closed"),
 ("Claude Opus 4.8", 54, 1.80, "closed"),
 ("GPT-5.6 Luna", 51, 0.21, "closed"),
 ("Kimi K2.6", 44, 0.33, "open"),
]
fig, ax = plt.subplots(figsize=(10.5,5.9))
fig.patch.set_facecolor(SURF); ax.set_facecolor(SURF)
for name,aaii,cost,typ in models:
    col = GREEN if typ=="open" else BLUE
    ax.scatter(cost, aaii, s=170, color=col, zorder=3, edgecolor="white", linewidth=1.5)
# labels with simple offset logic
offsets = {
 "Claude Fable 5":(8,4),"GPT-5.6 Sol":(8,4),"Kimi K3":(8,-2),"GPT-5.6 Terra":(8,4),
 "Grok 4.5":(-6,-16),"Claude Opus 4.8":(8,4),"GPT-5.6 Luna":(8,4),"Kimi K2.6":(8,4),
}
for name,aaii,cost,typ in models:
    dx,dy=offsets[name]
    ax.annotate(name,(cost,aaii),textcoords="offset points",xytext=(dx,dy),
                fontsize=10.5, color=INK, fontweight="bold" if typ=="open" else "normal")
ax.set_xlabel("←  cheaper        Cost per task (US$)        pricier  →", fontsize=12, color=SEC)
ax.set_ylabel("Intelligence  (AAII v4.1)", fontsize=12, color=SEC)
ax.set_xlim(0, 3.1); ax.set_ylim(40, 63)
ax.grid(True, color=GRID, linewidth=0.7, zorder=0)
ax.set_axisbelow(True)
for spine in ["top","right"]: ax.spines[spine].set_visible(False)
# legend
from matplotlib.lines import Line2D
leg = [Line2D([0],[0],marker='o',color='w',markerfacecolor=BLUE,markersize=12,label='Closed frontier'),
       Line2D([0],[0],marker='o',color='w',markerfacecolor=GREEN,markersize=12,label='Open weights')]
ax.legend(handles=leg, loc="lower right", frameon=False, fontsize=11)
ax.set_title("Frontier intelligence is converging — open weights (Kimi) now sit inside the pack",
             fontsize=13.5, color=NAVY, fontweight="bold", pad=12, loc="left")
plt.tight_layout()
plt.savefig(f"{OUT}/scatter.png", facecolor=SURF, bbox_inches="tight"); plt.close()

# ---------- Chart 1b: Intelligence per $ bar ----------
fig, ax = plt.subplots(figsize=(10.5,5.9))
fig.patch.set_facecolor(SURF); ax.set_facecolor(SURF)
ipd = sorted([(n, a/c, t) for n,a,c,t in models], key=lambda x:x[1])
names=[x[0] for x in ipd]; vals=[x[1] for x in ipd]; cols=[GREEN if x[2]=="open" else BLUE for x in ipd]
bars=ax.barh(names, vals, color=cols, zorder=3, height=0.68)
for b,v in zip(bars,vals):
    ax.text(v+3, b.get_y()+b.get_height()/2, f"{v:.0f}", va="center", fontsize=11, color=INK, fontweight="bold")
ax.set_xlabel("Intelligence per dollar  (AAII ÷ $/task)  —  higher is more cost-efficient", fontsize=11.5, color=SEC)
ax.set_xlim(0, max(vals)*1.15)
ax.grid(True, axis="x", color=GRID, linewidth=0.7, zorder=0); ax.set_axisbelow(True)
for spine in ["top","right","left"]: ax.spines[spine].set_visible(False)
ax.legend(handles=leg, loc="lower right", frameon=False, fontsize=11)
ax.set_title("Cost-efficiency frontier: cheap models (Luna, Grok) and open weights (Kimi) lead on intelligence-per-$",
             fontsize=12.5, color=NAVY, fontweight="bold", pad=12, loc="left")
plt.tight_layout(); plt.savefig(f"{OUT}/intper.png", facecolor=SURF, bbox_inches="tight"); plt.close()

# ---------- Chart 2: Energy per query ----------
fig, ax = plt.subplots(figsize=(10.5,5.4))
fig.patch.set_facecolor(SURF); ax.set_facecolor(SURF)
eq = [
 ("Google Gemini\n(chip-only)", 0.10, "measured"),
 ("Google Gemini\n(full-stack)", 0.24, "measured"),
 ("Epoch AI\nGPT-4o", 0.30, "estimate"),
 ("OpenAI ChatGPT\n(Altman)", 0.34, "estimate"),
 ("Old 2023-24\nestimate", 3.00, "estimate"),
]
names=[x[0] for x in eq]; vals=[x[1] for x in eq]
cols=[BLUE if x[2]=="measured" else ORANGE for x in eq]
bars=ax.bar(names, vals, color=cols, zorder=3, width=0.6)
for b,v in zip(bars,vals):
    ax.text(b.get_x()+b.get_width()/2, v+0.05, f"{v:.2f} Wh", ha="center", fontsize=11, color=INK, fontweight="bold")
ax.set_ylabel("Energy per query (Wh)", fontsize=12, color=SEC)
ax.set_ylim(0, 3.4)
ax.grid(True, axis="y", color=GRID, linewidth=0.7, zorder=0); ax.set_axisbelow(True)
for spine in ["top","right"]: ax.spines[spine].set_visible(False)
leg2=[Line2D([0],[0],marker='s',color='w',markerfacecolor=BLUE,markersize=12,label='Measured'),
      Line2D([0],[0],marker='s',color='w',markerfacecolor=ORANGE,markersize=12,label='Estimate')]
ax.legend(handles=leg2, loc="upper left", frameon=False, fontsize=11)
ax.set_title("Per-query energy is ~10× below the old myth — today's assistants use ~0.24–0.34 Wh",
             fontsize=13, color=NAVY, fontweight="bold", pad=12, loc="left")
plt.tight_layout(); plt.savefig(f"{OUT}/energy.png", facecolor=SURF, bbox_inches="tight"); plt.close()

# ---------- Chart 3: Open-shift scenario ----------
P0,k,Mc,Mo,Ms,J = 25,0.75,1.0,1.15,3.0,1.5
shares=np.array([0,0.25,0.5,0.75,1.0])
A=P0*((1-shares)+shares*1*k*Mo)
B=P0*((1-shares)+shares*1*k*Ms)
C=P0*((1-shares)+shares*J*k*Ms)
fig, ax = plt.subplots(figsize=(10.5,5.7))
fig.patch.set_facecolor(SURF); ax.set_facecolor(SURF)
x=shares*100
ax.axhline(P0, color=MUT, linestyle="--", linewidth=1, zorder=1)
ax.text(2, P0-3.4, "baseline 25 GW (all closed)", fontsize=9.5, color=MUT)
ax.plot(x, A, "-o", color=GREEN, linewidth=2.5, markersize=7, label="A  Efficient open, cloud-served", zorder=3)
ax.plot(x, B, "-o", color=ORANGE, linewidth=2.5, markersize=7, label="B  Fragmented self-hosted", zorder=3)
ax.plot(x, C, "-o", color=RED, linewidth=2.5, markersize=7, label="C  Self-hosted + induced demand", zorder=3)
for arr,col in [(A,GREEN),(B,ORANGE),(C,RED)]:
    ax.text(101, arr[-1], f"{arr[-1]:.0f} GW", color=col, fontsize=11, fontweight="bold", va="center")
ax.set_xlabel("Share of inference shifted to open-weight models (%)", fontsize=12, color=SEC)
ax.set_ylabel("US AI inference power (GW)", fontsize=12, color=SEC)
ax.set_xlim(-2, 112); ax.set_ylim(0, 90)
ax.grid(True, color=GRID, linewidth=0.7, zorder=0); ax.set_axisbelow(True)
for spine in ["top","right"]: ax.spines[spine].set_visible(False)
ax.legend(loc="upper left", frameon=False, fontsize=11)
ax.set_title("Direction depends on HOW open models are served — not on the weights being open",
             fontsize=13, color=NAVY, fontweight="bold", pad=12, loc="left")
plt.tight_layout(); plt.savefig(f"{OUT}/scenario.png", facecolor=SURF, bbox_inches="tight"); plt.close()

print("charts written to", OUT)
import os
for f in sorted(os.listdir(OUT)): print(" ", f)
