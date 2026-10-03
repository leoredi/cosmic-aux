import csv
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import detector as d  # noqa: E402


def run(blocks, polarity="neg", win=256, k=6.0):
    det = d.Detector(win, int(0.003 * d.FS), polarity, True, k)
    blocks = list(blocks)
    cal = np.concatenate(blocks[:200])
    det.baseline = float(np.median(cal))
    det.sigma = d.robust_sigma(cal - det.baseline)
    det.thr = k * det.sigma
    for b in blocks[200:]:
        det.process(b)
    return det


def test_finds_simulated_pulses():
    det = run(d.simulated_blocks(70))
    assert 140 < det.count < 220          # ~3 pulses/s for 60 s
    assert all(w.shape == (d.PRE + 256,) for w in det.pulses)


def test_no_triggers_on_pure_noise():
    rng = np.random.default_rng(1)
    noise = (rng.normal(0, 0.01, d.BLOCK).astype(np.float32) for _ in range(1500))
    assert run(noise).count == 0


def test_trigger_sits_at_pre_index_and_polarity():
    rng = np.random.default_rng(2)
    blocks = [rng.normal(0, 0.002, d.BLOCK).astype(np.float32) for _ in range(300)]
    blocks[250][500] -= 0.5                      # one negative spike
    det = run(blocks)
    assert det.count == 1
    assert det.pulses[0][d.PRE] < -0.4 * d.FULL
    pos = run([b * -1 for b in blocks], polarity="pos")
    assert pos.count == 1


def test_pulse_spanning_block_boundary():
    rng = np.random.default_rng(3)
    blocks = [rng.normal(0, 0.002, d.BLOCK).astype(np.float32) for _ in range(300)]
    blocks[250][-3:] -= 0.5
    blocks[251][:5] -= 0.3
    det = run(blocks)
    assert det.count == 1 and len(det.pulses[0]) == d.PRE + 256


def test_sweep_picks_knee():
    rng = np.random.default_rng(4)
    x = rng.normal(0, 0.01, d.FS * 10)
    k, table = d.sweep_threshold(-x, 0.01, 144, 0.1)
    assert k >= 6.0 and table[0][0] == 6.0
