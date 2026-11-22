"""Build the English reading PDF from its checked editable Markdown source.

Run with the bundled document Python. Equations are separately rendered with
the project's scientific Python; no terminal LaTeX installation is required.
"""
from pathlib import Path
import hashlib
import html
import json
import re
from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, KeepTogether, CondPageBreak
from pypdf import PdfReader

PAPER=Path.cwd()
ROOT=PAPER/'parper'
SOURCE=ROOT/'WEVJ_second_draft_zh.md'
OUTPUT=ROOT/'WEVJ_second_draft_zh_feasibility_20261118.pdf'
WIDTH=A4[0]-108


def markup(text):
    text=html.escape(text.replace('—',' - ').replace('–','-').replace('\u2011','-'))
    text=re.sub(r'\b(E_nom|A_i|p_i|q_t|b_t|r_t|c_t|u_t|H_d)\b',lambda m:m[0].split('_')[0]+'<sub>'+m[0].split('_')[1]+'</sub>',text)
    text=re.sub(r'\*\*(.+?)\*\*',r'<b>\1</b>',text)
    text=re.sub(r'`([^`]+)`',r'\1',text)
    return text


def main():
    for name,path in [('TimesStudy','simsun.ttc'),('TimesStudy-Bold','simhei.ttf'),('TimesStudy-Italic','simsun.ttc'),('TimesStudy-BoldItalic','simhei.ttf')]:
        pdfmetrics.registerFont(TTFont(name,str(Path('C:/Windows/Fonts')/path)))
    pdfmetrics.registerFontFamily('TimesStudy',normal='TimesStudy',bold='TimesStudy-Bold',italic='TimesStudy-Italic',boldItalic='TimesStudy-BoldItalic')
    styles={
        'body':ParagraphStyle('body',fontName='TimesStudy',fontSize=10.5,leading=17,alignment=TA_JUSTIFY,spaceAfter=7,allowWidows=0,allowOrphans=0),
        'title':ParagraphStyle('title',fontName='TimesStudy-Bold',fontSize=17,leading=25,alignment=TA_LEFT,spaceAfter=14,keepWithNext=True),
        'h2':ParagraphStyle('h2',fontName='TimesStudy-Bold',fontSize=13,leading=20,spaceBefore=11,spaceAfter=7,keepWithNext=True),
        'h3':ParagraphStyle('h3',fontName='TimesStudy-Bold',fontSize=11.5,leading=18,spaceBefore=8,spaceAfter=5,keepWithNext=True),
        'caption':ParagraphStyle('caption',fontName='TimesStudy',fontSize=9.5,leading=14,spaceBefore=5,spaceAfter=9),
        'tablecaption':ParagraphStyle('tablecaption',fontName='TimesStudy',fontSize=9.5,leading=14,spaceBefore=7,spaceAfter=5,keepWithNext=True),
        'cell':ParagraphStyle('cell',fontName='TimesStudy',fontSize=9.2,leading=14,alignment=TA_LEFT),
        'reference':ParagraphStyle('reference',fontName='TimesStudy',fontSize=9.5,leading=14,spaceAfter=6,splitLongWords=True),
    }
    for style in styles.values():
        style.wordWrap = 'CJK'
    text=SOURCE.read_text(encoding='utf-8')
    lines=text.splitlines(); story=[]; i=0; equation=0; figures=0; in_refs=False
    eqs=json.loads((PAPER/'parper/second_draft_equations/manifest.json').read_text(encoding='utf-8'))
    while i<len(lines):
        line=lines[i].strip()
        if not line: i+=1; continue
        if line.startswith('# '):
            story.append(Paragraph(markup(line[2:]),styles['title'])); i+=1; continue
        if line.startswith('## '):
            in_refs=line=='## 参考文献'
            story.append(Paragraph(markup(line[3:]),styles['h2'])); i+=1; continue
        if line.startswith('### '):
            story.append(Paragraph(markup(line[4:]),styles['h3'])); i+=1; continue
        if line=='\\[':
            equation+=1
            while lines[i].strip()!='\\]': i+=1
            entry=eqs[equation-1]; w,h=entry['width_points'],entry['height_points']
            scale=min(1,WIDTH/w)
            obj=Image(entry['path'],width=w*scale,height=h*scale); obj.hAlign='CENTER'
            story.extend([Spacer(1,4),obj,Spacer(1,8)]); i+=1; continue
        picture=re.fullmatch(r'!\[[^\]]*\]\(([^)]+)\)',line)
        if picture:
            figures+=1; path=(SOURCE.parent/picture.group(1)).resolve()
            pw,ph=PILImage.open(path).size
            w=WIDTH; h=w*ph/pw
            maximum_height=340 if 'figure_07_' in path.name else (300 if 'figure_09_' in path.name else 385)
            if h>maximum_height: w*=maximum_height/h; h=maximum_height
            pic=Image(str(path),width=w,height=h); pic.hAlign='CENTER'
            j=i+1
            while j<len(lines) and not lines[j].strip(): j+=1
            cap=lines[j].strip()
            assert cap.startswith('**图'),cap
            story.append(KeepTogether([Spacer(1,7),pic,Paragraph(markup(cap),styles['caption'])]))
            i=j+1; continue
        if line.startswith('|'):
            data=[]
            while i<len(lines) and lines[i].strip().startswith('|'):
                cells=[c.strip() for c in lines[i].strip().strip('|').split('|')]
                if not all(re.fullmatch(r':?-+:?',c) for c in cells): data.append(cells)
                i+=1
            n=len(data[0])
            if n==3: widths=[WIDTH*.35,WIDTH*.30,WIDTH*.35]
            elif n==6: widths=[WIDTH*.14,WIDTH*.26]+[WIDTH*.15]*4
            elif n==5: widths=[WIDTH*.30]+[WIDTH*.175]*4
            else: widths=[WIDTH/n]*n
            formatted=[[Paragraph(markup(c),styles['cell']) for c in row] for row in data]
            t=Table(formatted,colWidths=widths,repeatRows=1,hAlign='CENTER')
            t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#f0f0f0')),
                ('LINEABOVE',(0,0),(-1,0),.65,colors.black),('LINEBELOW',(0,0),(-1,0),.45,colors.black),
                ('LINEBELOW',(0,-1),(-1,-1),.65,colors.black),('LEFTPADDING',(0,0),(-1,-1),5),
                ('RIGHTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
            story.extend([t,Spacer(1,8)]); continue
        para=[line]; i+=1
        while i<len(lines) and lines[i].strip() and not lines[i].lstrip().startswith(('#','|','![','\\[')):
            para.append(lines[i].strip()); i+=1
        joined=' '.join(para)
        sty='reference' if in_refs else ('tablecaption' if joined.startswith('**表') else 'body')
        story.append(Paragraph(markup(joined),styles[sty]))
    def page(canvas,doc):
        canvas.saveState(); canvas.setFont('TimesStudy',8.5); canvas.setFillColor(colors.HexColor('#555555'))
        canvas.drawString(54,A4[1]-34,'健康感知EV充电微电网 | 二稿中文审阅版 | 2026年11月18日')
        canvas.drawRightString(A4[0]-54,30,str(doc.page)); canvas.restoreState()
    doc=SimpleDocTemplate(str(OUTPUT),pagesize=A4,leftMargin=54,rightMargin=54,topMargin=52,bottomMargin=48,
        title=text.splitlines()[0][2:],author='',pageCompression=1)
    doc.build(story,onFirstPage=page,onLaterPages=page)
    reader=PdfReader(OUTPUT)
    pages=[p.extract_text() or '' for p in reader.pages]
    joined='\n'.join(pages)
    assert all(s in joined for s in ['摘要','9. 结论','参考文献','68.4979','44.8310','61.7198','11722.94','7.74','热安全'])
    assert all('图'+str(i)+'.' in joined for i in range(1,9))
    assert all('表'+str(i)+'.' in joined for i in range(1,6))
    assert not any(s in joined for s in ['@@REFERENCES@@','TODO','\u25a0','\ufffd'])
    audit=dict(pages=len(pages),figures=figures,equations=equation,source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        pdf_sha256=hashlib.sha256(OUTPUT.read_bytes()).hexdigest(),characters_per_page=[len(s) for s in pages],
        structural_checks_passed=True,visual_review='Pending final rendered-page inspection')
    (ROOT/'second_draft_zh_feasibility_pdf_audit_20261118.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
    print(json.dumps(audit,indent=2))


if __name__=='__main__': main()
