#!/usr/bin/env python3
"""Auto-triggering reader for the DIY Particle Detector (O. Keller, CERN S'Cool Lab).

Mirrors the trigger of Keller's web recorder (ozel.github.io/DIY_particle_detector):
one-sided, negative-going pulses, a fixed-length waveform captured per trigger
(256 samples in electron mode, 2048 in alpha mode), peak height = pulse energy.

Unlike the web app, nothing has to be tuned by hand:
  1. the macOS input gain is lowered automatically until the input does not clip
     (original volume restored on exit),
  2. the trigger level is found by a threshold sweep on a baseline recording
     and then tracked adaptively as the noise floor drifts,
  3. or pass --level web / --level -8000 for the web app's fixed levels.

    .venv/bin/python detector.py                      # electron mode, auto level
    .venv/bin/python detector.py --mode alpha
    .venv/bin/python detector.py --level web          # -8000 (electron) / -300 (alpha)
    .venv/bin/python detector.py --simulate 60        # test without hardware
"""
import argparse
import csv
import datetime as dt
import queue
import subprocess
import sys
import time

import numpy as np

FS = 48000
BLOCK = 1024
FULL = 32768.0   # web app counts in 16-bit PCM units
PRE = 16         # pre-trigger samples saved with every pulse

MODES = {  # window samples, web-app default level, web-app step
    "electron": dict(win=256, level=-8000),
    "alpha": dict(win=2048, level=-300),
}


def get_input_volume():
    try:
        out = subprocess.run(["osascript", "-e", "input volume of (get volume settings)"],
                             capture_output=True, text=True, check=True).stdout.strip()
        return int(out)
    except (subprocess.SubprocessError, ValueError, FileNotFoundError):
        return None


def set_input_volume(v):
    try:
        subprocess.run(["osascript", "-e", f"set volume input volume {int(v)}"],
                       capture_output=True, check=True)
    except (subprocess.SubprocessError, FileNotFoundError):
        print("warning: could not set input volume", file=sys.stderr)


def robust_sigma(x):
    """Noise sigma from the median absolute deviation (ignores pulses)."""
    return 1.4826 * float(np.median(np.abs(x - np.median(x))))


def trig_signal(x, polarity):
    """Signal oriented so that a pulse is a positive excursion."""
    if polarity == "neg":
        return -x
    if polarity == "pos":
        return x
    return np.abs(x)


def count_crossings(y, thr, refractory):
    idx = np.flatnonzero(y > thr)
    if idx.size == 0:
        return 0
    return 1 + int(np.sum(np.diff(idx) > refractory))


def sweep_threshold(y, sigma, refractory, max_false_hz, kmin=6.0, kmax=25.0):
    """Sweep the threshold in units of sigma and return the lowest one at the
    knee of the rate curve, plus the table.

    Noise crossings fall off steeply with threshold while real pulses form a
    plateau, so the knee is the lowest k whose rate is within 20% of the
    plateau rate (rate at kmax) plus max_false_hz of slack. Small real pulses
    are kept even if the baseline already contains particle hits."""
    secs = y.size / FS
    ks = np.arange(kmin, kmax + 0.5, 0.5)
    table = [(float(k), count_crossings(y, k * sigma, refractory) / secs) for k in ks]
    target = 1.2 * table[-1][1] + max_false_hz
    chosen = next((k for k, r in table if r <= target), kmax)
    return chosen, table


class Detector:
    """Streaming pulse detector. Each pulse is saved as PRE + win samples, with
    the trigger at index PRE (zero-padded on the left at the start of a run)."""

    def __init__(self, win, dead, polarity, adaptive, k, pre=PRE):
        self.win, self.dead, self.polarity, self.pre = win, dead, polarity, pre
        self.adaptive, self.k = adaptive, k
        self.sigma, self.thr, self.baseline = 1e-3, 0.0, 0.0
        self.pos = 0            # absolute sample index of block start
        self.dead_until = 0
        self.hist = np.zeros(pre, dtype=np.float32)   # last `pre` samples, baseline removed
        self.pending = None     # dict(start, need, segs)
        self.times, self.pulses, self.peaks = [], [], []

    @property
    def count(self):
        return len(self.peaks)

    def _emit(self):
        p = self.pending
        self.pending = None
        wave = np.concatenate(p["segs"])
        y = trig_signal(wave, self.polarity)
        self.dead_until = p["start"] + self.win + self.dead
        self.peaks.append(float(y.max()))
        self.times.append(p["start"])
        self.pulses.append(np.clip(np.round(wave * FULL), -32768, 32767).astype(np.int16))

    def process(self, raw):
        base = self.pos
        self.pos += raw.size
        x = raw - self.baseline
        y = trig_signal(x, self.polarity)
        # slow tracking of DC offset and noise floor, only on quiet blocks
        if self.pending is None and base >= self.dead_until and y.max() <= self.thr:
            self.baseline = 0.99 * self.baseline + 0.01 * float(np.median(raw))
            self.sigma = 0.995 * self.sigma + 0.005 * robust_sigma(x)
            if self.adaptive:
                self.thr = self.k * self.sigma
        pos = 0
        while pos < x.size:
            if self.pending:
                p = self.pending
                take = min(p["need"], x.size - pos)
                p["segs"].append(x[pos:pos + take])
                p["need"] -= take
                pos += take
                if p["need"] == 0:
                    self._emit()
                continue
            s = max(pos, self.dead_until - base)
            if s >= x.size:
                break
            cand = np.flatnonzero(y[s:] > self.thr)
            if cand.size == 0:
                break
            i = s + int(cand[0])
            pre = np.concatenate([self.hist, x[:i]])[-self.pre:]
            self.pending = dict(start=base + i, need=self.win, segs=[pre])
            pos = i
        self.hist = np.concatenate([self.hist, x])[-self.pre:]


