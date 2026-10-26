import itertools
import random
from collections import Counter

import pytest
from pydantic import ValidationError

from gobot.variant.config import VariantConfig
from gobot.variant.randomness import (
    HiddenSchedule,
    commitment,
    sample_conditional,
    sample_schedule,
    trigger_probability,
)


def test_schedule_is_a_valid_distinct_subset() -> None:
    rng = random.Random(5)
    for _ in range(200):
        s = sample_schedule(2, 3, 6, rng)
        assert len(s.black) == 2 and len(s.white) == 3
        assert all(1 <= t <= 6 for t in s.black | s.white)
    assert sample_schedule(0, 0, 30, rng) == HiddenSchedule(frozenset(), frozenset())
    assert sample_schedule(30, 0, 30, rng).black == frozenset(range(1, 31))


def test_schedule_is_uniform_over_subsets() -> None:
    rng = random.Random(11)
    h, a, n = 6, 2, 15000
    counts = Counter(tuple(sorted(sample_schedule(a, 0, h, rng).black)) for _ in range(n))
    subsets = list(itertools.combinations(range(1, h + 1), a))
    assert set(counts) == set(subsets)
    expected = n / len(subsets)
    chi2 = sum((counts[s] - expected) ** 2 / expected for s in subsets)
    assert chi2 < 36.1  # chi-square, 14 degrees of freedom, p = 0.001


def test_conditional_trigger_probability_matches_simulation() -> None:
    rng = random.Random(3)
    h, a = 8, 3
    hits: Counter[tuple[int, int]] = Counter()
    seen: Counter[tuple[int, int]] = Counter()
    for _ in range(40000):
        sched = sample_schedule(a, 0, h, rng).black
        r = a
        for t in range(h):  # t personal turns already started, r bonuses left
            seen[(t, r)] += 1
            if t + 1 in sched:
                hits[(t, r)] += 1
                r -= 1
    for (t, r), n in seen.items():
        if n < 300:
            continue
        p = trigger_probability(r, t, h)
        assert p == pytest.approx(r / (h - t))
        emp = hits[(t, r)] / n
        se = max((p * (1 - p) / n) ** 0.5, 1e-9)
        assert abs(emp - p) < 4.5 * se + 1e-9, (t, r, emp, p)


def test_trigger_probability_edges() -> None:
    assert trigger_probability(0, 0, 30) == 0.0
    assert trigger_probability(2, 28, 30) == 1.0
    assert trigger_probability(0, 30, 30) == 0.0
    assert trigger_probability(0, 35, 30) == 0.0  # games can outlast the horizon
    with pytest.raises(ValueError):
        trigger_probability(3, 28, 30)  # more left than eligible turns
    with pytest.raises(ValueError):
        trigger_probability(1, 30, 30)


def test_conditional_resampling_for_imports() -> None:
    rng = random.Random(9)
    counts: Counter[int] = Counter()
    for _ in range(6000):
        s = sample_conditional((2, 0), (10, 4), 30, rng)
        assert len(s.black) == 2 and all(11 <= t <= 30 for t in s.black) and not s.white
        counts.update(s.black)
    assert set(counts) == set(range(11, 31))
    assert max(counts.values()) / min(counts.values()) < 1.35  # each eligible turn about 600 times


def test_commitment_binds_schedule_and_salt() -> None:
    s = HiddenSchedule(frozenset({1, 5}), frozenset({2}))
    c = commitment(s, "salt")
    assert c == commitment(HiddenSchedule.from_json(s.to_json()), "salt")
    assert c != commitment(s, "other") and c != commitment(
        HiddenSchedule(frozenset({1, 6}), frozenset({2})), "salt"
    )


def test_allocations_must_fit_the_horizon() -> None:
    assert VariantConfig(size=11).horizon == 30
    assert VariantConfig(size=11, black_bonuses=30, white_bonuses=0).black_bonuses == 30
    with pytest.raises(ValidationError):
        VariantConfig(size=11, black_bonuses=31)
    with pytest.raises(ValidationError):
        VariantConfig(size=11, white_bonuses=-1)
