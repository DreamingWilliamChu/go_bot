"""Integration tests against the REAL KataGo engine and real kata1 networks.

On a Mac these run with Homebrew's Metal build; in CI/cloud with the official Eigen (CPU) build.
Visit counts are kept small because the Eigen backend is slow; correctness, not strength, is tested.
"""

from __future__ import annotations

import asyncio

import pytest

from gobot.engine.katago import QueryError
from gobot.engine.query import AnalyzeParams, Move, Position, Stone
from gobot.engine.service import EngineService

from .conftest import needs_katago

pytestmark = [pytest.mark.katago]


async def run(svc, pos, params, client="t"):
    return [r async for r in svc.manager.analyze(client, pos, params)]


@pytest.fixture
async def svc(tmp_settings):
    tmp_settings.model = "b18c384-conv"
    s = EngineService(tmp_settings)
    yield s
    await s.manager.shutdown()


@needs_katago("b18c384-conv")
async def test_version_backend_and_model(svc):
    await svc.manager.ensure_started()
    st = svc.manager.status()
    assert st["state"] == "ready"
    assert st["version"] and st["version"][0].isdigit()
    assert st["backend"] and st["backend"] != "unknown"
    assert st["modelId"] == "b18c384-conv"
    assert "b18c384nbt" in (st["modelInternalName"] or st["modelDescription"] or "")


@needs_katago("b18c384-conv")
@pytest.mark.parametrize("size", [9, 13, 19])
async def test_photo_position_initial_player(svc, size):
    c = size // 2
    pos = Position(
        size=size,
        initial_stones=[Stone(color="B", x=c, y=c), Stone(color="W", x=c + 1, y=c)],
        initial_player="W",
        rules="chinese",
        komi=7.5,
    )
    res = await run(svc, pos, AnalyzeParams(max_time=5, max_visits=12))
    final = res[-1]
    assert final["type"] == "final"
    assert final["currentPlayer"] == "W"
    assert 0.0 <= final["blackWinrate"] <= 1.0
    assert len(final["blackOwnership"]) == size * size
    assert final["candidates"], "KataGo returned no candidate moves"
    occupied = {(c, c), (c + 1, c)}
    for cand in final["candidates"]:
        if not cand["pass"]:
            assert (cand["x"], cand["y"]) not in occupied
        assert cand["pv"][0]["color"] == "W"


@needs_katago("b18c384-conv")
async def test_capture_then_play_in_captured_point(svc):
    # White stone at B2 (x=1,y=7 on 9x9) surrounded on three sides; Black captures at the 4th liberty.
    st = [
        Stone(color="W", x=1, y=7),
        Stone(color="B", x=0, y=7),
        Stone(color="B", x=2, y=7),
        Stone(color="B", x=1, y=6),
    ]
    moves = [Move(color="B", x=1, y=8), Move(color="W", x=6, y=2), Move(color="B", x=1, y=7)]
    # B captures W at (1,7) with (1,8); later B plays into the emptied point (1,7): legal only if capture happened.
    pos = Position(size=9, initial_stones=st, initial_player="B", moves=moves)
    res = await run(svc, pos, AnalyzeParams(max_time=5, max_visits=8))
    assert res[-1]["type"] == "final" and res[-1]["currentPlayer"] == "W"
    # Ownership at the captured point should not favour White strongly.
    own = res[-1]["blackOwnership"][7 * 9 + 1]
    assert own > 0


@needs_katago("b18c384-conv")
async def test_illegal_move_is_reported(svc):
    pos = Position(size=9, initial_stones=[Stone(color="B", x=4, y=4)], moves=[Move(color="W", x=4, y=4)])
    with pytest.raises(QueryError):
        await run(svc, pos, AnalyzeParams(max_time=2, max_visits=4))


@needs_katago("b18c384-conv")
async def test_pass_moves(svc):
    pos = Position(
        size=9,
        initial_stones=[Stone(color="B", x=4, y=4)],
        initial_player="W",
        moves=[Move(color="W"), Move(color="B")],
    )
    res = await run(svc, pos, AnalyzeParams(max_time=5, max_visits=8))
    assert res[-1]["currentPlayer"] == "W"


@needs_katago("b18c384-conv")
async def test_rules_and_komi_change_evaluation(svc):
    base = dict(size=9, initial_stones=[Stone(color="B", x=4, y=4)], initial_player="W")
    lo = (await run(svc, Position(**base, komi=0.5), AnalyzeParams(max_time=5, max_visits=16)))[-1]
    hi = (await run(svc, Position(**base, komi=20.5), AnalyzeParams(max_time=5, max_visits=16)))[-1]
    assert lo["blackScoreLead"] > hi["blackScoreLead"] + 10
    jp = (
        await run(svc, Position(**base, rules="japanese", komi=6.5), AnalyzeParams(max_time=5, max_visits=8))
    )[-1]
    assert jp["type"] == "final"


@needs_katago("b18c384-conv")
async def test_cancel_and_stale_with_real_engine(svc):
    pos19 = Position(size=19, initial_stones=[Stone(color="B", x=3, y=3)], initial_player="W")
    pos9 = Position(size=9, initial_stones=[Stone(color="B", x=4, y=4)], initial_player="W")
    old: list = []

    async def first():
        async for r in svc.manager.analyze("c", pos19, AnalyzeParams(max_time=60, report_every=0.2)):
            old.append(r)

    t = asyncio.create_task(first())
    await svc.manager.ensure_started()
    for _ in range(100):
        if old:
            break
        await asyncio.sleep(0.1)
    n = len(old)
    new = await run(svc, pos9, AnalyzeParams(max_time=3, max_visits=6), client="c")
    await asyncio.wait_for(t, 20)
    assert len(old) == n, "superseded analysis must not deliver results"
    assert len(new[-1]["blackOwnership"]) == 81

    # explicit cancel
    got: list = []
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    async for r in svc.manager.analyze("c", pos19, AnalyzeParams(max_time=60, report_every=0.2)):
        got.append(r)
        if len(got) == 1:
            await svc.manager.cancel("c")
    assert got[-1]["type"] == "final" and got[-1]["cancelled"]
    assert loop.time() - t0 < 20


@needs_katago("tf2-b10c384")
async def test_transformer_model_loads(tmp_settings):
    tmp_settings.model = "tf2-b10c384"
    s = EngineService(tmp_settings)
    try:
        res = [
            r
            async for r in s.manager.analyze("t", Position(size=9), AnalyzeParams(max_time=10, max_visits=3))
        ]
        assert res[-1]["type"] == "final"
        assert "transformer" in (s.manager.status()["modelDescription"] or "")
    finally:
        await s.manager.shutdown()