def auto_gain(blocks, start, floor=5, max_sigma=0.05):
    """Lower the macOS input volume until the input neither clips nor is
    dominated by noise. Returns the chosen volume."""
    for g in [v for v in (50, 35, 25, 15, 10, floor) if v <= start] or [floor]:
        set_input_volume(g)
        for _ in range(int(0.5 * FS / BLOCK)):   # let the new gain settle
            next(blocks)
        x = np.concatenate([next(blocks) for _ in range(int(1.0 * FS / BLOCK))])
        s = robust_sigma(x)
        print(f"auto-gain: volume {g:3d}  sigma {s:.4f}  peak {np.abs(x).max():.3f}")
        if np.abs(x).max() < 0.9 and s <= max_sigma:
            return g
    return floor


def calibrate(blocks, args, polarity):
    n = int(args.calib_s * FS / BLOCK)
    x = np.concatenate([next(blocks) for _ in range(n)])
    if np.abs(x).max() >= 0.98:
        print("warning: input clipping during calibration - signal too large for this gain",
              file=sys.stderr)
    dc = float(np.median(x))
    x = x - dc
    sigma = max(robust_sigma(x), 1e-6)
    print(f"baseline: sigma = {sigma:.5f} FS ({sigma * FULL:.0f} counts), DC = {dc:.5f}")
    k, table = None, None
    if args.level == "auto":
        refractory = int(args.dead_ms * FS / 1000)
        k, table = sweep_threshold(trig_signal(x, polarity), sigma, refractory, args.max_false_hz)
        print("trigger sweep (threshold in sigma -> baseline trigger rate):")
        for kk, rate in table[::2]:
            print(f"  {kk:5.1f} sigma  {rate:8.3f} Hz{'  <== chosen' if kk == k else ''}")
    return k, sigma, dc


def live_blocks(device):
    import sounddevice as sd
    q = queue.Queue()

    def cb(indata, frames, time_info, status):
        if status:
            print(status, file=sys.stderr)
        q.put(indata[:, 0].copy())

    stream = sd.InputStream(samplerate=FS, channels=1, blocksize=BLOCK, device=device,
                            dtype="float32", callback=cb)

    def gen():
        with stream:
            while True:
                yield q.get()
    return gen()


