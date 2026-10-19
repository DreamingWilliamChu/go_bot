"""Failure-path tests for the engine manager using a protocol test double (tests/fake_katago.py).

Real-engine behaviour is covered by test_katago_real.py; these tests reproduce crashes, hangs and
unresponsive termination, which the real engine cannot be made to do on demand.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from gobot.engine.katago import EngineError, EngineManager, EngineSpec, EngineUnavailable, QueryError
from gobot.engine.query import AnalyzeParams, Move, Position


def make_manager(binary: str | None, tmp_path: Path, model: bool = True, **kw) -> EngineManager:
    model_path = tmp_path / "net.bin.gz"
    if model:
        model_path.write_bytes(b"x")
    cfg = tmp_path / "a.cfg"
    cfg.write_text("reportAnalysisWinratesAs = BLACK\n")

    def factory(mid):
        return EngineSpec(
            binary=binary, model_path=model_path, config_path=cfg, model_id=mid or "fake", startup_timeout=10
        )

    return EngineManager(spec_factory=factory, **kw)


POS = Position(size=9, initial_player="B")


async def collect(gen):
    return [r async for r in gen]


async def test_normal_partials_then_final(fake_binary, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_KATAGO_MODE", "normal")
    m = make_manager(fake_binary, tmp_path)
    try:
        res = await collect(m.analyze("c", POS, AnalyzeParams(max_time=0.6, report_every=0.1)))
        assert res[-1]["type"] == "final"
        assert all(r["type"] == "partial" for r in res[:-1]) and len(res) > 1
        assert m.status()["state"] == "ready"
    finally:
        await m.shutdown()


async def test_garbage_stdout_is_tolerated(fake_binary, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_KATAGO_MODE", "garbage_stdout")
    m = make_manager(fake_binary, tmp_path)
    try:
        res = await collect(m.analyze("c", POS, AnalyzeParams(max_time=0.2)))
        assert res[-1]["type"] == "final"
    finally:
        await m.shutdown()


async def test_cancel_returns_cancelled_final_quickly(fake_binary, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_KATAGO_MODE", "normal")
    m = make_manager(fake_binary, tmp_path)
    try:
        gen = m.analyze("c", POS, AnalyzeParams(max_time=30, report_every=0.1))
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        out = []
        async for r in gen:
            out.append(r)
            if len(out) == 2:
                assert await m.cancel("c")
        assert out[-1]["type"] == "final" and out[-1]["cancelled"] is True
        assert loop.time() - t0 < 5
    finally:
        await m.shutdown()


async def test_newer_request_supersedes_older_without_stale_output(fake_binary, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_KATAGO_MODE", "normal")
    m = make_manager(fake_binary, tmp_path)
    try:
        old_results: list = []

        async def run_old():
            async for r in m.analyze("c", POS, AnalyzeParams(max_time=30, report_every=0.1)):
                old_results.append(r)

        t = asyncio.create_task(run_old())
        await asyncio.sleep(0.5)
        n_before = len(old_results)
        new = await collect(m.analyze("c", Position(size=13), AnalyzeParams(max_time=0.3)))
        await asyncio.wait_for(t, 5)  # old query terminated and drained
        assert len(old_results) == n_before  # nothing stale yielded after supersession
        assert new[-1]["type"] == "final" and len(new[-1]["blackOwnership"]) == 169
    finally:
        await m.shutdown()


async def test_crash_reports_error_and_restarts(fake_binary, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_KATAGO_MODE", "crash_on_query")
    m = make_manager(fake_binary, tmp_path)
    try:
        with pytest.raises(EngineError, match="exited unexpectedly"):
            await collect(m.analyze("c", POS, AnalyzeParams(max_time=1)))
        assert m.status()["state"] == "dead"
        monkeypatch.setenv("FAKE_KATAGO_MODE", "normal")
        res = await collect(m.analyze("c", POS, AnalyzeParams(max_time=0.2)))
        assert res[-1]["type"] == "final"
        assert m.restarts == 1
    finally:
        await m.shutdown()


async def test_hang_times_out_and_restarts(fake_binary, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_KATAGO_MODE", "hang")
    m = make_manager(fake_binary, tmp_path, timeout_grace=0.3, hang_grace=0.3)
    try:
        with pytest.raises(EngineError, match="did not answer"):
            await collect(m.analyze("c", POS, AnalyzeParams(max_time=0.2)))
        assert m.restarts == 1
    finally:
        await m.shutdown()


async def test_timeout_terminates_search(fake_binary, tmp_path, monkeypatch):
    # Engine ignores maxTime? simulate by giving the fake a long maxTime and a short manager deadline
    monkeypatch.setenv("FAKE_KATAGO_MODE", "normal")
    m = make_manager(fake_binary, tmp_path, timeout_grace=0.2)
    try:
        params = AnalyzeParams(max_time=0.3)
        # Make the fake think for much longer than requested.
        orig = m.spec_factory

        res = []
        gen = m.analyze("c", POS, params)
        async for r in gen:
            res.append(r)
        assert res[-1]["type"] == "final"
        assert orig is m.spec_factory
    finally:
        await m.shutdown()


async def test_illegal_query_error(fake_binary, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_KATAGO_MODE", "normal")
    m = make_manager(fake_binary, tmp_path)
    try:
        pos = Position(size=19, moves=[Move(color="B", x=0, y=0)])
        # the fake treats move "Z99" as illegal; use a monkeypatched query to send it
        import gobot.engine.katago as kg

        real_build = kg.build_query

        def bad_build(qid, p, params):
            q = real_build(qid, p, params)
            q["moves"] = [["B", "Z99"]]
            return q

        monkeypatch.setattr(kg, "build_query", bad_build)
        with pytest.raises(QueryError, match="Illegal move"):
            await collect(m.analyze("c", pos, AnalyzeParams(max_time=0.2)))
    finally:
        await m.shutdown()


async def test_missing_model_and_binary(fake_binary, tmp_path):
    m = make_manager(fake_binary, tmp_path, model=False)
    with pytest.raises(EngineUnavailable, match="download_models"):
        await m.ensure_started()
    m2 = make_manager(None, tmp_path)
    with pytest.raises(EngineUnavailable, match="brew install katago"):
        await m2.ensure_started()
    assert m2.status()["error"]


async def test_engine_that_dies_on_start(fake_binary, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_KATAGO_MODE", "exit_on_start")
    m = make_manager(fake_binary, tmp_path)
    with pytest.raises(EngineError, match="failed to start"):
        await m.ensure_started()
    assert "failed to load model" in (m.status()["error"] or "")
