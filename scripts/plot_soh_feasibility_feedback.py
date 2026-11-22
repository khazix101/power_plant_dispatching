"""Plot recorded SOH coupling; distinguish accepted days from retained failures."""
from pathlib import Path
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / 'parper/experiment_runs/phase2_90day_20261115/2020_may_soh090'
OUT = ROOT / 'parper/second_draft_figures/figure_13_soh_feasibility_state_feedback.png'
plt.rcParams.update({'font.size': 10, 'axes.spines.top': False,
                     'axes.spines.right': False})
fig, axes = plt.subplots(1, 3, figsize=(12.4, 3.8), constrained_layout=True)
specs = [('soh_end_posthoc', 'End-of-day SOH', 'SOH'),
         ('usable_capacity_end_kwh', 'End-of-day usable capacity', 'Energy (kWh)'),
         ('initial_marginal_degradation_cost_yuan_per_kwh', 'Daily planning coefficient',
          'Marginal cost (CNY/kWh)')]
for arm, label, color, style in [
    ('fixed_price_bess', 'Fixed price', '#777777', ':'),
    ('dynamic_fixed_cost', 'Dynamic fixed cost', '#2474b5', '--'),
    ('soh_aware', 'SOH-aware', '#d88116', '-')]:
    data = json.loads((RUN / f'{arm}.json').read_text(encoding='utf8'))
    days = data['days'][:73]
    accepted = [(i + 1, d) for i, d in enumerate(days)
                if d['status'] == 'diagnostic_completed' and i < 72]
    for ax, (field, title, ylabel) in zip(axes, specs):
        ax.plot([i for i, d in accepted], [d['summary'][field] for i, d in accepted],
                label=label, color=color, linestyle=style, linewidth=1.8)
        failures = [(i + 1, d) for i, d in enumerate(days)
                    if d['status'] != 'diagnostic_completed']
        for i, d in failures:
            ax.scatter([i], [d['summary'][field]], marker='x', s=45,
                       color='#b52828', zorder=5)
        ax.set(title=title, ylabel=ylabel, xlabel='Day from 1 May 2020')
        ax.grid(alpha=.18)
        ax.ticklabel_format(useOffset=False, style='plain', axis='y')
axes[0].legend(fontsize=8, loc='upper right')
fig.suptitle('Recorded health feedback: 72 accepted days; dynamic search gate failed on day 73',
             fontsize=11)
fig.savefig(OUT, dpi=220, bbox_inches='tight', facecolor='white')
plt.close(fig)
print(OUT)
