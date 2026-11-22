"""Prepare focused manuscripts, figures and equations from accepted evidence."""
from pathlib import Path
import json
import re
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / 'parper'
OUT = P / 'soh_paper_figures'
OUT.mkdir(exist_ok=True)
audit = json.loads((P/'soh_feasibility_audit_20261118.json').read_text(encoding='utf8'))
totals = audit['cost_and_constraint_audit']['paired_totals']
refs = re.findall(r'^\[(?:[1-9]|10)\].+$', (P/'WEVJ_second_draft.md').read_text(encoding='utf8'), re.M)
assert len(refs) == 10
rows = [
 ('EV requested energy (kWh)', 'EV请求电量 (kWh)', 'requested_kwh'),
 ('EV delivered energy (kWh)', 'EV实际交付电量 (kWh)', 'served_kwh'),
 ('Unserved EV energy (kWh)', 'EV未服务电量 (kWh)', 'shed_kwh'),
 ('Unserved non-EV energy (kWh)', '非EV缺供电量 (kWh)', 'non_ev_unserved_kwh'),
 ('Delivered EV revenue (CNY)', 'EV交付收入 (元)', 'revenue_yuan'),
 ('Trajectory aging cost (CNY)', '轨迹老化成本 (元)', 'aging_cost_yuan'),
 ('Renewable curtailment (kWh)', '可再生能源弃电 (kWh)', 'curtailment_kwh'),
 ('Battery conversion loss (kWh)', '电池转换损耗 (kWh)', 'battery_loss_kwh'),
 ('Network loss (kWh)', '网络损耗 (kWh)', 'network_loss_kwh'),
 ('Curtailment plus losses (kWh)', '弃电与总损耗 (kWh)', 'curtailment_plus_loss_kwh'),
 ('Modeled operating value (CNY)', '模型运营价值 (元)', 'modeled_operating_value_yuan'),
 ('Recorded elapsed time (s)', '记录耗时 (s)', 'runtime_s')]
for lang in ['en','zh']:
    path = P/f'SOH_game_dispatch_{lang}.md'
    s = path.read_text(encoding='utf8')
    headers = ['Metric','Fixed price','Dynamic fixed cost','SOH-aware'] if lang=='en' else ['指标','固定电价','动态固定成本','SOH-aware']
    table = '| '+' | '.join(headers)+' |\n|---|---:|---:|---:|\n'
    for en,zh,key in rows:
        table += '| '+(en if lang=='en' else zh)+' | '+' | '.join(f'{totals[a][key]:,.2f}' for a in ['fixed_price_bess','dynamic_fixed_cost','soh_aware'])+' |\n'
    s = s.replace('@@TOTALS_TABLE@@',table.rstrip()).replace('@@REFERENCES@@','\n\n'.join(refs))
    path.write_text(s,encoding='utf8')

RUN=P/'experiment_runs/phase2_90day_20261115/2020_may_soh090'
arms={a:json.loads((RUN/f'{a}.json').read_text(encoding='utf8'))['days'][:72] for a in totals}
assert all(all(d['status']=='diagnostic_completed' for d in days) for days in arms.values())
plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
x=list(range(1,73))
fig,axes=plt.subplots(2,1,figsize=(8,4.8),sharex=True,constrained_layout=True)
days=arms['soh_aware']
axes[0].plot(x,[d['summary']['renewable_input_kwh'] for d in days],color='#2474b5',label='PV + wind')
axes[0].axhline(1440,color='#777777',linestyle='--',label='Non-EV demand')
axes[0].set(ylabel='Energy (kWh/day)')
axes[0].legend(ncol=2,fontsize=9)
axes[1].plot(x,[d['summary']['requested_kwh'] for d in days],color='#d88116')
axes[1].set(ylabel='EV requests (kWh/day)',xlabel='Day from 1 May 2020')
for ax in axes: ax.grid(alpha=.2)
fig.savefig(OUT/'figure_01_inputs.png',dpi=250); plt.close(fig)
fig,axes=plt.subplots(3,1,figsize=(8,6.5),sharex=True,constrained_layout=True)
specs=[('soh_end_posthoc','End-of-day SOH'),('usable_capacity_end_kwh','Usable capacity (kWh)'),('initial_marginal_degradation_cost_yuan_per_kwh','Marginal cost (CNY/kWh)')]
for arm,label,color,style in [('fixed_price_bess','Fixed price','#777777',':'),('dynamic_fixed_cost','Dynamic fixed cost','#2474b5','--'),('soh_aware','SOH-aware','#d88116','-')]:
    for ax,(field,ylabel) in zip(axes,specs):
        ax.plot(x,[d['summary'][field] for d in arms[arm]],label=label,color=color,linestyle=style,linewidth=1.7)
        ax.set_ylabel(ylabel); ax.grid(alpha=.2); ax.ticklabel_format(useOffset=False,style='plain',axis='y')
axes[0].legend(ncol=3,fontsize=9)
axes[-1].set_xlabel('Day from 1 May 2020')
fig.savefig(OUT/'figure_02_health_feedback.png',dpi=250); plt.close(fig)
eqout=P/'soh_paper_equations'; eqout.mkdir(exist_ok=True)
equations=re.findall(r'\\\[\s*(.*?)\s*\\\]',(P/'SOH_game_dispatch_en.md').read_text(encoding='utf8'),re.S)
assert len(equations)==11
manifest=[]
for i,eq in enumerate(equations,1):
    fig=plt.figure(figsize=(11,1),dpi=300)
    fig.text(.01,.5,'$'+eq.replace('\n',' ')+'$',fontsize=14,fontfamily='DejaVu Serif',va='center')
    path=eqout/f'equation_{i:02d}.png'
    fig.savefig(path,bbox_inches='tight',pad_inches=.04,dpi=300,facecolor='white'); plt.close(fig)
    w,h=Image.open(path).size
    manifest.append(dict(number=i,path=str(path),width_points=w/300*72*11/14,height_points=h/300*72*11/14))
(eqout/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
print('Prepared two manuscripts, two figures, eleven equations.')
