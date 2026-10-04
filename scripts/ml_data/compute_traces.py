"""Public data-center compute/power traces beyond Google, harmonized to one hourly table.

    .venv/bin/python -m scripts.ml_data.compute_traces            # fetch + harmonize
    .venv/bin/python -m scripts.ml_data.compute_traces harmonize  # rebuild hourly_all only

Raw (or, for archives too big for local disk, stream-aggregated) files go under
heat-reuse-data/data/compute_traces/<source>/ ; the harmonized output is
heat-reuse-data/data/compute_traces/hourly_all.csv.gz with one row per (source, domain, hour).

Sources (all downloadable without login, billing account, or BigQuery):
  google_2019      Google cluster-data PowerData2019 (already on disk; measured PDU power)
  alibaba_2018     github.com/alibaba/clusterdata cluster-trace-v2018 machine_usage (CPU util),
                   first ~250 MB of the 1.7 GB tarball (whole machines, sorted by machine id)
  alibaba_pai_2020 cluster-trace-gpu-v2020 pai_machine_metric (machine GPU util averaged over
                   each instance's lifetime; spread back over hours)
  philly           github.com/msr-fiddle/philly-traces cluster_gpu_util (per-minute GPU util),
                   streamed from the 1 GB LFS tarball and aggregated to machine-hours on the fly
  bitbrains        TU Delft GWA-T-12 Bitbrains fastStorage (VM CPU util, 5 min), via the
                   Internet Archive copy (gwa.ewi.tudelft.nl no longer resolves)
  azure_vm         Azure Public Dataset V2 VM CPU readings. Each of the 195 files is a 3.67 h
                   time slice of all VMs; we read only the first 512 KB (first 5-min reading) of
                   each, giving a sparse fleet-mean sample roughly every 3.67 h over 30 days.
  azure_llm        Azure LLM inference trace 2024 (code service, 1 week of request tokens),
                   streamed and aggregated to hourly tokens. A demand proxy, not power.
  pm100            CINECA Marconi100 PM100 job table (Zenodo 10127767): measured per-job node
                   power, summed over running jobs per hour (needs pyarrow; falls back to a
                   python that has it, e.g. .team-venv).
  mit_supercloud   MIT Supercloud datacenter-challenge slurm-log: hourly allocated GPUs/CPUs,
                   and a sample of nvidia-smi job files to calibrate measured W per busy GPU.

Utilization -> power: linear server power model P = P_idle + (P_peak - P_idle) * u
(Fan, Weber & Barroso, "Power provisioning for a warehouse-sized computer", ISCA 2007;
Barroso, Hoelzle & Ranganathan, "The Datacenter as a Computer", 3rd ed. 2018, ch. 5).
Idle fraction 0.5 for CPU servers (SPECpower-era servers idle at ~40-60 % of peak) and 0.3
for GPU nodes (V100/A100 idle ~25-60 W vs 250-400 W TDP, host adds a floor; cf. MIT Supercloud
nvidia-smi idle readings ~25-40 W per V100).
"""

from __future__ import annotations

import gzip
import io
import json
import subprocess
import sys
import tarfile
import time
import zipfile
import zlib
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "heat-reuse-data" / "data"
OUT = DATA / "compute_traces"
GOOGLE = DATA / "cluster-data" / "powerdata_2019"
UA = {"User-Agent": "HeatOS NYU hackathon research (public data; httpx)"}

IDLE_FRAC = {"cpu_util": 0.5, "gpu_util": 0.3, "request_load": 0.3, "gpu_alloc": 0.3}
GROUP = 100                                    # machines per synthetic "power domain"

ALI18 = "http://aliopentrace.oss-cn-beijing.aliyuncs.com/v2018Traces/"
PAI = "https://aliopentrace.oss-cn-beijing.aliyuncs.com/v2020GPUTraces/"
PHILLY = "https://media.githubusercontent.com/media/msr-fiddle/philly-traces/master/trace-data.tar.gz"
BITBRAINS = ("https://web.archive.org/web/20190501075002id_/http://gwa.ewi.tudelft.nl/fileadmin/pds/"
             "trace-archives/grid-workloads-archive/datasets/gwa-t-12/fastStorage.zip")
AZ_V2 = ("https://github.com/Azure/AzurePublicDataset/releases/download/dataset-v2/"
         "trace_data_vm_cpu_readings_vm_cpu_readings-file-{i}-of-195.csv.gz")
AZ_LLM = ("https://github.com/Azure/AzurePublicDataset/releases/download/dataset-llm-2024/"
          "AzureLLMInferenceTrace_code_1week.csv")
