"""Stochastic MCTS for bonus-turn Go: decision nodes and chance nodes.

* **Decision node**: the player to move (``actor``) chooses one legal action (placement or pass). The
  same player stays the actor for every move of a bonus turn; the actor changes only when a turn ends.
* **Chance node**: a turn just ended and the next player's turn starts; it is a bonus turn with the
  *public* probability (``VariantState.trigger_probability``): ``r / (H - t)`` for the Mac's random
  schedule, or the same model of an outside judge's timing for judge-granted bonuses. Never the hidden
  schedule (the search only ever receives an ``Observation``), and never a player's decision. Outcomes with probability 0 or 1 are not
  branched. Chance outcomes are chosen by stratified selection - the outcome whose visit share lags its
  probability the most - so visit shares follow the probabilities instead of being picked like actions.
* **Values** are Black's win probability in [0, 1] everywhere. A decision node's actor maximises its
  own side's view of it (``q`` for Black, ``1 - q`` for White); nothing is negated per move, so a run of
  same-player moves backs up correctly. Virtual loss and first-play urgency are applied from the actor's
  side.
* **Terminal** states use the exact area score (a truncated self-play state uses the score of the board
  as it stands as its value; it is never used as a training target).
* No transpositions: the tree is a tree, so superko history never leaks between paths. Evaluations are
  cached by the evaluator's key (its exact inputs).

Leaves are evaluated in batches: ``Search.prepare`` selects up to *k* leaves with virtual loss,
``Search.finish`` expands them with their evaluations and backs the values up. ``run_search`` drives one
search; self-play drives many searches and evaluates their leaves together.
"""

from __future__ import annotations

import collections
import math
import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from gobot.variant.models.evaluator import Evaluation, Evaluator, EvaluatorInfo
from gobot.variant.observation import Observation, observe
from gobot.variant.rules import BLACK, COLOR_NAMES
from gobot.variant.state import TERMINAL, TURN_START, VariantState, apply_action, start_turn


@dataclass
class SearchParams:
    c_puct: float = 1.5
    fpu_reduction: float = 0.2
    batch: int = 8
    dirichlet_alpha: float = 0.0  # > 0: root noise (self-play only)
    dirichlet_frac: float = 0.25
    # Chance model for future bonuses. False = "bonus-blind" (an evaluation baseline): the search
    # imagines that no further bonus can trigger; the current turn's moves are still played out.
    model_bonuses: bool = True


class Decision:
    __slots__ = (
        "state",
        "actor",
        "terminal",
        "expanded",
        "actions",
        "P",
        "N",
        "W",
        "children",
        "n",
        "w",
        "score",
    )

    def __init__(self, state: VariantState):
        self.state = state
        self.actor = state.to_move
        self.terminal = state.phase == TERMINAL
        self.expanded = False
        self.actions: np.ndarray = np.zeros(0, dtype=np.int64)
        self.P: np.ndarray = np.zeros(0, dtype=np.float64)
        self.N: np.ndarray = np.zeros(0, dtype=np.float64)
        self.W: np.ndarray = np.zeros(0, dtype=np.float64)
        self.children: dict[int, Decision | Chance] = {}
        self.n = 0.0  # visits through this node (including virtual ones)
        self.w = 0.0  # sum of Black values
        self.score: float | None = None

    def expand(self, ev: Evaluation) -> None:
        # Only the player's own moves: a bonus is never the player's (or the program's) decision.
        acts = np.asarray(self.state.legal_actions(), dtype=np.int64)
        p = ev.priors[acts].astype(np.float64)
        s = p.sum()
        self.actions = acts
        self.P = p / s if s > 0 else np.full(len(acts), 1.0 / len(acts))
        self.N = np.zeros(len(acts))
        self.W = np.zeros(len(acts))
        self.expanded = True
        self.score = ev.black_score

    def select(self, c_puct: float, fpu_reduction: float) -> int:
        n = self.N
        black = self.actor == BLACK
        parent_q = self.w / self.n if self.n > 0 else 0.5
        if not black:
            parent_q = 1.0 - parent_q
        visited = n > 0
        q = np.divide(self.W, n, out=np.zeros_like(self.W), where=visited)
        if not black:
            q = 1.0 - q
        fpu = parent_q - fpu_reduction * math.sqrt(float(self.P[visited].sum()))
        q = np.where(visited, q, fpu)
        u = c_puct * self.P * (math.sqrt(self.n) / (1.0 + n))
        return int(np.argmax(q + u))

    def value(self) -> float:
        return self.w / self.n if self.n > 0 else 0.5


