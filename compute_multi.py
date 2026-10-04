"""
Multi-source compute load model: the same job as heat_models.ComputeLoadModel (how a data
center's IT power - and so its heat - moves hour to hour), trained on power / GPU / CPU traces
from several operators instead of Google's alone.

    COMPUTE_TRACES=multi python heat_models.py      # use this model inside the full pipeline
    python compute_multi.py                         # train + leave-one-source-out validation only

Data: heat-reuse-data/data/compute_traces/hourly_all.csv.gz, built by
`python -m scripts.ml_data.compute_traces` (Google 2019 power + other public traces, harmonised).
Utilisation traces are converted to relative power with the linear server power model
(P = P_idle + (P_peak - P_idle) x u) in that script.

Target   est_power_rel = hourly power / that domain's mean power
Features hours after the domain's daily low, day of week (when known), workload type
         (web serving, mixed cloud, VMs, DL training, LLM inference, HPC...), true-power flag
Validation  leave-one-SOURCE-out: train on every operator but one, predict the one left out,
            and compare with a model trained on Google alone. This is the test that matters
            for a data center whose own traces we do not have.
"""
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

ROOT = Path(__file__).parent
TRACES = ROOT / 'heat-reuse-data' / 'data' / 'compute_traces' / 'hourly_all.csv.gz'
QUANTILES = [0.1, 0.5, 0.9]
TROUGH_LOCAL_HOUR = 5
# compute type in heat_models.py -> workload types to learn from, most specific first
KIND_WORKLOADS = {
    'site1_colo':   ['mixed_cloud', 'vm_iaas', 'web_serving'],       # multi-tenant carrier hotel
    'ai_inference': ['llm_inference', 'web_serving', 'mixed_cloud'], # user-facing serving
    'ai_training':  ['dl_training', 'hpc'],                          # long GPU jobs
}


# Traces kept out of training (still listed in the report): they are not the power of a whole facility.
EXCLUDE = {
    'azure_llm': 'request/token counts, not power (daily swing >100%)',
    'pm100': 'power summed per job, so idle nodes are missing (swings ~70%/day)',
}