PM100 = "https://zenodo.org/records/10127767/files/job_table.parquet?download=1"
MIT = "https://mit-supercloud-dataset.s3.amazonaws.com/datacenter-challenge/202201/"

ALI18_BYTES = 250_000_000
AZ_HEAD_BYTES = 512 * 1024
MIT_GPU_SAMPLE_BYTES = 30_000_000             # disk was nearly full on the build machine


# ----------------------------------------------------------------------------- download helpers
def _client() -> httpx.Client:
    return httpx.Client(headers=UA, timeout=httpx.Timeout(300, connect=30), follow_redirects=True)


def _download(url: str, dest: Path, max_bytes: int | None = None, tries: int = 6) -> Path:
    """Stream url to dest (via dest.part); stop after max_bytes. Resumes with Range on retry."""
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for i in range(tries):
        have = part.stat().st_size if part.exists() else 0
        if max_bytes and have >= max_bytes:
            break
        hdr = {"Range": f"bytes={have}-" + (str(max_bytes - 1) if max_bytes else "")} if (have or max_bytes) else {}
        try:
            with _client() as c, c.stream("GET", url, headers=hdr) as r:
                if r.status_code == 200 and have:          # server ignored Range: restart
                    have = 0
                    part.unlink(missing_ok=True)
                r.raise_for_status()
                with open(part, "ab") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        if max_bytes and have + len(chunk) > max_bytes:
                            chunk = chunk[: max_bytes - have]
                        f.write(chunk)
                        have += len(chunk)
                        if max_bytes and have >= max_bytes:
                            break
            break
        except (httpx.HTTPError, OSError) as e:
            print(f"  retry {i + 1}/{tries} {url}: {e}", flush=True)
            time.sleep(min(60, 3 * 2 ** i))
    else:
        raise RuntimeError(f"download failed: {url}")
    part.rename(dest)
    return dest


class _HttpStream(io.RawIOBase):
    """Read-only file object over a streaming HTTP GET (for archives too large to store)."""

    def __init__(self, url: str):
        self._c = _client()
        self._cm = self._c.stream("GET", url)
        self._r = self._cm.__enter__()
        self._r.raise_for_status()
        self._it = self._r.iter_raw(1 << 20)
        self._buf = b""
        self.n = 0

    def readable(self):
        return True

    def readinto(self, b):
        while not self._buf:
            try:
                self._buf = next(self._it)
            except StopIteration:
                return 0
        k = min(len(b), len(self._buf))
        b[:k], self._buf = self._buf[:k], self._buf[k:]
        self.n += k
        return k

    def close(self):
        try:
            self._cm.__exit__(None, None, None)
            self._c.close()
        finally:
            super().close()


class _Unseekable(io.RawIOBase):
    """Adapter so pandas can read a tarfile stream member (whose seekable() is broken)."""

    def __init__(self, f):
        self._f = f

    def readable(self):
        return True

    def readinto(self, b):
        data = self._f.read(len(b))
        b[:len(data)] = data
        return len(data)


def _member(tf: tarfile.TarFile, m: tarfile.TarInfo) -> io.BufferedReader:
    return io.BufferedReader(_Unseekable(tf.extractfile(m)), buffer_size=1 << 22)


def _open_stream(url: str) -> io.BufferedReader:
    return io.BufferedReader(_HttpStream(url), buffer_size=1 << 22)


# ----------------------------------------------------------------------------- fetchers
def fetch_alibaba_2018(out: Path) -> list[str]:
    d = out / "alibaba_2018"
    return [str(_download(ALI18 + "machine_meta.tar.gz", d / "machine_meta.tar.gz")),
            str(_download(ALI18 + "machine_usage.tar.gz", d / "machine_usage_head.tar.gz", ALI18_BYTES))]


def fetch_pai(out: Path) -> list[str]:
    d = out / "alibaba_pai_2020"
    return [str(_download(PAI + f, d / f)) for f in ("pai_machine_spec.tar.gz", "pai_machine_metric.tar.gz")]


def fetch_bitbrains(out: Path) -> list[str]:
    return [str(_download(BITBRAINS, out / "bitbrains" / "fastStorage.zip"))]


def fetch_azure_vm(out: Path) -> list[str]:
    d = out / "azure_vm"
    return [str(_download(AZ_V2.format(i=i), d / f"cpu_readings_head_{i:03d}.csv.gz", AZ_HEAD_BYTES))
            for i in range(1, 196)]


