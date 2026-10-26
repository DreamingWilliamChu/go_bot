"""Real training: gradient updates, checkpoints, resume, ONNX parity, self-play smoke (needs torch)."""

import random
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from gobot.variant.config import VariantConfig  # noqa: E402
from gobot.variant.models.features import Raw, encode, raw_from_state  # noqa: E402
from gobot.variant.models.net_onnx import OnnxEvaluator  # noqa: E402
from gobot.variant.observation import observe  # noqa: E402
from gobot.variant.training.lockstep import Player, run_games  # noqa: E402
from gobot.variant.training.net import NetConfig  # noqa: E402
from gobot.variant.training.replay import ReplayBuffer, make_chunk  # noqa: E402
from gobot.variant.training.train import TrainConfig, Trainer  # noqa: E402

pytestmark = pytest.mark.train
TINY = NetConfig(blocks=2, channels=16, head_channels=8, value_hidden=16)


def selfplay_chunk(evaluator, games: int = 4, size: int = 7) -> dict:
    cfg = VariantConfig(
        size=size, black_bonuses=1, white_bonuses=1, horizon=6, max_placements=3 * size * size
    )
    pl = Player("p", evaluator, visits=12, temperature_moves=8)
    outs = run_games([(cfg, 100 + i, pl, pl) for i in range(games)], record=True)
    raws, pols, vals, scores, actors = [], [], [], [], []
    for o in outs:
        for smp in o.samples:
            raws.append(smp.raw)
            pols.append(smp.policy)
            vals.append(np.nan if o.black_value is None else o.black_value)
            scores.append(np.nan if o.truncated else o.lead / (size * size))
            actors.append(smp.actor)
    return make_chunk(raws, pols, vals, scores, actors=actors, kind=0, stage=0, generation=0)


@pytest.fixture
def exported(tmp_path: Path):
    tr = Trainer(TINY, TrainConfig(batch_size=32, device="cpu"), seed=1)
    path = tr.export_onnx(tmp_path / "m0" / "model.onnx", 7, {"id": "m0", "sizes": [7]})
    return tr, path


def test_onnx_export_matches_torch_and_evaluator_masks(exported) -> None:
    tr, path = exported
    ev = OnnxEvaluator(path)
    from gobot.variant.env import VariantGame

    g = VariantGame.new(VariantConfig(size=7, black_bonuses=1, white_bonuses=1, horizon=5), seed=4)
    rng = random.Random(0)
    states = []
    for _ in range(20):
        states.append(g.state)
        g.play(rng.choice(g.state.legal_actions()[:-1]))
    raw = Raw.concat([raw_from_state(s) for s in states])
    sp, gl, legal = encode(raw)
    tr.model.eval()
    with torch.no_grad():
        tl, tv, ts = tr.model(torch.from_numpy(sp), torch.from_numpy(gl))
    ol, ov, os_, _ = ev.run(raw)
    assert np.abs(ol - tl.numpy()).max() < 1e-4 and np.abs(ov - tv.numpy()).max() < 1e-4
    evs = ev.evaluate([observe(s) for s in states])
    for e, m in zip(evs, legal, strict=True):
        assert e.priors[~m].sum() == 0 and e.priors.sum() == pytest.approx(1.0, abs=1e-5)
        assert 0 <= e.black_win <= 1


def test_real_updates_checkpoint_and_exact_resume(exported, tmp_path: Path) -> None:
    tr, path = exported
    buf = ReplayBuffer(tmp_path / "replay", 100_000, 10**9)
    buf.add(selfplay_chunk(OnnxEvaluator(path)), "gen000_w0")
    data = buf.load()
    assert len(data) > 20 and np.isfinite(data.value).any()
    before = {k: v.clone() for k, v in tr.model.state_dict().items()}
    first = tr.train_steps(data, 3)
    changed = [
        k
        for k, v in tr.model.state_dict().items()
        if v.dtype.is_floating_point and not torch.equal(v, before[k])
    ]
    assert changed and all(np.isfinite(m["loss"]) for m in first) and tr.skipped == 0
    ck = tmp_path / "ck.pt"
    tr.save(ck)
    cont = tr.train_steps(data, 4)
    resumed = Trainer(TINY, TrainConfig(batch_size=32, device="cpu"), seed=999)
    resumed.load(ck)
    again = resumed.train_steps(data, 4)
    assert [round(m["loss"], 5) for m in again] == [round(m["loss"], 5) for m in cont]
    assert resumed.step == tr.step == 7
    for k, v in tr.model.state_dict().items():
        assert torch.allclose(v, resumed.model.state_dict()[k], atol=1e-6), k


def test_overfits_a_small_batch(exported, tmp_path: Path) -> None:
    tr, path = exported
    buf = ReplayBuffer(tmp_path / "replay", 100_000, 10**9)
    buf.add(selfplay_chunk(OnnxEvaluator(path), games=2), "c")
    data = buf.load()
    tr.cfg.symmetries = False
    start = tr.evaluate_loss(data)["loss"]
    tr.train_steps(data, 60)
    end = tr.evaluate_loss(data)["loss"]
    assert end < 0.7 * start, (start, end)


def test_interrupted_training_resumes(tmp_path: Path) -> None:
    """SIGINT a training subprocess: it saves a checkpoint, and training continues from it."""
    script = Path(__file__).with_name("_train_child.py")
    p = subprocess.Popen([sys.executable, str(script), str(tmp_path)], stdout=subprocess.PIPE, text=True)
    assert p.stdout is not None
    line = p.stdout.readline()
    assert line.startswith("training"), line
    p.send_signal(subprocess.signal.SIGINT)  # type: ignore[attr-defined]
    out, _ = p.communicate(timeout=120)
    assert p.returncode == 130, out
    assert (tmp_path / "ck.pt").is_file()
    tr = Trainer(TINY, TrainConfig(batch_size=16, device="cpu"))
    tr.load(tmp_path / "ck.pt")
    assert tr.step > 0