def simulated_blocks(seconds, seed=0):
    """Noise + DC offset + occasional negative-going pulses of varying height."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(0.4e-3 * FS)) / FS
    shape = -np.exp(-t / 0.1e-3) * (1 - np.exp(-t / 0.02e-3))
    shape /= np.abs(shape).max()
    for _ in range(int(seconds * FS / BLOCK)):
        x = rng.normal(0, 0.002, BLOCK) + 0.01
        if rng.random() < 3.0 * BLOCK / FS:   # ~3 pulses / s
            h = rng.uniform(0.02, 0.4)
            at = rng.integers(0, BLOCK - shape.size)
            x[at:at + shape.size] += h * shape
        yield x.astype(np.float32)


def histogram(peaks, bins=15, width=40):
    if not peaks:
        return
    h, edges = np.histogram(peaks, bins=bins)
    m = h.max()
    print("\npeak-height histogram (full-scale units):")
    for c, lo, hi in zip(h, edges[:-1], edges[1:]):
        print(f"  {lo:6.3f}-{hi:6.3f} | {'#' * int(width * c / m):<{width}} {c}")


def parse_level(s):
    if s in ("auto", "web"):
        return s
    try:
        return int(s)
    except ValueError:
        raise argparse.ArgumentTypeError("level must be 'auto', 'web' or an integer in 16-bit units")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--mode", choices=MODES, default="electron",
                    help="electron: 256-sample window; alpha: 2048-sample window")
    ap.add_argument("--polarity", choices=("neg", "pos", "both"), default="neg",
                    help="pulse direction to trigger on (default neg, as in the web app)")
    ap.add_argument("--level", type=parse_level, default="auto",
                    help="'auto' (sweep + adaptive), 'web' (mode default) or fixed level in 16-bit units, e.g. -8000")
    ap.add_argument("--device", help="input device index or name substring (default: system input)")
    ap.add_argument("--list", action="store_true", help="list audio devices and exit")
    ap.add_argument("--simulate", type=float, metavar="SECONDS", help="run on synthetic data")
    ap.add_argument("--gain", type=int, help="fixed macOS input volume 0-100 (default: automatic)")
    ap.add_argument("--calib-s", type=float, default=10.0, help="baseline length in seconds")
    ap.add_argument("--max-false-hz", type=float, default=0.1,
                    help="slack in the sweep for noise triggers (default 0.1 Hz)")
    ap.add_argument("--dead-ms", type=float, default=3.0, help="dead time after a captured pulse")
    ap.add_argument("--duration", type=float, help="stop after this many seconds (live mode)")
    ap.add_argument("--out", default=None, help="output base name (default events_<timestamp>)")
    args = ap.parse_args()

    if args.list:
        import sounddevice as sd
        print(sd.query_devices())
        return

    device = args.device
    if device is not None and device.isdigit():
        device = int(device)

    mode = MODES[args.mode]
    level = mode["level"] if args.level == "web" else args.level
    base_name = args.out or f"events_{dt.datetime.now():%Y%m%d_%H%M%S}"
    f = open(base_name + ".csv", "w", newline="")
    writer = csv.writer(f)
    writer.writerow(["time", "peak_fs", "peak_counts"])

    old_volume = None
    det = None
    try:
        blocks = simulated_blocks(args.simulate + args.calib_s + 5) if args.simulate else live_blocks(device)
        if not args.simulate:
            old_volume = get_input_volume()
            if args.gain is not None:
                set_input_volume(args.gain)
            else:
                auto_gain(blocks, start=50)
        print(f"calibrating for {args.calib_s:.0f} s - keep the detector in its normal state...")
        k, sigma, dc = calibrate(blocks, args, args.polarity)
        adaptive = isinstance(level, str)
        det = Detector(mode["win"], int(args.dead_ms * FS / 1000), args.polarity, adaptive, k)
        det.sigma, det.baseline = sigma, dc
        det.thr = k * sigma if adaptive else abs(level) / FULL
        if not adaptive and det.thr < 5 * sigma:
            print(f"warning: fixed level {level} is only {det.thr / sigma:.1f} sigma above the noise",
                  file=sys.stderr)
        t0 = dt.datetime.now().astimezone()
        desc = f"{k:.1f} sigma, adaptive" if adaptive else f"fixed {level} counts"
        print(f"\nrunning {args.mode} mode: trigger {'-' if args.polarity == 'neg' else ''}"
              f"{det.thr:.5f} FS ({desc}), window {mode['win']} samples. "
              f"Writing {base_name}.csv/.npz. Ctrl-C to stop.")

        start = last = time.time()
        for raw in blocks:
            det.process(raw)
            now = time.time()
            if not args.simulate and now - last >= 1.0:
                last = now
                el = now - start
                print(f"\r{el:7.0f}s  counts {det.count:6d}  rate {det.count / el:6.2f}/s  "
                      f"thr {det.thr:.4f}  noise {det.sigma:.4f}", end="", flush=True)
                if args.duration and el >= args.duration:
                    break
    except (KeyboardInterrupt, StopIteration):
        pass
    finally:
        if old_volume is not None:
            set_input_volume(old_volume)
        if det is not None:
            for s, pk in zip(det.times, det.peaks):
                writer.writerow([(t0 + dt.timedelta(seconds=s / FS)).isoformat(timespec="microseconds"),
                                 f"{pk:.6f}", f"{pk * FULL:.0f}"])
            if det.pulses:
                np.savez_compressed(base_name + ".npz",
                                    t=np.array([(t0 + dt.timedelta(seconds=s / FS)).timestamp()
                                                for s in det.times]),
                                    pulses=np.stack(det.pulses), pre=det.pre, fs=FS,
                                    duration=det.pos / FS)
        f.close()
    if det is not None:
        print(f"\n{det.count} pulses logged to {base_name}.csv")
        histogram(det.peaks)


if __name__ == "__main__":
    main()