def fetch_philly(out: Path) -> list[str]:
    """Stream the 1 GB tarball; keep per-machine hourly mean GPU util (raw is ~6.6 GB unpacked)."""
    d = out / "philly"
    dest = d / "gpu_util_machine_hourly.csv.gz"
    if dest.exists():
        return [str(dest)]
    d.mkdir(parents=True, exist_ok=True)
    s = _open_stream(PHILLY)
    parts, machines = [], None
    with tarfile.open(fileobj=s, mode="r|gz") as tf:
        for m in tf:
            name = Path(m.name).name
            print(f"  philly member {m.name} ({m.size / 1e6:.0f} MB)", flush=True)
            if name.startswith("cluster_machine_list"):
                machines = pd.read_csv(_member(tf, m))
            elif name.startswith("cluster_gpu_util"):
                # a few rows have more than 10 fields (glued records): read wide, coerce to numbers
                for ch in pd.read_csv(_member(tf, m), chunksize=2_000_000, header=None, skiprows=1,
                                      names=list(range(24)), dtype=str):
                    g = ch.iloc[:, 2:10].apply(pd.to_numeric, errors="coerce")
                    ts = ch[0].astype(str)
                    ch = pd.DataFrame({"machine": ch[1], "hour": ts.str.slice(0, 13),
                                       "tz": ts.str.slice(-3), "util_sum": g.mean(axis=1), "n": 1})
                    ch = ch.dropna(subset=["util_sum"])
                    parts.append(ch.groupby(["machine", "hour", "tz"])[["util_sum", "n"]].sum())
                    print(f"    {sum(len(p) for p in parts):,} partial machine-hours", flush=True)
    s.close()
    h = pd.concat(parts).groupby(level=[0, 1, 2]).sum().reset_index()
    h["gpu_util"] = h["util_sum"] / h["n"]
    h[["machine", "hour", "tz", "gpu_util", "n"]].to_csv(d / "_tmp.csv.gz", index=False)
    (d / "_tmp.csv.gz").rename(dest)
    if machines is not None:
        machines.to_csv(d / "cluster_machine_list.csv", index=False)
    return [str(dest)]


def fetch_azure_llm(out: Path) -> list[str]:
    """Stream the 0.7 GB request log; keep hourly request count and token sums."""
    d = out / "azure_llm"
    dest = d / "code_1week_hourly.csv"
    if dest.exists():
        return [str(dest)]
    d.mkdir(parents=True, exist_ok=True)
    s = _open_stream(AZ_LLM)
    parts = []
    for ch in pd.read_csv(s, chunksize=2_000_000):
        hr = ch["TIMESTAMP"].str.slice(0, 13)
        parts.append(ch.assign(hour=hr, requests=1).groupby("hour")[["requests", "ContextTokens", "GeneratedTokens"]].sum())
    s.close()
    h = pd.concat(parts).groupby(level=0).sum()
    h.to_csv(d / "_tmp.csv")
    (d / "_tmp.csv").rename(dest)
    return [str(dest)]


PM100_SNIPPET = r"""
import sys, pyarrow.parquet as pq, numpy as np, pandas as pd
src, dst = sys.argv[1], sys.argv[2]
pf = pq.ParquetFile(src)
print(pf.schema_arrow, file=sys.stderr)
names = pf.schema_arrow.names
cols = [c for c in ['job_id','start_time','end_time','num_nodes','node_power_consumption','partition'] if c in names]
acc = {}
for b in pf.iter_batches(batch_size=20000, columns=cols):
    df = b.to_pandas()
    st = ((pd.to_datetime(df['start_time'], utc=True) - pd.Timestamp('1970-01-01', tz='UTC')).dt.total_seconds()).fillna(-1).astype('int64')
    for s0, pw in zip(st, df['node_power_consumption']):
        if s0 < 0 or pw is None or len(pw) == 0: continue
        pw = np.asarray(pw, float)
        t = s0 + 20 * np.arange(len(pw))          # PM100: node power sampled every 20 s
        hr = t // 3600
        u, inv = np.unique(hr, return_inverse=True)
        sums = np.bincount(inv, weights=pw)        # W*samples per hour
        for k, v in zip(u, sums):
            acc[k] = acc.get(k, 0.0) + v
s = pd.Series(acc).sort_index()
pd.DataFrame({'hour_unix': s.index * 3600, 'mean_power_w': s.values / 180.0}).to_csv(dst, index=False)
"""


