"""
Charts for energy_distribution.py: how priorities were decided and how much of their heat need
buildings actually get.

    python plot_distribution.py        (run energy_distribution.py first)

Writes outputs/distribution/charts/*.png
"""
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from energy_distribution import REUSE_TARGET, SITES, TIER_NAMES, WEIGHTS

ROOT = Path(__file__).parent
DIST = ROOT / 'outputs' / 'distribution'
MOD = ROOT / 'outputs' / 'models'
OUT = DIST / 'charts'
OUT.mkdir(parents=True, exist_ok=True)

TIER_COLORS = {1: '#c0392b', 2: '#2e86c1', 3: '#f39c12', 4: '#95a5a6'}
PART_COLORS = {'value': '#8e44ad', 'people': '#16a085', 'equity': '#d35400', 'proximity': '#7f8c8d'}
plt.rcParams.update({'figure.dpi': 130, 'axes.spines.top': False, 'axes.spines.right': False,
                     'axes.titleweight': 'bold', 'font.size': 9})


def load(site, label=''):
    b = pd.read_csv(DIST / f'{site}{label}_allocation_buildings.csv', low_memory=False)
    real = b[b['id'] != 'NEW_ANCHOR']
    b['people_norm'] = np.log1p(b['people']) / np.log1p(real['people'].max())
    b['proximity'] = (1 - b['dist_m'] / SITES[site]['radius_m']).clip(0, 1)
    b['short_name'] = b['name'].fillna(b['address']).astype(str).str.slice(0, 34)
    return b


def save(fig, name):
    fig.tight_layout()
    fig.savefig(OUT / name, bbox_inches='tight')
    plt.close(fig)
    print('wrote', OUT / name)


# ---------------- 1. social value by use type ----------------
def chart_social_value(b, site):
    g = (b[b['id'] != 'NEW_ANCHOR'].groupby('use_label')
         .agg(value=('social_value', 'median'), n=('id', 'size'), tier=('tier', 'min'))
         .sort_values('value'))
    fig, ax = plt.subplots(figsize=(8, 0.28 * len(g) + 1.2))
    ax.barh(g.index, g['value'], color=[TIER_COLORS[t] for t in g['tier']])
    for i, (v, n) in enumerate(zip(g['value'], g['n'])):
        ax.text(v + 0.01, i, f'{v:.2f}  ({n:,} bldgs)', va='center', fontsize=8)
    for cut in (0.4, 0.7, 0.9):
        ax.axvline(cut, color='k', lw=0.6, ls=':')
    ax.set_xlim(0, 1.25)
    ax.set_xlabel('Social value score (0-1); dotted lines = tier cut-offs')
    ax.set_title(f'{SITES[site]["name"]}: how valuable each kind of building is to society')
    ax.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=c) for c in TIER_COLORS.values()],
              labels=[f'Tier {t}: {n}' for t, n in TIER_NAMES.items()], loc='lower right', fontsize=7)
    save(fig, f'{site}_1_social_value_by_use.png')


# ---------------- 2. score breakdown for the top of the list ----------------
def chart_score_breakdown(b, site, n=25):
    top = b[b['id'] != 'NEW_ANCHOR'].sort_values('priority_rank').head(n).iloc[::-1]
    parts = {'value': WEIGHTS['value'] * top['social_value'], 'people': WEIGHTS['people'] * top['people_norm'],
             'equity': WEIGHTS['equity'] * top['equity'], 'proximity': WEIGHTS['proximity'] * top['proximity']}
    fig, ax = plt.subplots(figsize=(9, 0.3 * n + 1.5))
    left = np.zeros(len(top))
    labels = [f"#{r}  {nm}  ({u})" for r, nm, u in zip(top['priority_rank'], top['short_name'], top['use_label'])]
    for k, v in parts.items():
        ax.barh(labels, v, left=left, color=PART_COLORS[k], label=f'{k} (weight {WEIGHTS[k]:.2f})')
        left += v.to_numpy()
    for i, (s, st) in enumerate(zip(top['priority_score'], top['status'])):
        mark = 'CONNECTED' if str(st).startswith('connected') else ('too remote' if 'remote' in str(st) else '')
        ax.text(s + 0.005, i, f'{s:.2f} {mark}', va='center', fontsize=7,
                color='#1e8449' if mark == 'CONNECTED' else '#555')
    ax.set_xlim(0, 1.15)
    ax.set_xlabel('Priority score = weighted sum of the four parts')
    ax.set_title(f'{SITES[site]["name"]}: top {n} buildings by priority score, and why')
    ax.legend(loc='lower right', fontsize=7)
    save(fig, f'{site}_2_priority_score_breakdown.png')


