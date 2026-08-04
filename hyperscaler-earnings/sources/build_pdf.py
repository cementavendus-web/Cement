from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame, Paragraph,
                                Spacer, Table, TableStyle, KeepTogether)
from reportlab.lib.enums import TA_LEFT

OUT="/home/user/Cement/hyperscaler-earnings/Hyperscaler_Earnings_Key_Points.pdf"

# highlight helpers
def hl(t):  return f'<font backColor="#CFE1DD">{t}</font>'   # capex teal
def hd(t):  return f'<font backColor="#FBD5A8">{t}</font>'   # demand amber
def hb(t):  return f'<font backColor="#DCD6F2">{t}</font>'   # backlog violet
def who(t): return f'<font color="#8C949B" size=8.5>  — {t}</font>'
def rep():  return '<font color="#AEB5BB" size=8>  [reported]</font>'
def aside(t): return f'<font color="#AEB5BB" size=8.5>{t}</font>'

styles=getSampleStyleSheet()
body=ParagraphStyle('body', parent=styles['Normal'], fontName='Helvetica',
                    fontSize=9.6, leading=13.6, textColor=colors.HexColor('#2A2F33'))
h1=ParagraphStyle('h1', parent=styles['Normal'], fontName='Helvetica-Bold',
                  fontSize=19, leading=22, textColor=colors.HexColor('#1C2024'))
sub=ParagraphStyle('sub', parent=styles['Normal'], fontName='Helvetica',
                   fontSize=9.6, leading=13.5, textColor=colors.HexColor('#6b7278'))
eyebrow=ParagraphStyle('eb', parent=styles['Normal'], fontName='Helvetica-Bold',
                       fontSize=8, leading=11, textColor=colors.HexColor('#0E7C6F'))
coname=ParagraphStyle('co', parent=styles['Normal'], fontName='Helvetica-Bold',
                      fontSize=12.5, leading=14, textColor=colors.white)
cotag=ParagraphStyle('cotag', parent=styles['Normal'], fontName='Helvetica',
                     fontSize=8.5, leading=14, textColor=colors.HexColor('#EAF0F0'), alignment=2)
lbl=ParagraphStyle('lbl', parent=styles['Normal'], fontName='Helvetica-Bold',
                   fontSize=7.2, leading=9, textColor=colors.HexColor('#5b6167'), alignment=TA_LEFT)
foot=ParagraphStyle('foot', parent=styles['Normal'], fontName='Helvetica',
                    fontSize=7.8, leading=11, textColor=colors.HexColor('#8C949B'))

companies=[
 ("Amazon","#FF9900","AWS  ·  Q2 2026",[
   ("Capex · now", f'"...{hl("approximately $220 billion in cash CapEx in 2026")}, the higher cost of memory pushing this up from our prior estimate of about $200 billion..."{who("Andy Jassy, CEO · Jul 30")}'),
   ("Capex · prev", f'"...invest {hl("about $200 billion")} in capital expenditures across Amazon in 2026..."{who("Andy Jassy, CEO · Q4&#8217;25, Feb 5")}{rep()}'),
   ("Demand", f'"...backlog stands at {hd("$496 billion")}... {hd("still not have enough capacity to meet all the demand")}... the demand we already have for 2028 is striking."{who("Andy Jassy, CEO")}'),
   ("Backlog", f'Q2&#8217;25 ~$195B -&gt; Q1&#8217;26 $364B -&gt; {hb("Q2&#8217;26 $496B")}   {aside("AWS backlog · +154% YoY · +$132B QoQ")}'),
 ]),
 ("Microsoft","#0078D4","Azure  ·  FY26 Q4",[
   ("Capex · now", f'"...the shift from finance to operating leases adjusts our expectation to {hl("approximately $175 billion")}." {aside("(accounting change, not a cut)")}{who("Amy Hood, CFO · Jul 29")}'),
   ("Capex · prev", f'"For calendar year 2026... {hl("roughly $190 billion")}... includes ~$25B from higher component pricing."{who("Amy Hood, CFO · FY26 Q3, Apr 29")}{rep()}'),
   ("Demand", f'"...RPO grew 84% to {hd("$678 billion")}... {hd("customer demand continues to exceed supply")}."{who("Amy Hood, CFO")}'),
   ("Backlog", f'Q4 FY25 ~$368B -&gt; Q3 FY26 $627B -&gt; {hb("Q4 FY26 $678B")}   {aside("commercial RPO · +84% YoY · +$51B QoQ · year-ago derived")}'),
 ]),
 ("Alphabet","#4285F4","Google Cloud  ·  Q2 2026",[
   ("Capex · now", f'"...full year 2026 CapEx guidance range to {hl("$195 billion to $205 billion")}, up from our previous estimate of $180 billion to $190 billion."{who("Anat Ashkenazi, CFO · Jul 22")}'),
   ("Capex · prev", f'"...{hl("$180 billion to $190 billion")}, up from... $175 to $185 billion... to include... Intersect."{who("Anat Ashkenazi, CFO · Q1&#8217;26, Apr 29")}{rep()}'),
   ("Demand", f'"...{hd("strong demand for AI infrastructure")}... cloud backlog grew to {hd("$514 billion")}."{who("Sundar Pichai, CEO")}'),
   ("Backlog", f'Q2&#8217;25 $106B -&gt; Q1&#8217;26 $462B -&gt; {hb("Q2&#8217;26 $514B")}   {aside("Google Cloud backlog · +385% YoY · +$52B QoQ")}'),
 ]),
 ("Meta","#0866FF","AI infrastructure  ·  Q2 2026",[
   ("Capex · now", f'"...{hl("$130 billion to $145 billion")}, narrowed from our prior outlook of $125 billion to $145 billion."{who("Susan Li, CFO · Jul 29")}'),
   ("Capex · prev", f'"...{hl("$125 billion to $145 billion")}, increased from our prior range of $120 to $135 billion."{who("Susan Li, CFO · Q1&#8217;26, Apr 29")}{rep()}'),
   ("Demand", f'"...we are... {hd("demand-constrained")}... numerous ROI-positive places that we would put Compute toward if we had it."{who("Susan Li, CFO")}'),
   ("Backlog", f'{aside("n/a — Meta discloses no cloud backlog / RPO (no public cloud); reports capex &amp; total expenses instead.")}'),
 ]),
 ("Oracle","#C74634","OCI  ·  FY26 Q4",[
   ("Capex · now", f'"...fiscal year 2027 with an expected net cash outlay for capital expenditures of {hl("around $70 billion")}..." {aside("(reported capex ~$20-25B higher)")}{who("Hilary Maxson, CFO · Jun 10")}'),
   ("Capex · prev", f'FY2026 capex of {hl("~$50 billion")} — an unchanged guide at the time.{who("Q3 FY26 guidance · Mar 10")}{rep()}'),
   ("Demand", f'"...$67 billion in AI infrastructure contracts... delivered more than {hd("1.2 gigawatts")}... FY&#8217;27 Q1 approaching 1 gigawatt."{who("Clay Magouyrk, CEO")}'),
   ("Backlog", f'Q4 FY25 ~$139B -&gt; Q3 FY26 $553B -&gt; {hb("Q4 FY26 $638B")}   {aside("total RPO · +363% YoY · +$85B QoQ · year-ago derived")}'),
 ]),
]

