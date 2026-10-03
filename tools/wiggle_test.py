#!/usr/bin/env python3
"""Live noise meter for finding a loose connection: wiggle one thing at a time
(battery clip, +9V wire, signal wire, 3.5 mm plug) and watch the bar jump.

    ! .venv/bin/python wiggle_test.py        # Ctrl-C to stop
"""
import subprocess

import numpy as np
import sounddevice as sd

FS = 48000
GAIN = 10


def osa(script):
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True).stdout.strip()


old = osa("input volume of (get volume settings)")
osa(f"set volume input volume {GAIN}")
print("Live noise meter (0.25 s steps). Wiggle ONE part at a time. Ctrl-C to stop.\n")
try:
    with sd.InputStream(samplerate=FS, channels=1, dtype="float32") as st:
        st.read(FS)
        while True:
            x, _ = st.read(FS // 4)
            x = x[:, 0]
            s, pk = float(x.std()), float(np.abs(x).max())
            bar = "#" * min(60, int(s / 0.0015))
            state = "silent" if s < 0.001 else "NOISE "
            print(f"{state} std {s:.4f} peak {pk:5.2f} |{bar}")
except KeyboardInterrupt:
    pass
finally:
    if old:
        osa(f"set volume input volume {old}")
