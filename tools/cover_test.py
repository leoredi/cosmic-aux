#!/usr/bin/env python3
"""Light-leak test: records the noise level in three phases and tells you on
screen when to cover / uncover the sensor.

    ! .venv/bin/python cover_test.py
"""
import subprocess
import sys

import numpy as np
import sounddevice as sd

FS = 48000
GAIN = 10          # fixed macOS input volume during the test
PHASES = [("UNCOVERED (baseline)", 10), ("COVER THE SENSOR NOW  (opaque + metal)", 10),
          ("UNCOVER THE SENSOR NOW", 10)]


def osa(script):
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True).stdout.strip()


old = osa("input volume of (get volume settings)")
osa(f"set volume input volume {GAIN}")
results = []
try:
    print("Get ready: sensor UNCOVERED. Starting in 5 s...")
    sd.sleep(5000)
    with sd.InputStream(samplerate=FS, channels=1, dtype="float32") as st:
        st.read(FS)  # discard 1 s while gain settles
        for name, secs in PHASES:
            print(f"\n{'=' * 60}\n>>> {name}  -  {secs} s\n{'=' * 60}")
            stds = []
            for s in range(secs):
                x, _ = st.read(FS)
                x = x[:, 0]
                stds.append(float(x.std()))
                print(f"  {s + 1:2d}s  std {x.std():.4f}  min {x.min():+.3f}  max {x.max():+.3f}")
            results.append((name.split("  ")[0], float(np.median(stds))))
finally:
    if old:
        osa(f"set volume input volume {old}")

print("\n" + "=" * 60 + "\nSUMMARY (median noise std per phase)")
for n, v in results:
    print(f"  {n:<28} {v:.4f}")
a, b, c = (v for _, v in results)
if b < 0.5 * min(a, c):
    print("\n=> Noise DROPS when covered: it is LIGHT. Make the enclosure light-tight.")
elif b > 0.8 * min(a, c):
    print("\n=> Noise does NOT depend on light: suspect the board / supply / wiring.")
else:
    print("\n=> Partial drop: light is part of it, but the board is noisy too.")