PAGE_W, PAGE_H = A4
LM=RM=17*mm; TM=16*mm; BM=15*mm
frame=Frame(LM, BM, PAGE_W-LM-RM, PAGE_H-TM-BM, id='f', showBoundary=0)

def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont('Helvetica', 7.2)
    canvas.setFillColor(colors.HexColor('#AEB5BB'))
    canvas.drawString(LM, 9*mm, "Verbatim from official earnings-call transcripts (previous-capex figures from reporting; year-ago backlog for MSFT/ORCL derived).")
    canvas.drawRightString(PAGE_W-RM, 9*mm, f"{doc.page}")
    canvas.restoreState()

doc=BaseDocTemplate(OUT, pagesize=A4, leftMargin=LM, rightMargin=RM, topMargin=TM, bottomMargin=BM,
                    title="Hyperscaler Earnings - Key Points", author="Cement")
doc.addPageTemplates([PageTemplate(id='main', frames=[frame], onPage=footer)])

CW=PAGE_W-LM-RM
story=[]
story.append(Paragraph("HYPERSCALER EARNINGS · LATEST CALLS", eyebrow))
story.append(Spacer(1,4))
story.append(Paragraph("Key points by company — capex &amp; data-center demand", h1))
story.append(Spacer(1,5))
story.append(Paragraph('Per company: latest capex guidance, the previous quarter&#8217;s guidance, the data-center demand read, and the backlog trend (year-ago -&gt; prev quarter -&gt; current). Figures highlighted — '
   f'{hl("capex")} teal, {hd("demand")} amber, {hb("backlog")} violet.', sub))
story.append(Spacer(1,10))

for name,col,tag,rows in companies:
    block=[]
    # header bar
    hdr=Table([[Paragraph(name, coname), Paragraph(tag, cotag)]], colWidths=[CW*0.55, CW*0.45])
    hdr.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,-1),colors.HexColor(col)),
        ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ('LEFTPADDING',(0,0),(-1,-1),9),('RIGHTPADDING',(0,0),(-1,-1),9),
        ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),
    ]))
    block.append(hdr)
    # rows
    data=[[Paragraph(l, lbl), Paragraph(t, body)] for l,t in rows]
    tbl=Table(data, colWidths=[24*mm, CW-24*mm])
    tbl.setStyle(TableStyle([
        ('VALIGN',(0,0),(-1,-1),'TOP'),
        ('LEFTPADDING',(0,0),(0,-1),0),('LEFTPADDING',(1,0),(1,-1),8),
        ('RIGHTPADDING',(0,0),(-1,-1),2),
        ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),
        ('LINEBELOW',(0,0),(-1,-2),0.4,colors.HexColor('#ECEEF0')),
        ('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#FCFDFD')),
        ('BOX',(0,0),(-1,-1),0.5,colors.HexColor('#E6EAED')),
    ]))
    block.append(tbl)
    block.append(Spacer(1,11))
    story.append(KeepTogether(block))

doc.build(story)
print("WROTE", OUT)