class Chance:
    """The start of ``state.to_move``'s next turn: bonus (index 0) or normal (index 1)."""

    __slots__ = ("state", "probs", "outcomes", "N", "W", "n", "w")

    def __init__(self, state: VariantState, p_bonus: float):
        self.state = state
        self.probs = (p_bonus, 1.0 - p_bonus)
        self.outcomes = (Decision(start_turn(state, True)), Decision(start_turn(state, False)))
        self.N = [0.0, 0.0]
        self.W = [0.0, 0.0]
        self.n = 0.0
        self.w = 0.0

    def select(self) -> int:
        total = self.n + 1.0
        lag = [self.probs[i] - self.N[i] / total for i in (0, 1)]
        return 0 if lag[0] > lag[1] else 1

    def value(self) -> float:
        """Probability-weighted expectation once both outcomes have values, else the sample mean."""
        if self.N[0] > 0 and self.N[1] > 0:
            return sum(self.probs[i] * self.W[i] / self.N[i] for i in (0, 1))
        return self.w / self.n if self.n > 0 else 0.5


Node = Decision | Chance


def make_node(state: VariantState, model_bonuses: bool = True) -> Node:
    """The node for ``state``: chance at a turn start (unless one outcome is certain)."""
    if state.phase == TURN_START:
        p = state.trigger_probability(state.to_move)
        if p <= 0.0:
            return Decision(start_turn(state, False))
        if p >= 1.0:
            return Decision(start_turn(state, True))
        return Chance(state, p)
    return Decision(state)


def terminal_value(state: VariantState) -> float:
    r = state.result
    assert r is not None
    v = r.black_value
    return v if v is not None else r.score.black_value


@dataclass
class Leaf:
    node: Decision
    path: list[tuple[Node, int]]


@dataclass
class ActionStat:
    action: int
    visits: int
    black_win: float | None  # None: not visited
    prior: float
    pv: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SearchResult:
    root_black_win: float
    visits: int
    elapsed: float
    stats: list[ActionStat]  # sorted by visits, best first
    root_score: float | None
    actor: int
    size: int

    def best_action(self, temperature: float, rng: random.Random) -> int:
        """An action chosen from the visit counts (including "use a bonus"): the most visited at
        temperature 0, else proportionally to visits ** (1 / temperature)."""
        stats = [s for s in self.stats if s.visits > 0] or self.stats[:1]
        if temperature <= 0:
            top = max(s.visits for s in stats)
            return rng.choice([s.action for s in stats if s.visits == top])
        w = [s.visits ** (1.0 / temperature) for s in stats]
        return rng.choices([s.action for s in stats], weights=w)[0]

    def policy(self) -> np.ndarray:
        """Visit distribution over the N*N+1 actions (the self-play policy target)."""
        pi = np.zeros(self.size * self.size + 1, dtype=np.float32)
        for s in self.stats:
            pi[s.action] = s.visits
        t = pi.sum()
        return pi / t if t > 0 else pi


