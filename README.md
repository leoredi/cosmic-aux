# cosmic-aux

Auto-triggering recorder for the [DIY Particle Detector](https://github.com/ozel/DIY_particle_detector)
(Oliver Keller, CERN S'Cool Lab), read through a laptop's 3.5 mm audio input.
Nothing has to be tuned by hand: the script sets the input gain, finds the trigger
level and follows the noise floor while it runs.

Developed on a MacBook Pro (macOS, combo jack). Input-gain control uses `osascript`,
so that part is macOS-only; the rest is plain Python (`--gain N` skips it).

## Setup

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Record

```
.venv/bin/python detector.py                     # electron mode, auto gain + auto trigger
.venv/bin/python detector.py --mode alpha        # 2048-sample window, for alpha spectrometry
.venv/bin/python detector.py --level web         # the web app's fixed level (-8000 / -300)
.venv/bin/python detector.py --level -6000       # any fixed level, 16-bit units
.venv/bin/python detector.py --duration 600 --out run1
.venv/bin/python detector.py --simulate 60       # no hardware needed
```

How it works:

1. **Auto-gain.** Lowers the macOS input volume until the input neither clips nor is
   dominated by noise; your volume is restored on exit.
2. **Calibration (10 s).** Measures DC offset and noise (robust sigma) in the
   detector's resting state, then sweeps the trigger threshold from 6 to 25 sigma and
   picks the knee of the rate curve.
3. **Trigger.** One-sided, negative-going, like the
   [web recorder](https://ozel.github.io/DIY_particle_detector/data_recording_software/webGui/):
   256 samples after the trigger in electron mode, 2048 in alpha mode, plus 16
   pre-trigger samples. Dead time 3 ms. DC and noise floor are tracked on quiet
   blocks, so the level adapts to drift.

Output per run: `<name>.csv` (timestamp, peak in FS and in 16-bit counts) and
`<name>.npz` (timestamps, int16 waveforms with the trigger at index `pre`, `fs`,
`duration`). Peak height is proportional to deposited energy.

## Analyse

```
.venv/bin/python analyze.py data/salt1.npz -o report.png
```

Prints rate and median peak per run and plots the spectrum, rate over time,
waveforms and the mean pulse shape. Several files can be overlaid, e.g. a source
run against a control.

## Hardware notes

- **The sensor must be in the dark.** In room light the photodiodes saturate the
  amplifier and the output is a flat line, which looks like a dead detector. Close it
  in an opaque metal box. The 9V supply should be a battery.
- Moving the board or cable produces spikes; keep it still while recording.
- Resting noise on the test unit: sigma ~0.035-0.045 FS at input volume 10-15,
  Gaussian (kurtosis 3.0), mostly 5-15 kHz.
- `tools/cover_test.py` and `tools/wiggle_test.py` are the diagnostics used to find
  the light and loose-contact effects.

## First measurements

Potassium-chloride salt substitute (K-40 betas) next to the sensor, electron mode,
auto trigger at 6 sigma (`data/`):

| Run | Pulses | Duration | Rate |
|---|---|---|---|
| `salt1` | 46 | 120 s | 0.38/s |
| `control1` (no salt) | 0 | 120 s | 0.00/s |

`salt1.npz` was recorded before the pre-trigger samples were added (it has none) and
has no `duration` field, so `analyze.py` estimates it from the first to last pulse.

## Tests

```
.venv/bin/python -m pytest
```

## Credits

Detector design: Oliver Keller, CERN S'Cool Lab, open hardware
([repo](https://github.com/ozel/DIY_particle_detector)). This repository contains
only the readout software and was written independently; it is MIT licensed.
