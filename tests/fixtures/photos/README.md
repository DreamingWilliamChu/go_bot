# Test photos

These photos and their labels are copied unchanged from
[OliverBenz/Tengen-Go](https://github.com/OliverBenz/Tengen-Go) at commit
`ca8748034f71b558d109d65e2c07466ee8ec66b9`, `tests/vision/resources/`:

| File here | Upstream file |
|---|---|
| `tengen_angled_easy_1.jpeg` / `.json` | `angled_easy/angle_1.jpeg` / `.json` |
| `tengen_angled_easy_board.txt` | `angled_easy/board.txt` |
| `tengen_hard_lighting_2.jpeg` / `.json` | `angled_hard_lighting/angle_2.jpeg` / `.json` |
| `tengen_hard_lighting_board.txt` | `angled_hard_lighting/board.txt` |
| `tengen_empty_19.jpeg` / `.json` | `empty_angle_small/size_19.jpeg` / `.json` |

License: GNU AGPL-3.0 (same as this repository). Copyright the Tengen-Go authors.
`board.txt` uses Tengen's dotBW format (`.` empty, `B`, `W`); the JSON `gridCorners` are the
four outermost grid intersections in photo pixels, in no particular order. The board labels may be
in any of the four rotations.

The full evaluation sets are downloaded by `scripts/fetch_test_data.sh` and are not committed.
