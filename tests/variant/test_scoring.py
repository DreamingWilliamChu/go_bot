import json
from pathlib import Path

import pytest

from gobot.variant.rules import Board
from gobot.variant.scoring import area_score

CASES = json.loads((Path(__file__).parents[1] / "fixtures" / "variant" / "scoring_cases.json").read_text())[
    "cases"
]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_area_scoring_cases(case: dict) -> None:
    s = area_score(Board.from_rows(case["rows"]), case["komi"])
    assert (s.black_stones, s.white_stones) == (case["black_stones"], case["white_stones"])
    assert (s.black_area, s.white_area) == (case["black_area"], case["white_area"])
    assert s.black_total == s.black_area
    assert s.white_total == s.white_area + case["komi"]
    assert s.lead == pytest.approx(case["lead"])
    # area includes the stones; never stones + area
    assert s.black_area >= s.black_stones and s.white_area >= s.white_stones
    assert s.black_value == (1.0 if s.lead > 0 else 0.0 if s.lead < 0 else 0.5)
    owned = sum(1 for o in s.owner if o == 1), sum(1 for o in s.owner if o == -1)
    assert owned == (s.black_area, s.white_area)
