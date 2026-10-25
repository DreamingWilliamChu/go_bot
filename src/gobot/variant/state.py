"""Authoritative public state of a bonus-turn Go game and its transitions.

``VariantState`` holds only public information (docs/BONUS_GO.md): the board, superko history, whose
turn it is, the current turn's moves, both players' remaining bonuses and personal-turn counters, the
pass-only streak and the result. The hidden schedule is not here; it lives in
``gobot.variant.env.Environment``, which resolves each turn start with ``start_turn``. The search
resolves turn starts by chance instead, with the public probabilities.

Phases:

* ``in_turn``     - ``to_move`` has ``moves_left`` moves (placements or a pass) in the current turn
* ``turn_start``  - a turn just ended; ``to_move``'s next turn is about to start (bonus or not). Public
                    states shown to players are never in this phase: the environment resolves it at once
* ``terminal``    - two consecutive pass-only turns, or a self-play placement cap (``truncated``)
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal

from gobot.variant.config import VariantConfig
from gobot.variant.randomness import check_feasible, trigger_probability
from gobot.variant.rules import BLACK, COLOR_NAMES, WHITE, Board, IllegalMove, opponent
from gobot.variant.scoring import ScoreResult, area_score

IN_TURN = "in_turn"
TURN_START = "turn_start"
TERMINAL = "terminal"
RECENT = 6  # placements remembered for model history features


@dataclass(frozen=True, slots=True)
class Result:
    reason: Literal["passes", "truncated"]
    score: ScoreResult  # exact area score of the final board as it stands

    @property
    def truncated(self) -> bool:
        return self.reason == "truncated"

    @property
    def black_value(self) -> float | None:
        """1 Black win, 0 White win, 0.5 draw; None for a truncated game (not a real outcome)."""
        return None if self.truncated else self.score.black_value

    def public(self) -> dict[str, object]:
        return {"reason": self.reason, "truncated": self.truncated, "score": self.score.public()}


@dataclass(slots=True, eq=False)
class VariantState:
    config: VariantConfig
    board: Board
    seen: frozenset[int]  # hashes of every position so far, including the current one
    to_move: int
    phase: str
    moves_left: int  # in the current turn (0 outside in_turn)
    turn_moves: int  # moves the current turn started with (1, or moves_per_bonus)
    bonus_active: bool
    placed_this_turn: int
    remaining: tuple[int, int]  # bonus opportunities left: (Black, White)
    turns_started: tuple[int, int]  # personal turns started so far (the current one included)
    pass_streak: int  # consecutive pass-only turns that just ended
    placements: int  # stones placed in this game so far
    captures: tuple[int, int]  # stones captured by (Black, White)
    recent: tuple[tuple[int, int], ...]  # last RECENT placements as (action, colour), oldest first
    result: Result | None = None
    history_known: bool = True  # False for photo/branch imports: no earlier positions are known
    _legal: list[int] | None = field(default=None, init=False, repr=False)
    _superko: list[int] | None = field(default=None, init=False, repr=False)
    memo: dict[str, Any] = field(default_factory=dict, init=False, repr=False, compare=False)  # derived data

    # ------------------------------------------------------------------ queries
    @property
    def terminal(self) -> bool:
        return self.phase == TERMINAL

    def trigger_probability(self, player: int) -> float:
        """Public P(``player``'s next personal turn is a bonus turn by the random trigger); 0 when the
        players choose their bonuses (nothing is random then)."""
        if self.config.trigger == "choice":
            return 0.0
        i = player - 1
        return trigger_probability(self.remaining[i], self.turns_started[i], self.config.horizon)

    @property
    def can_use_bonus(self) -> bool:
        """Choice trigger: ``to_move`` may spend a bonus now - a turn in progress that is still a
        normal turn with no move made yet, and a bonus left."""
        return (
            self.config.trigger == "choice"
            and self.phase == IN_TURN
            and not self.bonus_active
            and self.placed_this_turn == 0
            and self.moves_left == self.turn_moves == 1
            and self.remaining[self.to_move - 1] > 0
        )

    def legal_actions(self) -> list[int]:
        """Legal actions for ``to_move`` in this turn: placements, then the pass action (N*N)."""
        if self._legal is None:
            if self.phase != IN_TURN:
                self._legal, self._superko = [], []
            else:
                sk: list[int] = []
                self._legal = self.board.legal_actions(self.to_move, self.seen, sk) + [
                    self.config.pass_action
                ]
                self._superko = sk
        return self._legal

    def superko_forbidden(self) -> list[int]:
        """Points ``to_move`` could play but for positional superko."""
        if self._superko is None:
            self.legal_actions()
        assert self._superko is not None
        return self._superko

    def is_legal(self, action: int) -> bool:
        if self.phase != IN_TURN:
            return False
        if action == self.config.pass_action:
            return True
        if action == self.config.bonus_action:
            return self.can_use_bonus
        return action in self.legal_actions()


def _check_board(board: Board, config: VariantConfig) -> None:
    if board.n != config.size:
        raise ValueError(f"board is {board.n}x{board.n}, config says {config.size}")
    dead = board.dead_chains()
    if dead:
        raise ValueError(f"{sum(len(c) for c in dead)} stone(s) have no liberties")


def initial_state(config: VariantConfig, board: Board | None = None, to_move: int = BLACK) -> VariantState:
    """A new game, before the first turn start is resolved (phase ``turn_start``)."""
    board = board or Board.empty(config.size)
    _check_board(board, config)
    return VariantState(
        config=config,
        board=board,
        seen=frozenset((board.hash,)),
        to_move=to_move,
        phase=TURN_START,
        moves_left=0,
        turn_moves=0,
        bonus_active=False,
        placed_this_turn=0,
        remaining=(config.black_bonuses, config.white_bonuses),
        turns_started=(0, 0),
        pass_streak=0,
        placements=0,
        captures=(0, 0),
        recent=(),
        history_known=board.count(BLACK) + board.count(WHITE) == 0,
    )


def imported_state(
    config: VariantConfig,
    board: Board,
    to_move: int,
    remaining: tuple[int, int],
    turns_started: tuple[int, int],
    bonus_active: bool = False,
    moves_left: int = 1,
    pass_streak: int = 0,
) -> VariantState:
    """A position entered without its history (photo, analysis branch). The current turn of
    ``to_move`` is already under way (it counts in ``turns_started``); everything is validated so the
    remaining bonuses fit in the remaining eligible turns. Superko only knows this position."""
    _check_board(board, config)
    i = to_move - 1
    if to_move not in (BLACK, WHITE):
        raise ValueError("to_move must be Black or White")
    if turns_started[i] < 1:
        raise ValueError("the current turn counts as a started personal turn (turns started >= 1)")
    if min(turns_started) < 0 or min(remaining) < 0:
        raise ValueError("counts cannot be negative")
    if config.trigger == "random":
        for j in (0, 1):
            check_feasible(remaining[j], turns_started[j], config.horizon)
    if bonus_active:
        if not 1 <= moves_left <= config.moves_per_bonus:
            raise ValueError(f"a bonus turn has 1..{config.moves_per_bonus} moves left")
        turn_moves = config.moves_per_bonus
    else:
        moves_left = turn_moves = 1
    if not 0 <= pass_streak <= 1:
        raise ValueError("the pass-only streak before a turn in progress is 0 or 1")
    return VariantState(
        config=config,
        board=board,
        seen=frozenset((board.hash,)),
        to_move=to_move,
        phase=IN_TURN,
        moves_left=moves_left,
        turn_moves=turn_moves,
        bonus_active=bonus_active,
        placed_this_turn=turn_moves - moves_left,
        remaining=remaining,
        turns_started=turns_started,
        pass_streak=pass_streak,
        placements=0,
        captures=(0, 0),
        recent=(),
        history_known=False,
    )


# ---------------------------------------------------------------------- transitions
def start_turn(state: VariantState, bonus: bool) -> VariantState:
    """Resolve the start of ``to_move``'s next personal turn. ``bonus`` must be feasible: a bonus
    needs one left; with as many bonuses left as eligible turns the bonus is forced."""
    if state.phase != TURN_START:
        raise ValueError(f"no turn start to resolve in phase {state.phase}")
    i = state.to_move - 1
    if state.config.trigger == "choice" and bonus:
        raise ValueError(
            "with the choice trigger a turn starts as a normal turn; the player may then use a bonus"
        )
    p = state.trigger_probability(state.to_move)
    if bonus and p <= 0.0:
        raise ValueError(f"{COLOR_NAMES[state.to_move]} has no bonus available for this turn")
    if not bonus and p >= 1.0 and state.config.trigger == "random":
        raise ValueError(f"{COLOR_NAMES[state.to_move]}'s bonus is forced on this turn")
    k = state.config.moves_per_bonus if bonus else 1
    remaining = list(state.remaining)
    if bonus:
        remaining[i] -= 1
    started = list(state.turns_started)
    started[i] += 1
    return replace(
        state,
        phase=IN_TURN,
        moves_left=k,
        turn_moves=k,
        bonus_active=bonus,
        placed_this_turn=0,
        remaining=(remaining[0], remaining[1]),
        turns_started=(started[0], started[1]),
    )


def apply_action(state: VariantState, action: int) -> VariantState:
    """Play one move (a placement or the pass action) for ``to_move``. Raises IllegalMove."""
    if state.phase != IN_TURN:
        raise IllegalMove("turn", "The game is over." if state.terminal else "No turn is in progress.")
    cfg = state.config
    if action == cfg.pass_action:
        return _end_turn(state)
    if action == cfg.bonus_action:
        return use_bonus(state)
    color = state.to_move
    board, n_cap = state.board.play(action, color, state.seen)
    caps = (
        (state.captures[0] + n_cap, state.captures[1])
        if color == BLACK
        else (
            state.captures[0],
            state.captures[1] + n_cap,
        )
    )
    placements = state.placements + 1
    ns = replace(
        state,
        board=board,
        seen=state.seen | {board.hash},
        moves_left=state.moves_left - 1,
        placed_this_turn=state.placed_this_turn + 1,
        placements=placements,
        captures=caps,
        recent=(state.recent + ((action, color),))[-RECENT:],
    )
    if cfg.max_placements is not None and placements >= cfg.max_placements:
        return _finish(ns, "truncated")
    if ns.moves_left == 0:
        return _end_turn(ns)
    return ns


def use_bonus(state: VariantState) -> VariantState:
    """Choice trigger: ``to_move`` spends one bonus; this turn becomes a bonus turn of
    ``moves_per_bonus`` moves in total (none made yet). Only before the turn's first move."""
    if not state.can_use_bonus:
        if state.config.trigger != "choice":
            raise IllegalMove("bonus", "In this game bonus turns are random; they cannot be chosen.")
        if state.bonus_active:
            raise IllegalMove("bonus", "This is already a bonus turn.")
        if state.phase == IN_TURN and state.remaining[state.to_move - 1] <= 0:
            raise IllegalMove("bonus", f"{COLOR_NAMES[state.to_move]} has no bonus left.")
        raise IllegalMove("bonus", "A bonus can only be used before the first move of a turn.")
    k = state.config.moves_per_bonus
    i = state.to_move - 1
    remaining = (
        (state.remaining[0] - 1, state.remaining[1])
        if i == 0
        else (state.remaining[0], state.remaining[1] - 1)
    )
    return replace(state, bonus_active=True, moves_left=k, turn_moves=k, remaining=remaining)


def _end_turn(state: VariantState) -> VariantState:
    pass_only = state.placed_this_turn == 0
    streak = state.pass_streak + 1 if pass_only else 0
    ended = replace(state, moves_left=0, pass_streak=streak)
    if streak >= 2:
        return _finish(ended, "passes")
    return replace(
        ended,
        to_move=opponent(state.to_move),
        phase=TURN_START,
        turn_moves=0,
        bonus_active=False,
        placed_this_turn=0,
    )


def _finish(state: VariantState, reason: Literal["passes", "truncated"]) -> VariantState:
    score = area_score(state.board, state.config.komi)
    return replace(state, phase=TERMINAL, moves_left=0, bonus_active=False, result=Result(reason, score))


def public_view(state: VariantState) -> dict[str, object]:
    """JSON for the browser: everything public, nothing hidden (there is nothing hidden here)."""
    cfg = state.config
    n = cfg.size
    pb = state.trigger_probability(BLACK)
    pw = state.trigger_probability(WHITE)
    return {
        "config": cfg.public(),
        "size": n,
        "grid": state.board.grid(),
        "toMove": COLOR_NAMES[state.to_move],
        "phase": state.phase,
        "movesLeft": state.moves_left,
        "turnMoves": state.turn_moves,
        "bonusActive": state.bonus_active,
        "placedThisTurn": state.placed_this_turn,
        "remaining": {"B": state.remaining[0], "W": state.remaining[1]},
        "turnsStarted": {"B": state.turns_started[0], "W": state.turns_started[1]},
        "eligibleTurnsLeft": {
            "B": max(0, cfg.horizon - state.turns_started[0]),
            "W": max(0, cfg.horizon - state.turns_started[1]),
        },
        # P(next personal turn is a bonus); for the player in turn this is about their *next* turn
        "nextTurnBonusProbability": {"B": pb, "W": pw},
        "passStreak": state.pass_streak,
        "placements": state.placements,
        "captures": {"B": state.captures[0], "W": state.captures[1]},
        "lastPlacement": (
            {
                "x": state.recent[-1][0] % n,
                "y": state.recent[-1][0] // n,
                "color": COLOR_NAMES[state.recent[-1][1]],
            }
            if state.recent
            else None
        ),
        "historyKnown": state.history_known,
        "legal": [a for a in state.legal_actions() if a != cfg.pass_action],
        "canUseBonus": state.can_use_bonus,
        "result": state.result.public() if state.result else None,
    }