class MultiSourceComputeModel:
    FEATS = ['hrs_after_trough', 'dow', 'workload_code', 'is_power']

    def __init__(self, path=TRACES, min_hours=48):
        g = pd.read_csv(path, low_memory=False)
        self.excluded = {s: why for s, why in EXCLUDE.items() if (g['source'] == s).any()}
        g = g[~g['source'].isin(EXCLUDE)]
        g = g[np.isfinite(g['est_power_rel']) & (g['est_power_rel'] > 0)].copy()
        g['domain'] = g['source'].astype(str) + '/' + g['domain'].astype(str)
        g = g[g.groupby('domain')['domain'].transform('size') >= min_hours]
        g['hour'] = self._hour_index(g)
        g = g.sort_values(['domain', 'hour'])
        # renormalise per domain after filtering, and clip sensor glitches
        g['rel'] = (g['est_power_rel'] / g.groupby('domain')['est_power_rel'].transform('mean')).clip(0, 3)
        # align each domain on its own daily low (most traces hide their time zone)
        prof = g.groupby(['domain', g['hour'] % 24])['rel'].mean()
        trough = prof.groupby(level=0).idxmin().map(lambda t: t[1])
        g['hrs_after_trough'] = (g['hour'] % 24 - g['domain'].map(trough)) % 24
        g['dow'] = self._dow(g)
        self.workloads = sorted(g['workload'].dropna().unique())
        g['workload_code'] = g['workload'].map({w: i for i, w in enumerate(self.workloads)})
        g['is_power'] = (g['metric'] == 'power').astype(int)
        self.data = g.reset_index(drop=True)
        self.sources = sorted(g['source'].unique())

    @staticmethod
    def _hour_index(g):
        h = g['hour_utc']
        if np.issubdtype(h.dtype, np.number):
            return h.astype(int)
        t = pd.to_datetime(h, utc=True, errors='coerce')
        return ((t - pd.Timestamp('2000-01-01', tz='UTC')) // pd.Timedelta(hours=1)).astype(int)

    @staticmethod
    def _dow(g):
        if g['hour_utc'].dtype == object:
            t = pd.to_datetime(g['hour_utc'], utc=True, errors='coerce')
            if t.notna().mean() > 0.9:
                return t.dt.dayofweek.fillna(-1).astype(int)
        return pd.Series(-1, index=g.index)                    # unknown calendar: the model learns "no weekday info"

    def _fit(self, d):
        return {q: HistGradientBoostingRegressor(loss='quantile', quantile=q, max_iter=250, learning_rate=0.05,
                                                 categorical_features=[2], random_state=0).fit(d[self.FEATS], d['rel'])
                for q in QUANTILES}

    def fit(self, rows=None):
        self.models = self._fit(self.data if rows is None else self.data[rows])
        return self

    def validate(self):
        """Leave-one-source-out, vs a Google-only model and vs a flat 'always average' guess."""
        lines, rows = [], []
        google = self.data['source'].str.startswith('google')
        for src in self.sources:
            test = self.data['source'] == src
            if test.all() or (~test).sum() < 500:
                continue
            d = self.data[test]
            m = self._fit(self.data[~test])
            p = {q: mm.predict(d[self.FEATS]) for q, mm in m.items()}
            row = dict(source=src, domains=d['domain'].nunique(), hours=len(d),
                       mae_multi=mean_absolute_error(d['rel'], p[0.5]),
                       cover=((d['rel'] >= p[0.1]) & (d['rel'] <= p[0.9])).mean(),
                       mae_flat=mean_absolute_error(d['rel'], np.ones(len(d))))
            if not src.startswith('google') and google.sum() > 500:
                mg = self._fit(self.data[google])[0.5]
                row['mae_google_only'] = mean_absolute_error(d['rel'], mg.predict(d[self.FEATS]))
            rows.append(row)
        self.fit()
        resid = self.data['rel'] - self.models[0.5].predict(self.data[self.FEATS])
        self.data['resid'] = resid
        self.resid_by_workload = {w: [r.to_numpy() for _, r in grp.groupby('domain')['resid'] if len(r) >= 168]
                                  for w, grp in self.data.groupby('workload')}
        self.resid_all = [r for v in self.resid_by_workload.values() for r in v]
        self.validation = pd.DataFrame(rows)
        lines.append(f"Multi-source trace model: {len(self.sources)} sources, {self.data['domain'].nunique()} domains, "
                     f"{len(self.data):,} domain-hours; workloads: {', '.join(self.workloads)}")
        for s, why in self.excluded.items():
            lines.append(f"  not used for training: {s} ({why})")
        for r in rows:
            g = f", Google-only {r['mae_google_only']:.3f}" if 'mae_google_only' in r else ''
            lines.append(f"  held out {r['source']:16s} MAE {r['mae_multi']:.3f} of mean load (flat guess {r['mae_flat']:.3f}{g})"
                         f" | P10-P90 coverage {r['cover']:.0%} | {r['domains']} domains")
        return lines

    def describe(self):
        g = self.data
        daily = g.groupby(['domain', g['hour'] // 24])['rel'].agg(['max', 'min'])
        out = []
        for src, d in g.groupby('source'):
            dd = daily.loc[daily.index.get_level_values(0).isin(d['domain'].unique())]
            out.append(f"{src}: P10-P90 {np.percentile(d['rel'], 10):.2f}-{np.percentile(d['rel'], 90):.2f} x mean, "
                       f"daily swing {(dd['max'] - dd['min']).median():.1%}")
        return 'Hourly load by source: ' + '; '.join(out)

    # ---- interface used by heat_models.py (same names as ComputeLoadModel)
    def share_for(self, kind):
        """Workload type used for a compute kind (heat_models passes this back into simulate)."""
        for w in KIND_WORKLOADS.get(kind, []):
            if w in self.workloads:
                return w
        return self.workloads[0]

    def _X(self, hrs, dow, workload):
        return pd.DataFrame({'hrs_after_trough': hrs, 'dow': dow,
                             'workload_code': self.workloads.index(workload), 'is_power': 1})

    def simulate(self, idx, workload, rng=None):
        """Relative IT load (mean ~1) per hour: P50 shape + a bootstrapped week of real residuals of that workload."""
        rng = rng if rng is not None else np.random.default_rng(42)
        dow = idx.dayofweek if (self.data['dow'] >= 0).any() else -1
        shape = self.models[0.5].predict(self._X((idx.hour - TROUGH_LOCAL_HOUR) % 24, dow, workload)[self.FEATS])
        pool = self.resid_by_workload.get(workload) or self.resid_all
        res = np.empty(len(idx))
        for s in range(0, len(idx), 168):
            r = pool[rng.integers(len(pool))]
            start = rng.integers(0, len(r) - 168 + 1)
            res[s:s + 168] = r[start:start + 168][:len(idx) - s]
        return np.clip(shape + res, 0, None)

    def hourly_profile(self, kind, q):
        hours = np.arange(24)
        dow = 2 if (self.data['dow'] >= 0).any() else -1
        return self.models[q].predict(self._X((hours - TROUGH_LOCAL_HOUR) % 24, dow, self.share_for(kind))[self.FEATS])


if __name__ == '__main__':
    m = MultiSourceComputeModel()
    print('\n'.join(m.validate()))
    print(m.describe())
    for k in KIND_WORKLOADS:
        print(f"{k}: learns from '{m.share_for(k)}' traces")