# ---------------- 3. map of who gets heat ----------------
def chart_map(b, site):
    geo = pd.read_csv(MOD / f'{site}_building_demand_summary.csv', usecols=['id', 'lat', 'lon'], low_memory=False)
    geo['id'] = geo['id'].astype(str)
    m = b.assign(id=b['id'].astype(str)).merge(geo, on='id', how='inner')
    fig, ax = plt.subplots(figsize=(7.5, 7.5))
    groups = [('too remote', '#f5b041', 'too remote (pipe loss > 25%)'),
              ('not connected', '#aab7b8', 'not connected (heat already fully used)')]
    for key, c, lab in groups:
        g = m[m['status'].astype(str).str.startswith(key)]
        ax.scatter(g['lon'], g['lat'], s=4 if key == 'too remote' else 3, c=c, label=f'{lab}: {len(g):,}', linewidths=0)
    con = m[m['status'].astype(str).str.startswith('connected')]
    size = 30 + 400 * np.sqrt(con['annual_mwh'] / con['annual_mwh'].max())
    ax.scatter(con['lon'], con['lat'], s=size, c=[TIER_COLORS[t] for t in con['tier']],
               edgecolors='k', linewidths=0.6, zorder=3, label=f'connected: {len(con)} (size = heat need)')
    for i, (_, r) in enumerate(con.iterrows()):
        ax.annotate(f"#{r['priority_rank']} {r['short_name'][:22]}", (r['lon'], r['lat']), fontsize=6.5,
                    xytext=(6, 10 - 10 * (i % 3)), textcoords='offset points', zorder=4,
                    bbox=dict(boxstyle='round,pad=0.15', fc='white', ec='none', alpha=0.7))
    lat0, lon0 = (SITES[site]['lat'], SITES[site]['lon']) if SITES[site]['lat'] else \
        (m['lat'].mean(), m['lon'].mean())
    ax.scatter([lon0], [lat0], marker='*', s=300, c='gold', edgecolors='k', zorder=5, label='data center')
    ax.set_aspect(1 / np.cos(np.radians(lat0)))
    ax.set_xlabel('longitude')
    ax.set_ylabel('latitude')
    ax.set_title(f'{SITES[site]["name"]}: who gets heat (colour = priority tier)')
    ax.legend(loc='lower left', fontsize=7, markerscale=0.6)
    save(fig, f'{site}_3_map_who_gets_heat.png')


# ---------------- 4. share of need covered per connected building ----------------
def chart_coverage(b, site, label=''):
    c = b[b['status'].astype(str).str.startswith('connected')].sort_values('priority_rank')
    y = np.arange(len(c))[::-1]
    fig, ax = plt.subplots(figsize=(9, 0.38 * len(c) + 1.6))
    p50 = c['share_of_need_covered_p50'].to_numpy()
    err = np.vstack([p50 - c['share_of_need_covered_p10'], c['share_of_need_covered_p90'] - p50])
    ax.barh(y + 0.2, p50, height=0.4, color=[TIER_COLORS[t] for t in c['tier']], xerr=err,
            error_kw=dict(lw=0.8, capsize=2), label='whole year (P50, bar = P10-P90)')
    ax.barh(y - 0.2, c['winter_share_covered_p50'], height=0.4, color='#5d6d7e', alpha=0.6,
            label='winter (Dec-Feb)')
    ax.set_yticks(y)
    ax.set_yticklabels([f"#{r} {n} [T{t}]" for r, n, t in zip(c['priority_rank'], c['short_name'], c['tier'])])
    for yi, v, need, got in zip(y, c['share_of_need_covered_p90'], c['annual_mwh'], c['dc_heat_mwh_p50']):
        ax.text(min(v, 1) + 0.02, yi + 0.2, f'{got / 1000:,.1f} of {need / 1000:,.1f} GWh', va='center', fontsize=7)
    ax.set_xlim(0, 1.3)
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1))
    ax.set_xlabel('Share of the building\'s heat need supplied by the data center (rest: its own boiler / steam)')
    ax.set_title(f'{SITES[site]["name"]}{label.replace("_", " ")}: how much of its need each connected building gets')
    ax.legend(loc='lower right', fontsize=7)
    save(fig, f'{site}{label}_4_share_of_need_covered.png')


