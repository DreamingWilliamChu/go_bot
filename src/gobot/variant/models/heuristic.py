"""A documented, deliberately simple heuristic evaluator (no network, no KataGo).

Priors (weights, then normalised over legal actions):

* capture an enemy chain in atari: 6
* extend a friendly chain in atari: 4
* next to any stone: 1.5; elsewhere 1; on the first line while the board is less than half full: 0.4
* fill one of your own eyes (every neighbour is your stone and no friendly neighbour chain is in atari): 0.02
* pass: 0.01, or 1 when every placement fills an own eye

Value: Black's win probability ``sigmoid(lead / scale)`` with ``scale = max(3, 0.06 * N**2)``, where the
lead comes from a nearest-stone (grid Voronoi) estimate of each empty point plus the stones, minus komi.
It knows nothing about bonuses: in the bonus search, future bonuses enter only through the search's
chance nodes. This is the "heuristic baseline" of the evaluation reports.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from gobot.variant.models.evaluator import Evaluation, EvaluatorInfo, legal_mask, normalise
from gobot.variant.observation import Observation, search_key
from gobot.variant.rules import BLACK, EMPTY, WHITE, Board


def nearest_stone_lead(board: Board, komi: float) -> float:
    """Black's lead if every empty point went to the colour of its nearest stone (ties: nobody)."""
    geo = board.geo
    cells = board.cells
    neigh = geo.neigh
    dist = [-1] * geo.cells
    owner = [0] * geo.cells
    frontier = []
    for p in geo.points:
        c = cells[p]
        if c != EMPTY:
            dist[p] = 0
            owner[p] = c
            frontier.append(p)
    if not frontier:
        return -komi
    d = 0
    while frontier:
        d += 1
        nxt: list[int] = []
        for p in frontier:
            o = owner[p]
            for q in neigh[p]:
                if cells[q] != EMPTY:
                    continue
                if dist[q] < 0:
                    dist[q] = d
                    owner[q] = o
                    nxt.append(q)
                elif dist[q] == d and owner[q] != o:
                    owner[q] = 3  # equidistant from both colours
        frontier = nxt
    b = sum(1 for p in geo.points if owner[p] == BLACK)
    w = sum(1 for p in geo.points if owner[p] == WHITE)
    return b - w - komi


def heuristic_value(board: Board, komi: float) -> float:
    lead = nearest_stone_lead(board, komi)
    scale = max(3.0, 0.06 * board.n * board.n)
    return 1.0 / (1.0 + math.exp(-lead / scale))


def heuristic_priors(obs: Observation) -> np.ndarray:
    st = obs.state
    board = st.board
    geo = board.geo
    cells = board.cells
    color = st.to_move
    opp = 3 - color
    ch = board.chains()
    mask = legal_mask(obs)
    w = np.zeros(geo.area + 1, dtype=np.float32)
    early = board.count(BLACK) + board.count(WHITE) < geo.area // 2
    n = geo.n
    non_eye = 0
    for a in np.flatnonzero(mask[:-1]):
        p = geo.points[a]
        nb = geo.neigh[p]
        weight = 1.0
        own_all = True
        own_atari = False
        near = False
        for q in nb:
            v = cells[q]
            if v == opp:
                near = True
                own_all = False
                if ch.libs[ch.label[q]] == 1:
                    weight = max(weight, 6.0)
            elif v == color:
                near = True
                if ch.libs[ch.label[q]] == 1:
                    own_atari = True
                    weight = max(weight, 4.0)
            elif v == EMPTY:
                own_all = False
        if own_all and not own_atari:
            weight = 0.02  # filling an own eye
        else:
            non_eye += 1
            if near:
                weight *= 1.5
            x, y = a % n, a // n
            if early and n >= 7 and (x in (0, n - 1) or y in (0, n - 1)):
                weight *= 0.4
        w[a] = weight
    w[-1] = 0.01 if non_eye else 1.0
    return normalise(w, mask)


class HeuristicEvaluator:
    info = EvaluatorInfo(
        "heuristic",
        "Heuristic evaluator",
        "heuristic-v1",
        approximate=True,
        note="Nearest-stone territory estimate; knows nothing about bonuses or Go tactics beyond ataris.",
    )

    def evaluate(self, batch: Sequence[Observation]) -> list[Evaluation]:
        out = []
        for obs in batch:
            st = obs.state
            lead = nearest_stone_lead(st.board, st.config.komi)
            scale = max(3.0, 0.06 * st.config.area)
            out.append(Evaluation(heuristic_priors(obs), 1.0 / (1.0 + math.exp(-lead / scale)), lead))
        return out

    def key(self, obs: Observation) -> bytes:
        return search_key(obs.state)
