"""Render editable Markdown equations for the PDF reading copy."""
from pathlib import Path
import json
import re
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from PIL import Image

PAPER=Path.cwd()
OUT=PAPER/'parper/second_draft_equations'
OUT.mkdir(parents=True,exist_ok=True)
source=(PAPER/'parper/WEVJ_second_draft.md').read_text(encoding='utf-8')
equations=re.findall(r'\\\[\s*(.*?)\s*\\\]',source,re.S)
manifest=[]
for i,eq in enumerate(equations,1):
    fig=plt.figure(figsize=(11,1),dpi=300)
    fig.text(.01,.5,'$'+eq.replace('\n',' ')+'$',fontsize=14,fontfamily='DejaVu Serif',va='center')
    path=OUT/f'equation_{i:02d}.png'
    fig.savefig(path,bbox_inches='tight',pad_inches=.04,dpi=300,facecolor='white')
    plt.close(fig)
    w,h=Image.open(path).size
    manifest.append(dict(number=i,path=str(path),width_points=w/300*72*11/14,height_points=h/300*72*11/14))
(OUT/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
print(f'{len(manifest)} equations rendered')