def fetch_pm100(out: Path) -> list[str]:
    d = out / "pm100"
    raw = _download(PM100, d / "job_table.parquet")
    dest = d / "cluster_job_power_hourly.csv"
    if not dest.exists():
        pys = [sys.executable, str(ROOT / ".team-venv" / "bin" / "python"), "python3"]
        for py in pys:
            ok = subprocess.run([py, "-c", "import pyarrow"], capture_output=True).returncode == 0
            if ok:
                subprocess.run([py, "-c", PM100_SNIPPET, str(raw), str(dest)], check=True)
                break
        else:
            print("  pm100: no python with pyarrow found; skipping conversion", flush=True)
            return [str(raw)]
    return [str(raw), str(dest)]


def fetch_mit(out: Path) -> list[str]:
    d = out / "mit_supercloud"
    files = [str(_download(MIT + "LICENSE", d / "LICENSE"))]
    # slurm-log.csv (103 MB) is streamed and reduced to hourly allocated GPUs (disk was nearly full)
    alloc_f = d / "gpu_alloc_hourly.csv"
    if not alloc_f.exists():
        s = pd.read_csv(_open_stream(MIT + "slurm-log.csv"), usecols=["time_start", "time_end", "tres_alloc"])
        s = s[(s["time_start"] > 0) & (s["time_end"] > s["time_start"])]
        tres = s["tres_alloc"].fillna("").str.extractall(r"(\d+)=(\d+)").astype(float)   # some values overflow int64
        tres.columns = ["id", "n"]
        s["gpus"] = tres[tres["id"].isin([1001, 1002])].groupby(level=0)["n"].sum().reindex(s.index).fillna(0)
        s = s[s["gpus"] > 0]
        h0, h1 = s["time_start"] // 3600, s["time_end"] // 3600
        ev = pd.concat([pd.Series(s["gpus"].to_numpy(), h0.to_numpy()),
                        pd.Series(-s["gpus"].to_numpy(), (h1 + 1).to_numpy())])
        alloc = ev.groupby(level=0).sum().sort_index().cumsum()
        alloc = alloc.reindex(range(alloc.index.min(), alloc.index.max() + 1)).ffill()
        alloc.rename_axis("hour_unix_h").rename("gpus_alloc").to_csv(alloc_f)
    files.append(str(alloc_f))
    # sample nvidia-smi job files (power_draw_W @100 ms) to calibrate W per allocated GPU
    cal = d / "gpu_power_calibration.csv"
    if not cal.exists():
        keys = []
        with _client() as c:
            for folder in ("0000", "0001"):
                r = c.get("https://mit-supercloud-dataset.s3.amazonaws.com/",
                          params={"list-type": "2", "prefix": f"datacenter-challenge/202201/gpu/{folder}/"})
                import re
                for k, sz in re.findall(r"<Key>([^<]+)</Key>.*?<Size>(\d+)</Size>", r.text):
                    if k.endswith(".csv") and int(sz) < 4_000_000:
                        keys.append((k, int(sz)))
        rng = np.random.default_rng(0)
        rng.shuffle(keys)
        rows, used = [], 0
        for k, sz in keys:
            if used + sz > MIT_GPU_SAMPLE_BYTES:
                continue
            with _client() as c:
                try:
                    txt = c.get("https://mit-supercloud-dataset.s3.amazonaws.com/" + k).text
                except httpx.HTTPError:
                    continue
            used += sz
            g = pd.read_csv(io.StringIO(txt), usecols=lambda x: x in ("gpu_index", "power_draw_W",
                                                                      "utilization_gpu_pct", "timestamp"))
            if g.empty:
                continue
            rows.append({"key": k, "n_gpus": g["gpu_index"].nunique(), "mean_w": g["power_draw_W"].mean(),
                         "p05_w": g["power_draw_W"].quantile(0.05), "mean_util": g["utilization_gpu_pct"].mean(),
                         "dur_s": g["timestamp"].max() - g["timestamp"].min()})
        pd.DataFrame(rows).to_csv(cal, index=False)
    return files + [str(cal)]


FETCHERS = [fetch_alibaba_2018, fetch_pai, fetch_bitbrains, fetch_azure_vm, fetch_pm100,
            fetch_philly, fetch_mit, fetch_azure_llm]


def fetch(out_dir: Path = OUT) -> list[str]:
    got = []
    for fn in FETCHERS:
        print(f"[fetch] {fn.__name__}", flush=True)
        try:
            got += fn(out_dir)
        except Exception as e:                       # keep going; one dead source must not stop the rest
            print(f"  FAILED {fn.__name__}: {e!r}", flush=True)
    return got


