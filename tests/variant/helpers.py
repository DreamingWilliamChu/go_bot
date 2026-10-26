from gobot.variant.config import VariantConfig
from gobot.variant.env import Environment
from gobot.variant.randomness import HiddenSchedule
from gobot.variant.rules import Board
from gobot.variant.state import initial_state


def env_with(
    rows: list[str] | None = None,
    *,
    size: int | None = None,
    black: set[int] = frozenset(),  # type: ignore[assignment]
    white: set[int] = frozenset(),  # type: ignore[assignment]
    horizon: int = 10,
    k: int = 3,
    komi: float = 0.5,
    max_placements: int | None = None,
    to_move: int = 1,
) -> Environment:
    """An environment with an explicit (test-only) schedule."""
    board = Board.from_rows(rows) if rows else Board.empty(size or 5)
    cfg = VariantConfig(
        size=board.n,
        komi=komi,
        black_bonuses=len(black),
        white_bonuses=len(white),
        moves_per_bonus=k,
        horizon=horizon,
        max_placements=max_placements,
    )
    return Environment(initial_state(cfg, board, to_move), HiddenSchedule(frozenset(black), frozenset(white)))


def act(env: Environment, x: int, y: int) -> list[dict]:
    return env.step(y * env.state.config.size + x)


def pass_(env: Environment) -> list[dict]:
    return env.step(env.state.config.pass_action)
