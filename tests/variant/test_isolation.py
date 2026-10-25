"""Nothing that searches or evaluates can reach the hidden schedule."""

import ast
import dataclasses
import json
from pathlib import Path

import gobot.variant as variant_pkg
from gobot.variant.config import VariantConfig
from gobot.variant.env import VariantGame
from gobot.variant.observation import Observation, observe
from gobot.variant.state import VariantState, public_view

PKG = Path(variant_pkg.__file__).parent
FORBIDDEN_MODULES = {
    "gobot.variant.env",
    "gobot.variant.randomness",
    "gobot.variant.serialization",
    "gobot.variant.store",
}
HIDDEN_WORDS = ("schedule", "seed", "salt", "rng", "random")


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out |= {f"{node.module}.{a.name}" for a in node.names}
    return out


def test_search_models_and_observation_never_import_hidden_modules() -> None:
    files = [PKG / "observation.py", PKG / "state.py"]
    for sub in ("search", "models"):
        files += sorted((PKG / sub).glob("*.py"))
    for f in files:
        bad = {
            m for m in imported_modules(f) if any(m == b or m.startswith(b + ".") for b in FORBIDDEN_MODULES)
        }
        if f.name == "state.py":
            # state.py may use the public probability math only
            bad -= {
                "gobot.variant.randomness",
                "gobot.variant.randomness.check_feasible",
                "gobot.variant.randomness.trigger_probability",
                "gobot.variant.randomness.modelled_probability",
            }
        assert not bad, f"{f.relative_to(PKG)} imports {bad}"


def test_observation_and_state_have_no_hidden_fields() -> None:
    for cls in (Observation, VariantState):
        for f in dataclasses.fields(cls):
            assert not any(w in f.name.lower() for w in HIDDEN_WORDS), f"{cls.__name__}.{f.name}"


def test_public_view_never_contains_hidden_data() -> None:
    g = VariantGame.new(VariantConfig(size=9, black_bonuses=3, white_bonuses=3), seed=123)
    obs = observe(g.state)
    assert isinstance(obs.state, VariantState)
    text = json.dumps(public_view(g.state))
    for w in ("schedule", "seed", "salt"):
        assert w not in text
    # the scheduled turn indices appear nowhere in the observation
    assert not any(
        isinstance(v, frozenset) and v in (g.schedule.black, g.schedule.white) for v in vars_of(obs.state)
    )


def vars_of(s: VariantState) -> list[object]:
    return [getattr(s, f.name) for f in dataclasses.fields(s)]