class Search:
    def __init__(
        self, root: VariantState, params: SearchParams | None = None, rng: random.Random | None = None
    ):
        if root.phase == TURN_START:
            raise ValueError("search starts from a resolved turn (in_turn), never from a pending turn start")
        self.params = params or SearchParams()
        if not self.params.model_bonuses:
            # Bonus-blind baseline: imagine a game in which no further bonus can ever trigger. The
            # current turn is played out as it is; every action chosen is legal in the real game.
            root = replace(root, remaining=(0, 0))
        self.rng = rng or random.Random()
        self.root = Decision(root)
        self.started = time.monotonic()
        self._noise_done = False

    @property
    def visits(self) -> int:
        return int(sum(self.root.N)) if self.root.expanded else 0

    # ------------------------------------------------------------------ selection
    def _vl(self, node: Decision) -> float:
        """Virtual loss: a loss for the actor (Black value 0 if Black acts, 1 if White does)."""
        return 0.0 if node.actor == BLACK else 1.0

    def prepare(self, k: int) -> list[Leaf]:
        """Up to ``k`` leaves that need an evaluation (terminal leaves are backed up at once)."""
        leaves: list[Leaf] = []
        pending: set[int] = set()
        if not self.root.expanded:
            if self.root.terminal:
                return []
            return [Leaf(self.root, [])]
        for _ in range(k):
            node: Node = self.root
            path: list[tuple[Node, int]] = []
            while True:
                if isinstance(node, Chance):
                    i = node.select()
                    path.append((node, i))
                    node.N[i] += 1
                    node.n += 1
                    node = node.outcomes[i]
                    continue
                if node.terminal or not node.expanded:
                    break
                i = node.select(self.params.c_puct, self.params.fpu_reduction)
                path.append((node, i))
                node.N[i] += 1
                node.W[i] += self._vl(node)
                node.n += 1
                child = node.children.get(i)
                if child is None:
                    child = make_node(
                        apply_action(node.state, int(node.actions[i])), self.params.model_bonuses
                    )
                    node.children[i] = child
                node = child
            if node.terminal:
                self._backup(path, terminal_value(node.state), node)
                continue
            if id(node) in pending:  # collision: another pending leaf; undo the virtual loss
                self._revert(path)
                break
            pending.add(id(node))
            leaves.append(Leaf(node, path))
        return leaves

    def _revert(self, path: list[tuple[Node, int]]) -> None:
        for node, i in path:
            node.N[i] -= 1
            node.n -= 1
            if isinstance(node, Decision):
                node.W[i] -= self._vl(node)

    def _backup(self, path: list[tuple[Node, int]], value: float, leaf: Decision) -> None:
        leaf.n += 1
        leaf.w += value
        for node, i in path:
            if isinstance(node, Decision):
                node.W[i] += value - self._vl(node)  # replace the virtual loss by the real value
            else:
                node.W[i] += value
            node.w += value

    # ------------------------------------------------------------------ expansion
    def finish(self, leaves: Sequence[Leaf], evals: Sequence[Evaluation]) -> None:
        for leaf, ev in zip(leaves, evals, strict=True):
            leaf.node.expand(ev)
            if leaf.node is self.root and self.params.dirichlet_alpha > 0 and not self._noise_done:
                self._add_noise()
            self._backup(leaf.path, ev.black_win, leaf.node)

    def _add_noise(self) -> None:
        r = self.root
        if len(r.actions) < 2:
            return
        alpha = self.params.dirichlet_alpha
        noise = np.array([self.rng.gammavariate(alpha, 1.0) for _ in range(len(r.actions))])
        noise /= noise.sum()
        f = self.params.dirichlet_frac
        r.P = (1 - f) * r.P + f * noise
        self._noise_done = True

    # ------------------------------------------------------------------ results
    def result(self, pv_len: int = 12) -> SearchResult:
        r = self.root
        stats = []
        for i in np.argsort(-r.N, kind="stable") if r.expanded else []:
            n = int(r.N[i])
            child = r.children.get(int(i))
            q: float | None = None
            if n > 0:
                q = child.value() if isinstance(child, Chance) else float(r.W[i] / r.N[i])
            pv = self._pv(r, int(i), pv_len) if n > 0 else []
            stats.append(ActionStat(int(r.actions[i]), n, q, float(r.P[i]), pv))
        return SearchResult(
            root_black_win=r.value(),
            visits=self.visits,
            elapsed=time.monotonic() - self.started,
            stats=stats,
            root_score=r.score,
            actor=r.actor,
            size=r.state.config.size,
        )

    def _pv(self, node: Decision, i: int, pv_len: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        n = node.state.config.size
        while len(out) < pv_len:
            a = int(node.actions[i])
            step: dict[str, Any] = {"color": COLOR_NAMES[node.actor], "action": a}
            if a == n * n:
                step.update(pass_=True, x=None, y=None)
            else:
                step.update(pass_=False, x=a % n, y=a // n)
            out.append(step)
            child = node.children.get(i)
            if child is None:
                break
            if isinstance(child, Chance):
                j = 0 if child.N[0] >= child.N[1] else 1
                step["turnStart"] = {
                    "player": COLOR_NAMES[child.state.to_move],
                    "bonus": j == 0,
                    "probability": child.probs[0],
                }
                child = child.outcomes[j]
            elif child.state.phase != TERMINAL and child.actor != node.actor:
                step["turnStart"] = {
                    "player": COLOR_NAMES[child.actor],
                    "bonus": child.state.bonus_active,
                    "probability": None,
                }
            if not child.expanded or child.terminal or not child.N.any():
                break
            node = child
            i = int(np.argmax(node.N))
        return out


class CachedEvaluator:
    """LRU cache in front of an evaluator, keyed by the evaluator's own key (its exact inputs)."""

    def __init__(self, inner: Evaluator, size: int = 200_000):
        self.inner = inner
        self.info: EvaluatorInfo = inner.info
        self.size = size
        self.cache: collections.OrderedDict[bytes, Evaluation] = collections.OrderedDict()
        self.hits = 0
        self.misses = 0

    def key(self, obs: Observation) -> bytes:
        return self.inner.key(obs)

    def evaluate(self, batch: Sequence[Observation]) -> list[Evaluation]:
        keys = [self.inner.key(o) for o in batch]
        out: list[Evaluation | None] = []
        todo: list[int] = []
        for i, k in enumerate(keys):
            ev = self.cache.get(k)
            if ev is None:
                todo.append(i)
            else:
                self.cache.move_to_end(k)
                self.hits += 1
            out.append(ev)
        if todo:
            # duplicates inside one batch are evaluated once
            first: dict[bytes, int] = {}
            uniq = [i for i in todo if first.setdefault(keys[i], i) == i]
            evs = self.inner.evaluate([batch[i] for i in uniq])
            for i, ev in zip(uniq, evs, strict=True):
                self.cache[keys[i]] = ev
            self.misses += len(uniq)
            for i in todo:
                out[i] = self.cache[keys[i]]
            while len(self.cache) > self.size:
                self.cache.popitem(last=False)
        return [e for e in out if e is not None]


def run_search(
    root: VariantState | Observation,
    evaluator: Evaluator,
    *,
    visits: int | None = None,
    seconds: float | None = None,
    params: SearchParams | None = None,
    rng: random.Random | None = None,
    should_stop: Callable[[], bool] | None = None,
    on_progress: Callable[[Search], None] | None = None,
    progress_every: float = 0.5,
) -> Search:
    """Search until ``visits`` root visits, ``seconds`` or ``should_stop()``; returns the Search."""
    state = root.state if isinstance(root, Observation) else root
    if visits is None and seconds is None:
        raise ValueError("give a visit or time budget")
    s = Search(state, params, rng)
    deadline = time.monotonic() + seconds if seconds is not None else math.inf
    next_report = time.monotonic() + progress_every
    batch = (params or SearchParams()).batch
    while True:
        if s.root.terminal:
            break
        if visits is not None and s.visits >= visits:
            break
        if time.monotonic() >= deadline or (should_stop is not None and should_stop()):
            break
        k = batch if visits is None else max(1, min(batch, visits - s.visits))
        leaves = s.prepare(k)
        if leaves:
            s.finish(leaves, evaluator.evaluate([observe(leaf.node.state) for leaf in leaves]))
        if on_progress is not None and time.monotonic() >= next_report:
            next_report = time.monotonic() + progress_every
            on_progress(s)
    return s