# ---------------- 5. monthly allocation by tier ----------------
def chart_tiers_monthly(site, label=''):
    t = pd.read_csv(DIST / f'{site}{label}_allocation_hourly_by_tier.csv', parse_dates=['timestamp'])
    mon = t.groupby(t['timestamp'].dt.month).mean(numeric_only=True)
    tiers = sorted(int(c[4]) for c in mon if c.endswith('_demand_mw'))
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    x = np.arange(1, 13)
    bottom = np.zeros(12)
    for tr in tiers:
        axes[0].bar(x, mon[f'tier{tr}_allocated_mw'], bottom=bottom, color=TIER_COLORS[tr], label=f'Tier {tr} gets')
        bottom += mon[f'tier{tr}_allocated_mw'].to_numpy()
    need = sum(mon[f'tier{tr}_demand_mw'] for tr in tiers)
    axes[0].plot(x, need, 'k--', marker='o', ms=3, label='total need of connected buildings')
    axes[0].set_title('Average MW allocated per tier, by month')
    axes[0].set_ylabel('MW')
    axes[0].legend(fontsize=7)
    for tr in tiers:
        share = mon[f'tier{tr}_allocated_mw'] / mon[f'tier{tr}_demand_mw'].replace(0, np.nan)
        axes[1].plot(x, share, marker='o', color=TIER_COLORS[tr], label=f'Tier {tr}: {TIER_NAMES[tr]}')
    axes[1].set_ylim(0, 1.05)
    axes[1].yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1))
    axes[1].set_title('Share of each tier\'s need met by data-center heat')
    axes[1].legend(fontsize=7)
    for ax in axes:
        ax.set_xticks(x)
        ax.set_xticklabels(list('JFMAMJJASOND'))
    fig.suptitle(f'{SITES[site]["name"]}{label.replace("_", " ")}: priority in action (median Monte Carlo run)',
                 fontweight='bold')
    save(fig, f'{site}{label}_5_allocation_by_tier_monthly.png')


# ---------------- 6. heat balance and reuse rate ----------------
def chart_heat_balance(site, label=''):
    h = pd.read_csv(DIST / f'{site}{label}_heat_balance_hourly.csv', parse_dates=['timestamp'])
    mon = h.groupby(h['timestamp'].dt.month).mean(numeric_only=True)
    x = np.arange(1, 13)
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(x, mon['heat_out_mw_p50'], 'k-', marker='o', label='heat the data center produces')
    ax.plot(x, mon['delivered_mw_p50'], color='#7d3c98', marker='o', label='after heat pumps (adds compressor heat)')
    ax.bar(x, mon['used_mw_p50'], color='#27ae60', alpha=0.8, label='used by buildings')
    ax.bar(x, mon['rejected_mw_p50'], bottom=mon['used_mw_p50'], color='#e74c3c', alpha=0.7,
           label='surplus rejected')
    ax.set_ylabel('MW (monthly mean, P50)')
    ax2 = ax.twinx()
    dc_share = (mon['captured_mw_p50'] / mon['delivered_mw_p50'])
    reuse = mon['used_mw_p50'] * dc_share / mon['heat_out_mw_p50']
    ax2.plot(x, reuse, color='#1f618d', ls='--', marker='s', label='reuse rate')
    ax2.axhline(REUSE_TARGET, color='#1f618d', lw=0.8, ls=':')
    ax2.text(12.4, REUSE_TARGET, f'{REUSE_TARGET:.0%} target', color='#1f618d', fontsize=7, va='center')
    ax2.set_ylim(0, 1.05)
    ax2.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1))
    ax2.spines['right'].set_visible(True)
    ax.set_xticks(x)
    ax.set_xticklabels(list('JFMAMJJASOND'))
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=7, loc='upper center', bbox_to_anchor=(0.5, -0.1), ncol=3, frameon=False)
    ax.set_title(f'{SITES[site]["name"]}{label.replace("_", " ")}: where the data center\'s heat goes')
    save(fig, f'{site}{label}_6_heat_balance_reuse.png')


