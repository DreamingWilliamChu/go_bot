"""The three analysis sources for bonus-turn positions, and how the web process resolves them.

1. ``katago``  - standard KataGo on the current board (ignores future bonuses; see docs/BONUS_GO.md §8)
2. ``variant`` with evaluator ``katago-net`` (or ``heuristic`` without KataGo) - the bonus search with an
   approximate evaluator
3. ``variant`` with evaluator ``net:<model id>`` - the bonus search with a trained variant network,
   offered only for board sizes in the model's metadata
"""

from __future__ import annotations

from typing import Any

from gobot.engine.config import default_engine_settings, write_config
from gobot.engine.models import resolve_model
from gobot.engine.query import Move, Position, Stone
from gobot.variant.models.katago_eval import evaluator_config_settings
from gobot.variant.rules import COLOR_NAMES
from gobot.variant.state import VariantState

EVALUATOR_NET = "tf2-b10c384"  # the light network: fast one-visit answers, little memory
KATAGO_SOURCE_NOTE = (
    "Standard KataGo on the current board: it ignores future bonuses. To show this turn's remaining moves "
    "it lets the opponent only pass inside its search (artificial passes)."
)


def katago_evaluator_spec(engine_service: Any) -> dict[str, Any] | None:
    """Spec for the KataGo evaluator in the search worker, or None when KataGo is not installed."""
    s = engine_service.settings
    binary = s.find_katago()
    if not binary:
        return None
    net, path = resolve_model(EVALUATOR_NET, engine_service.manifest, engine_service.model_dirs)
    if path is None:
        net, path = resolve_model(None, engine_service.manifest, engine_service.model_dirs)
    if path is None:
        return None
    backend = engine_service.probe().get("backend_kind", "other")
    eng = s.load_engine_json()
    base = {**default_engine_settings(engine_service.hardware, backend), **eng.get("katago", {})}
    cfg_path = write_config(s.katago_dir / "variant_eval.cfg", evaluator_config_settings(base), backend)
    return {
        "kind": "katago-net",
        "binary": binary,
        "model_path": str(path),
        "config_path": str(cfg_path),
        "label": net.id if net else path.name,
    }


def snapshot_position(state: VariantState) -> Position:
    """The current board as a fresh KataGo position with the actor to play (no history, no passes)."""
    return Position(
        size=state.config.size,
        initial_stones=[Stone(color=COLOR_NAMES[c], x=x, y=y) for x, y, c in state.board.stones()],  # type: ignore[arg-type]
        initial_player=COLOR_NAMES[state.to_move],  # type: ignore[arg-type]
        moves=list[Move](),
        rules="chinese",
        komi=state.config.komi,
    )


def katago_moves_this_turn(state: VariantState) -> int:
    return max(1, min(10, state.moves_left)) if state.bonus_active else 1


def filter_katago_result(res: dict[str, Any], state: VariantState) -> dict[str, Any]:
    """Drop candidates the variant forbids (superko against positions KataGo never saw) and label."""
    legal = set(state.legal_actions())
    n = state.config.size
    if "candidates" in res:
        res["candidates"] = [c for c in res["candidates"] if c["pass"] or (c["y"] * n + c["x"]) in legal]
    res["source"] = "katago"
    res["evaluator"] = {
        "kind": "katago",
        "label": "Standard KataGo (ignores future bonuses)",
        "modelVersion": None,
        "approximate": True,
        "note": KATAGO_SOURCE_NOTE,
    }
    return res