# ----------------------------------------------------------------------------- harmonizers
COLS = ["source", "company", "domain", "hour_utc", "ts_utc", "local_tz", "local_hour_known", "metric",
        "value_raw", "rel_load", "est_power_rel", "workload", "n_units"]


def _finish(df: pd.DataFrame, **const) -> pd.DataFrame:
    """df: domain, hour (int, absolute or relative), value_raw, metric, [ts_utc], [n_units]."""
    df = df.assign(**const).dropna(subset=["value_raw"])
    df["hour_utc"] = df["hour"] - df.groupby("domain")["hour"].transform("min")
    m = df.groupby("domain")["value_raw"].transform("mean")
    df["rel_load"] = df["value_raw"] / m
    met = df["metric"].iloc[0]
    if met == "power":
        df["est_power_rel"] = df["rel_load"]
    else:
        idle = IDLE_FRAC[met]
        u = df["value_raw"].clip(0, 1)
        p = idle + (1 - idle) * u                    # linear server power model (see module docstring)
        df["est_power_rel"] = p / p.groupby(df["domain"]).transform("mean")
    for c in COLS:
        if c not in df:
            df[c] = pd.NA
    return df[COLS]


def h_google(out: Path) -> pd.DataFrame:
    frames = []
    for f in sorted(GOOGLE.glob("*.csv.gz")):
        d = pd.read_csv(f)
        d = d[~d["bad_measurement_data"].astype(bool) & ~d["bad_production_power_data"].astype(bool)]
        hour = ((d["time"] / 1e6 - 600) // 3600).astype(int)       # hours since 2019-05-01 00:00 PT
        h = d.groupby(hour)["measured_power_util"].mean().rename("value_raw").reset_index()
        frames.append(h.rename(columns={"time": "hour"}).assign(domain=f.name.split(".")[0]))
    g = pd.concat(frames)
    g["ts_utc"] = (pd.Timestamp("2019-05-01 07:00", tz="UTC") + pd.to_timedelta(g["hour"], "h")).dt.strftime("%Y-%m-%dT%H:%M")
    df = _finish(g, metric="power", source="google_2019", company="Google", workload="mixed_cloud",
                 local_tz="unknown (trace clock is US/Pacific)", local_hour_known=False, n_units=pd.NA)
    # keep heat_models' shared index: hours since 2019-05-01 00:00 PT (= 07:00 UTC)
    df["hour_utc"] = (pd.to_datetime(df["ts_utc"]) - pd.Timestamp("2019-05-01 07:00")) // pd.Timedelta("1h")
    return df


def h_alibaba_2018(out: Path) -> pd.DataFrame:
    f = out / "alibaba_2018" / "machine_usage_head.tar.gz"
    cols = ["machine", "t", "cpu", "mem", "mem_gps", "mkpi", "net_in", "net_out", "disk"]
    parts = []
    try:
        with tarfile.open(fileobj=gzip.open(f), mode="r|") as tf:
            m = next(iter(tf))
            for ch in pd.read_csv(_member(tf, m), names=cols, usecols=[0, 1, 2], chunksize=2_000_000):
                parts.append(ch.assign(hour=ch["t"] // 3600).groupby(["machine", "hour"])["cpu"].agg(["sum", "count"]))
    except (EOFError, tarfile.ReadError, gzip.BadGzipFile, pd.errors.ParserError) as e:
        print(f"  alibaba_2018: truncated archive ends ({type(e).__name__}); using complete machines", flush=True)
    h = pd.concat(parts).groupby(level=[0, 1]).sum().reset_index()
    last = h["machine"].iloc[-1]
    h = h[h["machine"] != last]                                    # last machine is cut mid-series
    h["u"] = h["sum"] / h["count"] / 100
    ms = sorted(h["machine"].unique(), key=lambda s: int(s.split("_")[1]))
    grp = {m: f"ali18_g{i // GROUP:02d}" for i, m in enumerate(ms)}
    h["domain"] = h["machine"].map(grp)
    a = h.groupby(["domain", "hour"]).agg(value_raw=("u", "mean"), n_units=("machine", "nunique")).reset_index()
    a = a[a["n_units"] >= 0.8 * a.groupby("domain")["n_units"].transform("max")]
    return _finish(a, metric="cpu_util", source="alibaba_2018", company="Alibaba", workload="mixed_cloud",
                   local_tz="unknown (likely Asia/Shanghai)", local_hour_known=False)


def h_pai(out: Path) -> pd.DataFrame:
    d = out / "alibaba_pai_2020"
    spec = pd.read_csv(tarfile.open(d / "pai_machine_spec.tar.gz").extractfile("pai_machine_spec.csv"),
                       names=["machine", "gpu_type", "cap_cpu", "cap_mem", "cap_gpu"])
    hdr = ["worker_name", "machine", "start_time", "end_time", "machine_cpu_iowait", "machine_cpu_kernel",
           "machine_cpu_usr", "machine_gpu", "machine_load_1", "machine_net_receive", "machine_num_worker",
           "machine_cpu"]
    with tarfile.open(d / "pai_machine_metric.tar.gz") as tf:
        mm = pd.read_csv(tf.extractfile("pai_machine_metric.csv"), names=hdr,
                         usecols=["machine", "start_time", "end_time", "machine_gpu"])
    mm = mm.dropna().merge(spec[spec["cap_gpu"] > 0], on="machine")
    mm = mm[mm["end_time"] > mm["start_time"]]
    # machine_gpu is summed over the machine's GPUs (percent; up to 100 * cap_gpu)
    mm["u"] = (mm["machine_gpu"] / (100 * mm["cap_gpu"])).clip(0, 1)
    s, e = mm["start_time"].to_numpy(np.int64), mm["end_time"].to_numpy(np.int64)
    h0, h1 = s // 3600, (e - 1) // 3600
    n = (h1 - h0 + 1)
    idx = np.repeat(np.arange(len(mm)), n)
    hr = h0[idx] + (np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n))
    ov = np.minimum(e[idx], (hr + 1) * 3600) - np.maximum(s[idx], hr * 3600)
    x = pd.DataFrame({"machine": mm["machine"].to_numpy()[idx], "hour": hr, "w": ov,
                      "wu": ov * mm["u"].to_numpy()[idx]})
    x = x.groupby(["machine", "hour"])[["w", "wu"]].sum()
    # machine-level metrics: overlapping instances each report the same machine average, so the
    # overlap-weighted mean is the machine's util in that hour while anything ran; hours inside a
    # machine's active span with no instance running count as idle (u = 0)
    mu = (x["wu"] / x["w"]) * np.minimum(x["w"], 3600) / 3600
    mu = mu.rename("u").reset_index()
    full = []
    for m, g in mu.groupby("machine"):
        r = pd.RangeIndex(g["hour"].min(), g["hour"].max() + 1)
        full.append(g.set_index("hour")["u"].reindex(r, fill_value=0.0).rename_axis("hour").reset_index().assign(machine=m))
    mu = pd.concat(full).merge(spec[["machine", "gpu_type"]], on="machine")
    # domains: ~100 machines of the same GPU type
    mu["k"] = mu.groupby("gpu_type")["machine"].transform(lambda s: s.astype("category").cat.codes // GROUP)
    mu["domain"] = "pai_" + mu["gpu_type"].astype(str) + "_g" + mu["k"].astype(str)
    a = mu.groupby(["domain", "hour"]).agg(value_raw=("u", "mean"), n_units=("machine", "nunique")).reset_index()
    a = a[a["n_units"] >= 20]
    # trace holds jobs submitted inside the window: drop 2-day ramp-in / ramp-out at the edges
    lo, hi = a.groupby("domain")["hour"].transform("min"), a.groupby("domain")["hour"].transform("max")
    a = a[(a["hour"] >= lo + 48) & (a["hour"] <= hi - 48)]
    # timestamps: seconds, shifted so that Unix time read in UTC+8 keeps true time of day & weekday
    a["ts_utc"] = pd.to_datetime(a["hour"] * 3600, unit="s").dt.strftime("%Y-%m-%dT%H:%M")
    return _finish(a, metric="gpu_util", source="alibaba_pai_2020", company="Alibaba", workload="dl_training",
                   local_tz="Asia/Shanghai (fake dates; true hour-of-day and weekday)", local_hour_known=True)


def h_philly(out: Path) -> pd.DataFrame:
    h = pd.read_csv(out / "philly" / "gpu_util_machine_hourly.csv.gz")
    h = h[h["n"] >= 30]                                             # >= 30 of 60 minutes reported
    # trace wall clock is US Pacific, labelled PST/PDT; convert to UTC hours
    t = pd.to_datetime(h["hour"], format="%Y-%m-%d %H", errors="coerce") + pd.to_timedelta(
        h["tz"].map({"PST": 8, "PDT": 7}), "h")
    h = h.assign(hour=(t - pd.Timestamp("1970-01-01")) // pd.Timedelta("1h")).dropna(subset=["hour"])
    h["hour"] = h["hour"].astype(np.int64)
    ms = sorted(h["machine"].unique())
    grp = {m: f"philly_g{i // GROUP:02d}" for i, m in enumerate(ms)}
    h["domain"] = h["machine"].map(grp)
    h["u"] = h["gpu_util"] / 100
    a = h.groupby(["domain", "hour"]).agg(value_raw=("u", "mean"), n_units=("machine", "nunique")).reset_index()
    a = a[a["n_units"] >= 0.5 * a.groupby("domain")["n_units"].transform("median")]
    a["ts_utc"] = pd.to_datetime(a["hour"] * 3600, unit="s").dt.strftime("%Y-%m-%dT%H:%M")
    return _finish(a, metric="gpu_util", source="philly", company="Microsoft", workload="dl_training",
                   local_tz="America/Los_Angeles (trace clock PST/PDT)", local_hour_known=True)


def h_bitbrains(out: Path) -> pd.DataFrame:
    z = zipfile.ZipFile(out / "bitbrains" / "fastStorage.zip")
    names = sorted(n for n in z.namelist() if n.endswith(".csv"))
    rows = []
    for i, n in enumerate(names):
        d = pd.read_csv(z.open(n), sep=r";\s*", engine="python")
        d.columns = [c.strip() for c in d.columns]
        hr = d["Timestamp [ms]"] // 3600                          # header says ms, values are Unix seconds
        g = d.groupby(hr)[["CPU usage [MHZ]", "CPU capacity provisioned [MHZ]"]].mean()
        rows.append(g.assign(vm=i, domain=f"bitbrains_g{i // 125:02d}").rename_axis("hour").reset_index())
    h = pd.concat(rows)
    a = h.groupby(["domain", "hour"]).agg(use=("CPU usage [MHZ]", "sum"), cap=("CPU capacity provisioned [MHZ]", "sum"),
                                          n_units=("vm", "nunique")).reset_index()
    a = a[a["n_units"] >= 0.8 * a.groupby("domain")["n_units"].transform("max")]
    a["value_raw"] = a["use"] / a["cap"]                            # capacity-weighted VM CPU util
    a["ts_utc"] = pd.to_datetime(a["hour"] * 3600, unit="s").dt.strftime("%Y-%m-%dT%H:%M")
    return _finish(a, metric="cpu_util", source="bitbrains", company="Bitbrains", workload="vm_iaas",
                   local_tz="Europe/Amsterdam", local_hour_known=True)


def h_azure_vm(out: Path) -> pd.DataFrame:
    rows = []
    for f in sorted((out / "azure_vm").glob("cpu_readings_head_*.csv.gz")):
        buf = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(f.read_bytes())   # truncated on purpose
        lines = buf.decode(errors="ignore").splitlines()[:-1]
        d = pd.read_csv(io.StringIO("\n".join(lines)), names=["t", "vm", "mn", "mx", "avg"])
        d = d[d["t"] == d["t"].iloc[0]]
        d["domain"] = "azure_v2_h" + (d["vm"].map(lambda v: zlib.crc32(v.encode())) % 3).astype(str)
        rows.append(d.groupby("domain").agg(t=("t", "first"), value_raw=("avg", "mean"), n_units=("vm", "size")).reset_index())
    a = pd.concat(rows)
    a["value_raw"] /= 100
    a["hour"] = a["t"] // 3600
    a["ts_utc"] = ""
    return _finish(a, metric="cpu_util", source="azure_vm", company="Microsoft Azure", workload="vm_iaas",
                   local_tz="unknown (sparse: one 5-min fleet sample per 3.67 h slice)", local_hour_known=False)


def h_azure_llm(out: Path) -> pd.DataFrame:
    h = pd.read_csv(out / "azure_llm" / "code_1week_hourly.csv")
    t = pd.to_datetime(h["hour"], format="%Y-%m-%d %H")
    tok = h["ContextTokens"] + h["GeneratedTokens"]
    a = pd.DataFrame({"domain": "azure_llm_code", "hour": (t - pd.Timestamp("1970-01-01")) // pd.Timedelta("1h"),
                      # demand proxy: hourly tokens / P99 hour, treated like GPU utilization
                      "value_raw": (tok / tok.quantile(0.99)).clip(upper=1).to_numpy(),
                      "ts_utc": t.dt.strftime("%Y-%m-%dT%H:%M"), "n_units": h["requests"]})
    return _finish(a, metric="request_load", source="azure_llm", company="Microsoft Azure", workload="llm_inference",
                   local_tz="UTC timestamps (service region not stated)", local_hour_known=False)


def h_pm100(out: Path) -> pd.DataFrame:
    h = pd.read_csv(out / "pm100" / "cluster_job_power_hourly.csv")
    h = h[h["mean_power_w"] > 0]
    hr = h["hour_unix"] // 3600
    # trim ramp-in/out (jobs that started before / ended after the dataset window)
    keep = h["mean_power_w"] > 0.2 * h["mean_power_w"].median()
    a = pd.DataFrame({"domain": "m100_jobs", "hour": hr, "value_raw": h["mean_power_w"],
                      "ts_utc": pd.to_datetime(h["hour_unix"], unit="s").dt.strftime("%Y-%m-%dT%H:%M")})[keep]
    return _finish(a, metric="power", source="pm100", company="CINECA", workload="hpc",
                   local_tz="Europe/Rome", local_hour_known=True)


def h_mit(out: Path) -> pd.DataFrame:
    d = out / "mit_supercloud"
    alloc = pd.read_csv(d / "gpu_alloc_hourly.csv", index_col=0)["gpus_alloc"]
    # trim edges where the job sample ramps in/out
    alloc = alloc[alloc > 0.25 * alloc.median()]
    cap = alloc.quantile(0.995)
    cal = pd.read_csv(d / "gpu_power_calibration.csv") if (d / "gpu_power_calibration.csv").exists() else None
    a = pd.DataFrame({"domain": "mit_supercloud_gpu", "hour": alloc.index, "value_raw": (alloc / cap).clip(upper=1).to_numpy(),
                      "n_units": alloc.to_numpy(),
                      "ts_utc": pd.to_datetime(alloc.index * 3600, unit="s").strftime("%Y-%m-%dT%H:%M")})
    df = _finish(a, metric="gpu_alloc", source="mit_supercloud", company="MIT Lincoln Lab", workload="hpc",
                 local_tz="America/New_York", local_hour_known=True)
    if cal is not None and len(cal):
        # measured idle/busy watts per GPU from sampled nvidia-smi files replace the 0.3 idle default
        idle_w = cal["p05_w"].median()
        busy_w = np.average(cal["mean_w"], weights=cal["dur_s"].clip(lower=1) * cal["n_gpus"])
        p = idle_w + (busy_w - idle_w) * df["value_raw"]       # allocated GPUs draw busy_w, others idle_w
        df["est_power_rel"] = p / p.mean()
    return df


HARMONIZERS = [h_google, h_alibaba_2018, h_pai, h_philly, h_bitbrains, h_azure_vm, h_azure_llm, h_pm100, h_mit]


def harmonize(out_dir: Path = OUT) -> list[str]:
    frames = []
    for fn in HARMONIZERS:
        try:
            df = fn(out_dir)
            print(f"[harmonize] {fn.__name__}: {len(df):,} domain-hours, {df['domain'].nunique()} domains", flush=True)
            frames.append(df)
        except FileNotFoundError as e:
            print(f"[harmonize] {fn.__name__}: missing input ({e.filename}); skipped", flush=True)
        except Exception as e:
            print(f"[harmonize] {fn.__name__}: FAILED {e!r}", flush=True)
    allh = pd.concat(frames, ignore_index=True)
    for c in ("value_raw", "rel_load", "est_power_rel"):
        allh[c] = allh[c].astype(float).round(5)
    dest = out_dir / "hourly_all.csv.gz"
    tmp = out_dir / "hourly_all.csv.gz.part"
    allh.to_csv(tmp, index=False, compression="gzip")
    tmp.rename(dest)
    print(summary(allh).to_string(), flush=True)
    return [str(dest)]


def summary(allh: pd.DataFrame) -> pd.DataFrame:
    def stats(g):
        day = g["hour_utc"] // 24
        sw = g.groupby([g["domain"], day])["est_power_rel"].agg(lambda x: x.max() - x.min() if len(x) >= 20 else np.nan)
        return pd.Series({"domains": g["domain"].nunique(), "domain_hours": len(g),
                          "p10": g["est_power_rel"].quantile(0.1), "p90": g["est_power_rel"].quantile(0.9),
                          "median_daily_swing": sw.median(),
                          "rel_load_p10": g["rel_load"].quantile(0.1), "rel_load_p90": g["rel_load"].quantile(0.9)})
    return allh.groupby("source").apply(stats).round(3)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    if "harmonize" not in sys.argv[1:]:
        fetch(OUT)
    harmonize(OUT)