# ---------------- 7. score vs people: everyone in the radius ----------------
def chart_score_scatter(b, site):
    r = b[b['id'] != 'NEW_ANCHOR']
    fig, ax = plt.subplots(figsize=(8, 5))
    for t in sorted(r['tier'].unique()):
        g = r[r['tier'] == t]
        ax.scatter(g['people'].clip(lower=1), g['priority_score'], s=6, alpha=0.4, c=TIER_COLORS[t],
                   label=f'Tier {t}: {TIER_NAMES[t]} ({len(g):,})')
    con = r[r['status'].astype(str).str.startswith('connected')]
    ax.scatter(con['people'].clip(lower=1), con['priority_score'], s=60, facecolors='none', edgecolors='k',
               label=f'connected ({len(con)})')
    ax.set_xscale('log')
    ax.set_xlabel('People served (residents or daily users, log scale)')
    ax.set_ylabel('Priority score')
    ax.set_title(f'{SITES[site]["name"]}: priority score of every building in the radius')
    ax.legend(fontsize=7, loc='lower right')
    save(fig, f'{site}_7_score_vs_people.png')


# ---------------- 8. where the heat goes: produced -> used ----------------
def chart_heat_chain():
    files = sorted(DIST.glob('*_heat_chain.csv'))
    fig, axes = plt.subplots(1, len(files), figsize=(3.6 * len(files), 4.2), sharey=True)
    labels = ['produced', 'reaches\nwater loop', 'captured by\nheat pumps', 'after pipe\nlosses', 'used by\nbuildings']
    colors = ['#34495e', '#2874a6', '#7d3c98', '#d68910', '#27ae60']
    for ax, f in zip(np.atleast_1d(axes), files):
        c = pd.read_csv(f)
        share = c['share_of_produced_p50'].to_numpy()
        ax.bar(range(len(c)), share, color=colors)
        for i, (v, g) in enumerate(zip(share, c['gwh_p50'])):
            ax.text(i, v + 0.02, f'{v:.0%}\n{g:,.0f} GWh', ha='center', fontsize=7)
        ax.axhline(REUSE_TARGET, color='r', ls=':', lw=0.9)
        ax.set_xticks(range(len(c)))
        ax.set_xticklabels(labels, fontsize=6.5)
        ax.set_ylim(0, 1.2)
        name = f.name.replace('_heat_chain.csv', '').replace('site1', 'Site 1').replace('site2_', 'Site 2 ')
        ax.set_title(name.replace('_', ' '), fontsize=9)
    np.atleast_1d(axes)[0].yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1))
    np.atleast_1d(axes)[0].set_ylabel('Share of the heat the data center produces (P50)')
    np.atleast_1d(axes)[-1].text(4.4, REUSE_TARGET, f'{REUSE_TARGET:.0%} target', color='r', fontsize=7, va='center')
    fig.suptitle('How much data-center heat can realistically be taken, step by step', fontweight='bold')
    save(fig, '0_heat_chain_all_sites.png')


if __name__ == '__main__':
    chart_heat_chain()
    b1 = load('site1')
    chart_social_value(b1, 'site1')
    chart_score_breakdown(b1, 'site1')
    chart_score_scatter(b1, 'site1')
    chart_map(b1, 'site1')
    chart_coverage(b1, 'site1')
    chart_tiers_monthly('site1')
    chart_heat_balance('site1')

    b2 = load('site2', '_ai_training')
    chart_social_value(b2, 'site2')
    chart_score_breakdown(b2, 'site2')
    chart_score_scatter(b2, 'site2')
    for kind in ['ai_training', 'ai_inference', 'bitcoin_mining']:
        chart_heat_balance('site2', f'_{kind}')
