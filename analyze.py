#!/usr/bin/env python3
"""Plot energy spectrum, rate over time and waveforms from detector.py runs.

    .venv/bin/python analyze.py data/salt1.npz data/control1.npz -o report.png

Each .npz holds the pulses (int16, trigger at index `pre`), their timestamps and
the run duration. Peak height is proportional to the deposited energy.
"""
import argparse
import os

import numpy as np

FULL = 32768.0


def load(path):
    d = np.load(path)
    p = d["pulses"].astype(float) / FULL
    pre = int(d["pre"]) if "pre" in d else 0
    fs = float(d["fs"]) if "fs" in d else 48000.0
    t = d["t"] - d["t"][0] if len(d["t"]) else d["t"]
    dur = float(d["duration"]) if "duration" in d else (float(t[-1]) if len(t) else 0.0)
    peak = -p.min(axis=1)   # negative-going pulses
    return dict(name=os.path.splitext(os.path.basename(path))[0], p=p, pre=pre, fs=fs,
                t=t, dur=dur, peak=peak)


def summarize(r):
    n = len(r["peak"])
    rate = n / r["dur"] if r["dur"] else float("nan")
    err = np.sqrt(n) / r["dur"] if r["dur"] else float("nan")
    med = float(np.median(r["peak"])) if n else float("nan")
    print(f"{r['name']:<16} pulses {n:5d}  duration {r['dur']:7.1f} s  "
          f"rate {rate:.3f} +/- {err:.3f} /s  median peak {med:.3f} FS")


def plot(runs, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(2, 2, figsize=(12, 8))
    allpk = np.concatenate([r["peak"] for r in runs if len(r["peak"])] or [np.zeros(1)])
    bins = np.linspace(0, max(allpk.max(), 0.3), 40)
    for r in runs:
        if not len(r["peak"]):
            continue
        h, _ = np.histogram(r["peak"], bins=bins)
        ax[0, 0].step(bins[:-1], h / r["dur"], where="post", label=r["name"])
    ax[0, 0].set(title="Peak-height spectrum", xlabel="peak height (FS)", ylabel="rate per bin (1/s)")
    ax[0, 0].legend()

    for r in runs:
        if not len(r["t"]):
            continue
        edges = np.arange(0, r["dur"] + 30, 30)
        h, _ = np.histogram(r["t"], bins=edges)
        ax[0, 1].step(edges[:-1], h / 30, where="post", label=r["name"])
    ax[0, 1].set(title="Rate over time (30 s bins)", xlabel="time (s)", ylabel="pulses/s")
    ax[0, 1].legend()

    r = max(runs, key=lambda r: len(r["peak"]))
    if len(r["peak"]):
        tt = (np.arange(r["p"].shape[1]) - r["pre"]) / r["fs"] * 1e6
        for w in r["p"][:40]:
            ax[1, 0].plot(tt, w, lw=0.6, alpha=0.5)
        ax[1, 0].set(title=f"First 40 waveforms ({r['name']})", xlabel="time from trigger (us)",
                     ylabel="amplitude (FS)", xlim=(tt[0], tt[min(len(tt) - 1, r['pre'] + 80)]))
        mean = (r["p"] / r["peak"][:, None]).mean(axis=0)
        ax[1, 1].plot(tt, mean)
        ax[1, 1].set(title="Mean waveform, normalized to peak", xlabel="time from trigger (us)",
                     xlim=ax[1, 0].get_xlim())
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    print(f"saved {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("files", nargs="+", help=".npz files written by detector.py")
    ap.add_argument("-o", "--out", default="report.png")
    args = ap.parse_args()
    runs = [load(f) for f in args.files]
    for r in runs:
        summarize(r)
    plot(runs, args.out)


if __name__ == "__main__":
    main()
