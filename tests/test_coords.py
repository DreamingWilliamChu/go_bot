import pytest

from gobot.go.coords import gtp_to_xy, ownership_index, sgf_to_xy, xy_to_gtp, xy_to_sgf


def test_points_roundtrip(coords_fixture):
    for p in coords_fixture["points"]:
        n, x, y = p["size"], p["x"], p["y"]
        assert xy_to_gtp(x, y, n) == p["gtp"], p
        assert gtp_to_xy(p["gtp"], n) == (x, y), p
        assert gtp_to_xy(p["gtp"].lower(), n) == (x, y), p
        assert xy_to_sgf(x, y, n) == p["sgf"], p
        assert sgf_to_xy(p["sgf"], n) == (x, y), p
        assert ownership_index(x, y, n) == p["ownershipIndex"], p


def test_passes(coords_fixture):
    for p in coords_fixture["passes"]:
        assert gtp_to_xy(p["gtp"], p["size"]) is None
        assert sgf_to_xy(p["sgf"], p["size"]) is None


def test_invalid(coords_fixture):
    for p in coords_fixture["invalid"]:
        with pytest.raises(ValueError):
            if "gtp" in p:
                gtp_to_xy(p["gtp"], p["size"])
            else:
                sgf_to_xy(p["sgf"], p["size"])


@pytest.mark.parametrize("n", [9, 13, 19])
def test_every_point_roundtrips(n):
    seen = set()
    for y in range(n):
        for x in range(n):
            g = xy_to_gtp(x, y, n)
            assert "I" not in g[0]
            assert gtp_to_xy(g, n) == (x, y)
            assert sgf_to_xy(xy_to_sgf(x, y, n), n) == (x, y)
            seen.add(g)
    assert len(seen) == n * n


def test_off_board_raises():
    with pytest.raises(ValueError):
        xy_to_gtp(19, 0, 19)
    with pytest.raises(ValueError):
        xy_to_sgf(0, 9, 9)
