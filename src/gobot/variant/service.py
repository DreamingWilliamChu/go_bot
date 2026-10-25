"""Bonus-search analysis for the web app, run in a separate worker process.

The search is plain Python and would hold the GIL; running it inside uvicorn would stall the event
loop, the KataGo reader and photo uploads. One worker process (spawned on first use, gone after
``IDLE_SECONDS`` without work) owns the evaluators - including the KataGo evaluator's own ``katago``
process - and runs one search at a time. Only a pickled public ``VariantState`` crosses the process
boundary: the hidden schedule never leaves the web process's game store.

Results use the same JSON shape as KataGo analysis (``engine/query.py``), plus ``source``,
``evaluator``, ``budget`` and ``rules``, and PV steps may carry ``turnStart`` (a chance outcome).
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import multiprocessing as mp
import queue
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gobot.go.coords import xy_to_gtp
from gobot.variant.search.mcts import SearchParams, SearchResult
from gobot.variant.state import VariantState

log = logging.getLogger("gobot.variant.service")

IDLE_SECONDS = 600.0
MAX_SECONDS = 120.0
TOP_N = 10


def rules_info(state: VariantState) -> dict[str, Any]:
    cfg = state.config
    return {
        "variant": "bonus-turn",
        "scoring": "area (Chinese), board as it stands",
        "komi": cfg.komi,
        "superko": "positional",
        "suicide": False,
        "movesPerBonus": cfg.moves_per_bonus,
        "horizon": cfg.horizon,
    }


def _gtp(step: dict[str, Any], n: int) -> str:
    return "pass" if step.get("pass_") else xy_to_gtp(step["x"], step["y"], n)


def result_json(
    res: SearchResult,
    state: VariantState,
    evaluator: dict[str, Any],
    *,
    final: bool,
    max_seconds: float | None,
    max_visits: int | None,
    cancelled: bool = False,
) -> dict[str, Any]:
    n = state.config.size
    cands = []
    for i, s in enumerate(res.stats[:TOP_N]):
        if s.visits == 0:
            continue
        x, y = (None, None) if s.action == n * n else (s.action % n, s.action // n)
        pv = []
        for step in s.pv:
            out = {
                "gtp": _gtp(step, n),
                "x": step["x"],
                "y": step["y"],
                "pass": step["pass_"],
                "color": step["color"],
            }
            if "turnStart" in step:
                out["turnStart"] = step["turnStart"]
            pv.append(out)
        cands.append(
            {
                "gtp": "pass" if x is None else xy_to_gtp(x, y, n),  # type: ignore[arg-type]
                "x": x,
                "y": y,
                "pass": x is None,
                "order": i,
                "visits": s.visits,
                "blackWinrate": s.black_win if s.black_win is not None else res.root_black_win,
                "blackScoreLead": None,
                "prior": s.prior,
                "lcb": None,
                "pv": pv,
            }
        )
    return {
        "type": "final" if final else "partial",
        "isDuringSearch": not final,
        "noResults": not cands,
        "perspective": "black",
        "source": "variant",
        "evaluator": evaluator,
        "budget": {
            "visits": res.visits,
            "seconds": round(res.elapsed, 2),
            "maxVisits": max_visits,
            "maxSeconds": max_seconds,
        },
        "rules": rules_info(state),
        "currentPlayer": "B" if state.to_move == 1 else "W",
        "rootVisits": res.visits,
        "blackWinrate": res.root_black_win,
        "blackScoreLead": res.root_score,
        "scoreStdev": None,
        "candidates": cands,
        "elapsed": round(res.elapsed, 3),
        "cancelled": cancelled,
        "timedOut": False,
    }


# ---------------------------------------------------------------------- worker process
@dataclass
class Job:
    id: int
    state: VariantState
    evaluator: dict[
        str, Any
    ]  # spec: {"kind": "heuristic"} | {"kind": "katago-net", ...} | {"kind": "variant-net", ...}
    seconds: float | None
    visits: int | None


def make_evaluator(spec: dict[str, Any]) -> Any:
    kind = spec["kind"]
    if kind == "heuristic":
        from gobot.variant.models.heuristic import HeuristicEvaluator

        return HeuristicEvaluator()
    if kind == "katago-net":
        from gobot.variant.models.katago_eval import KataGoEvaluator

        return KataGoEvaluator(
            spec["binary"], Path(spec["model_path"]), Path(spec["config_path"]), spec["label"]
        )
    if kind == "variant-net":
        from gobot.variant.models.net_onnx import OnnxEvaluator

        return OnnxEvaluator(Path(spec["path"]))
    raise ValueError(f"unknown evaluator {kind!r}")


def _worker(jobs: Any, results: Any, cancels: Any) -> None:  # pragma: no cover - runs in a subprocess
    from gobot.variant.search.mcts import CachedEvaluator, run_search

    evaluators: dict[str, Any] = {}
    cancelled: set[int] = set()

    def drain() -> None:
        while True:
            try:
                cancelled.add(cancels.get_nowait())
            except queue.Empty:
                return

    try:
        while True:
            try:
                job: Job | None = jobs.get(timeout=IDLE_SECONDS)
            except queue.Empty:
                break
            if job is None:
                break
            drain()
            if job.id in cancelled:
                results.put(
                    {"job": job.id, "type": "final", "cancelled": True, "noResults": True, "candidates": []}
                )
                continue
            spec_key = repr(sorted(job.evaluator.items()))
            try:
                ev = evaluators.get(spec_key)
                if ev is None:
                    ev = evaluators[spec_key] = CachedEvaluator(make_evaluator(job.evaluator), 200_000)
                info = ev.info.public()

                def stop(job_id: int = job.id) -> bool:
                    drain()
                    return job_id in cancelled

                def progress(s: Any, job: Job = job, info: dict[str, Any] = info) -> None:
                    results.put(
                        {
                            "job": job.id,
                            **result_json(
                                s.result(),
                                job.state,
                                info,
                                final=False,
                                max_seconds=job.seconds,
                                max_visits=job.visits,
                            ),
                        }
                    )

                s = run_search(
                    job.state,
                    ev,
                    visits=job.visits,
                    seconds=job.seconds,
                    params=SearchParams(batch=16 if job.evaluator["kind"] != "heuristic" else 8),
                    should_stop=stop,
                    on_progress=progress,
                )
                was_cancelled = job.id in cancelled
                results.put(
                    {
                        "job": job.id,
                        **result_json(
                            s.result(),
                            job.state,
                            info,
                            final=True,
                            max_seconds=job.seconds,
                            max_visits=job.visits,
                            cancelled=was_cancelled,
                        ),
                    }
                )
            except Exception as e:
                results.put({"job": job.id, "type": "error", "message": f"{type(e).__name__}: {e}"})
    finally:
        for ev in evaluators.values():
            close = getattr(ev.inner, "close", None)
            if close:
                with contextlib.suppress(Exception):
                    close()


# ---------------------------------------------------------------------- web side
class VariantAnalysisService:
    def __init__(self) -> None:
        self._ctx = mp.get_context("spawn")
        self._proc: Any = None
        self._jobs: Any = None
        self._results: Any = None
        self._cancels: Any = None
        self._ids = itertools.count(1)
        self._waiters: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]]]] = {}
        self._client_job: dict[str, int] = {}
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()

    def _ensure(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.is_alive():
                return
            self._jobs = self._ctx.Queue()
            self._results = self._ctx.Queue()
            self._cancels = self._ctx.Queue()
            self._proc = self._ctx.Process(
                target=_worker,
                args=(self._jobs, self._results, self._cancels),
                daemon=True,
                name="gobot-variant-search",
            )
            self._proc.start()
            self._reader = threading.Thread(target=self._read, args=(self._proc, self._results), daemon=True)
            self._reader.start()

    def _read(self, proc: Any, results: Any) -> None:
        while True:
            try:
                msg = results.get(timeout=1.0)
            except queue.Empty:
                if not proc.is_alive():
                    break
                continue
            except (EOFError, OSError):
                break
            self._deliver(msg)
        # the worker exited (idle timeout or a crash): fail whatever was still waiting
        for job in list(self._waiters):
            self._deliver(
                {"job": job, "type": "error", "message": "The bonus-search worker stopped; try again."}
            )

    def _deliver(self, msg: dict[str, Any]) -> None:
        w = self._waiters.get(msg.get("job", -1))
        if w is not None:
            loop, q = w
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(q.put_nowait, msg)

    def cancel(self, client: str) -> None:
        job = self._client_job.pop(client, None)
        if job is not None and self._cancels is not None:
            with contextlib.suppress(Exception):
                self._cancels.put(job)

    async def analyze(
        self,
        client: str,
        state: VariantState,
        evaluator: dict[str, Any],
        seconds: float | None,
        visits: int | None,
    ) -> AsyncIterator[dict[str, Any]]:
        self.cancel(client)
        await asyncio.to_thread(self._ensure)
        job = next(self._ids)
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._waiters[job] = (asyncio.get_running_loop(), q)
        self._client_job[client] = job
        seconds = min(seconds, MAX_SECONDS) if seconds is not None else None
        self._jobs.put(Job(job, state, evaluator, seconds, visits))
        deadline = time.monotonic() + (seconds or MAX_SECONDS) + 120.0  # first KataGo start can be slow
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=max(1.0, deadline - time.monotonic()))
                except TimeoutError:
                    yield {"type": "error", "message": "The bonus search did not finish in time."}
                    return
                msg.pop("job", None)
                yield msg
                if msg.get("type") in ("final", "error"):
                    return
        finally:
            self._waiters.pop(job, None)
            if self._client_job.get(client) == job:
                self._client_job.pop(client, None)
                if self._cancels is not None:
                    with contextlib.suppress(Exception):
                        self._cancels.put(job)  # consumer went away before the end: stop the search

    def shutdown(self) -> None:
        p = self._proc
        self._proc = None
        if p is None:
            return
        with contextlib.suppress(Exception):
            self._jobs.put(None)
        p.join(timeout=5)
        if p.is_alive():
            p.kill()
