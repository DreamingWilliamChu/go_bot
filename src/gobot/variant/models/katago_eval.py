"""KataGo's raw network as an *approximate* evaluator for the bonus search.

A separate ``katago analysis`` process (started lazily, owned by whoever creates the evaluator - in
the app, the variant-search worker process) answers one-visit queries: the board as ``initialStones``
with the actor as ``initialPlayer``, ``maxVisits: 1`` and ``includePolicy``. The reply's raw policy and
value are those of a network trained for *ordinary* Go with alternating moves: it does not know that
bonus turns exist, so its win estimates for bonus positions are approximations and labelled as such.
Its policy is masked to the variant's legal moves (which include superko) and renormalised.

Sending a snapshot (no move history) keeps KataGo's view consistent with ours - no fabricated passes
and no stale ko state - at the cost of KataGo's history features.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from gobot.go.coords import xy_to_gtp
from gobot.variant.models.evaluator import Evaluation, EvaluatorInfo, legal_mask, normalise
from gobot.variant.observation import Observation
from gobot.variant.rules import COLOR_NAMES

KATAGO_NOTE = (
    "KataGo's network judges positions as ordinary Go: it does not know about bonus turns, so its win "
    "estimates here are approximations. The bonus search itself handles turns, chances and rules."
)


def build_query(qid: str, obs: Observation) -> dict[str, Any]:
    st = obs.state
    n = st.config.size
    stones = [[COLOR_NAMES[c], xy_to_gtp(x, y, n)] for x, y, c in st.board.stones()]
    return {
        "id": qid,
        "initialStones": stones,
        "initialPlayer": COLOR_NAMES[st.to_move],
        "moves": [],
        "rules": "chinese",
        "komi": st.config.komi,
        "boardXSize": n,
        "boardYSize": n,
        "maxVisits": 1,
        "includePolicy": True,
        "includeOwnership": False,
        "analysisPVLen": 1,
    }


def parse_response(resp: dict[str, Any], obs: Observation) -> Evaluation:
    """Black-perspective value (the config reports winrates as BLACK) and the legal-masked policy."""
    if "error" in resp:
        raise RuntimeError(f"KataGo rejected a query: {resp['error']}")
    root = resp["rootInfo"]
    mask = legal_mask(obs)
    raw = np.asarray(resp["policy"], dtype=np.float32)
    if raw.shape != mask.shape:
        raise ValueError(f"policy has {raw.size} entries, expected {mask.size}")
    # KataGo marks moves it considers illegal with -1; it never sees our superko history, and we never
    # trust it about legality: a move legal for us that KataGo rejects gets a small floor prior.
    pol = np.where(raw < 0, 1e-4, raw)
    return Evaluation(normalise(pol, mask), float(root["winrate"]), float(root.get("scoreLead", 0.0)))


class KataGoEvaluator:
    def __init__(
        self, binary: str, model_path: Path, config_path: Path, model_label: str, startup_timeout: float = 600
    ):
        self.info = EvaluatorInfo(
            "katago-net",
            f"KataGo network ({model_label}), standard Go",
            model_label,
            approximate=True,
            note=KATAGO_NOTE,
        )
        self._cmd = [binary, "analysis", "-config", str(config_path), "-model", str(model_path)]
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._cond = threading.Condition()
        self._replies: dict[str, dict[str, Any]] = {}
        self._ids = 0
        self._dead: str | None = None
        self._stderr: list[str] = []
        self.startup_timeout = startup_timeout

    # ------------------------------------------------------------------ process
    def _ensure(self) -> subprocess.Popen[str]:
        p = self._proc
        if p is not None and p.poll() is None and self._dead is None:
            return p
        self._dead = None
        p = subprocess.Popen(
            self._cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._proc = p
        threading.Thread(target=self._read, args=(p,), daemon=True, name="katago-eval-out").start()
        threading.Thread(target=self._drain, args=(p,), daemon=True, name="katago-eval-err").start()
        # one throwaway query proves the network loaded
        self._wait_for([self._send(p, {"id": "warmup", "action": "query_version"})], self.startup_timeout)
        return p

    def _read(self, p: subprocess.Popen[str]) -> None:
        assert p.stdout is not None
        for line in p.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            qid = obj.get("id")
            if qid is None or obj.get("isDuringSearch"):
                continue
            with self._cond:
                self._replies[str(qid)] = obj
                self._cond.notify_all()
        with self._cond:
            self._dead = "KataGo (evaluator) exited: " + " | ".join(self._stderr[-5:])
            self._cond.notify_all()

    def _drain(self, p: subprocess.Popen[str]) -> None:
        assert p.stderr is not None
        for line in p.stderr:
            self._stderr.append(line.rstrip())
            del self._stderr[:-50]

    def _send(self, p: subprocess.Popen[str], obj: dict[str, Any]) -> str:
        assert p.stdin is not None
        p.stdin.write(json.dumps(obj, separators=(",", ":")) + "\n")
        p.stdin.flush()
        return str(obj["id"])

    def _wait_for(self, ids: list[str], timeout: float) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        with self._cond:
            while not all(i in self._replies for i in ids):
                if self._dead:
                    raise RuntimeError(self._dead)
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError("KataGo (evaluator) did not answer in time")
                self._cond.wait(left)
            return [self._replies.pop(i) for i in ids]

    # ------------------------------------------------------------------ evaluator
    def evaluate(self, batch: Sequence[Observation]) -> list[Evaluation]:
        with self._lock:
            p = self._ensure()
            ids = []
            for obs in batch:
                self._ids += 1
                ids.append(self._send(p, build_query(f"e{self._ids}", obs)))
            replies = self._wait_for(ids, 120)
        return [parse_response(r, o) for r, o in zip(replies, batch, strict=True)]

    def key(self, obs: Observation) -> bytes:
        # The query is a snapshot: KataGo's answer depends on the stones, the player and komi only.
        st = obs.state
        return bytes(st.board.cells) + bytes([st.to_move]) + str(st.config.komi).encode()

    def close(self) -> None:
        p = self._proc
        self._proc = None
        if p is None:
            return
        with contextlib.suppress(Exception):
            if p.stdin:
                p.stdin.close()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


def evaluator_config_settings(base: dict[str, Any]) -> dict[str, Any]:
    """Many one-visit queries at once: many analysis threads, one search thread each, a big batch."""
    return {
        **base,
        "num_analysis_threads": 24,
        "num_search_threads": 1,
        "nn_max_batch_size": 32,
        "nn_cache_pow2": 18,
    }
