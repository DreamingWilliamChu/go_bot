# Photo recognition: design and measured accuracy

## Pipeline

1. **Decode and normalise** (`src/gobot/vision/image_io.py`).
   - Applies the EXIF orientation; iPhone portrait photos are stored sideways with tag 6.
   - Decodes HEIC/HEIF (pillow-heif, with a `sips` fallback on macOS), JPEG, PNG and WebP.
   - Downscales to a 2048 px long side. The browser displays exactly this image, so pixel coordinates agree.
   - Gives retake hints for dark, blurry or tiny photos.
2. **Find the grid.** Two independent sources of candidates are scored by the *same* grid check:
   - **Classical** (`board_detect.py`):
     - Board quadrilaterals come from colour and edge segmentation.
     - Each candidate is warped and N equally spaced lines are fitted in each direction.
     - The homography is refined with RANSAC on detected line crossings.
   - **moku-v4** (`moku.py`, optional, AGPL-3.0): its `corner_points` output gives corner candidates, which are refined on the grid lines.
   - **Grid check** (`grid_quality`):
     - Line response on the predicted lines vs. half a cell away.
     - Share of visible crossings within 0.12 cell of the prediction.
     - A penalty when the perpendicular lines *continue past* a side (a shifted fit, or the wrong board size).
   - Below a score of 0.48 the app does **not** guess. It asks you to drag the four corners onto the outermost line crossings, showing a live grid preview and a magnifier.
3. **Classify stones** (`stones.py`), per intersection, on the rectified image:
   - Lightness and chroma relative to a smooth board-colour model.
   - Whether the grid crossing at the stone's base is visible (edge-aware).
   - Circular edge evidence.
   - A per-photo parallax offset, because stones stand up from the board.

   Two logistic models turn these features into P(stone) and P(black | stone). They were fitted **only** on the kaya-go/moku-v3 *validation* split (`python -m gobot.eval.fit_stone_model`).
4. **Fuse and flag.** Where the classical classifier is unsure and moku sees a stone, moku's call is used. Every point where the two disagree, or where confidence is below 0.8, is shown with a **?**. The user corrects by tapping, and nothing is analysed until they press *Confirm position*.

## Test sets

| Set | What it is | Labels | License / use |
|---|---|---|---|
| Tengen-Go | 68 real phone/camera photos of 13×13 boards (angled, hard light, empty 9/13/19, a game sequence). Never used for fitting or tuning, and independent of moku. | Positions for 60 photos; grid corners for 26 | AGPL-3.0, pinned commit |
| moku-v3 test | 50 Roboflow-derived photos, many very oblique, resized and squashed to 640×640 | Stones and corners, converted to positions (size inferred); some corner labels sit on the board edge | No stated license; evaluation only |
| goban_data_set | 494 photos of 19×19 boards (every 4th used: 122) | Grid corners only | MIT |
| synthetic | 36 rendered 9/13/19 photos with tilt, lighting gradient, shadow, blur, JPEG and parallax | Exact | Generated (`gobot.eval.synthetic`) |

Run it yourself:

```bash
./scripts/fetch_test_data.sh
uv run gobot eval
```

Results are written to `data/eval/recognition.md`. Photos you save from the app with "Save as test case" are evaluated as the set `user`.

## Results (measured 2026-10-19, cloud x86 CPU, automatic corners)

"Silent errors" are wrong points that were **not** marked "?". "Perfect" means every intersection is right. Tengen per-photo labels are compared in photo orientation (strict); the other sets allow the 4 rotations because their labels are orientation-free.

| Set | Detector | Photos | Grid found | Corners ≤ ½ cell | Wrong grids accepted | Boards scored | Perfect boards | Point accuracy | Errors / board | Flagged '?' / board | Silent errors / board | s / photo |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| tengen | classical | 68 | 68 (100.0%) | 100.0% (n=26) | 0 | 60 | 41 (68.3%) | 99.7% | 0.45 | 3.93 | 0.20 | 1.1 |
| tengen | classical+moku | 68 | 68 (100.0%) | 100.0% (n=26) | 0 | 60 | 45 (75.0%) | 99.8% | 0.25 | 4.18 | 0.00 | 1.5 |
| moku-v3-test | classical | 44 | 31 (70.5%) | 100.0% (n=31) | 0 | 31 | 13 (41.9%) | 99.2% | 2.71 | 8.65 | 0.84 | 2.1 |
| moku-v3-test | classical+moku | 44 | 41 (93.2%) | 100.0% (n=41) | 0 | 41 | 19 (46.3%) | 99.5% | 1.71 | 10.02 | 0.37 | 2.8 |
| goban_data_set | classical | 122 | 103 (84.4%) | 100.0% (n=103) | 0 | – | – | – | – | – | – | 2.5 |
| goban_data_set | classical+moku | 122 | 116 (95.1%) | 100.0% (n=116) | 0 | – | – | – | – | – | – | 2.8 |
| synthetic | classical | 36 | 36 (100.0%) | 100.0% (n=36) | 0 | 36 | 36 (100.0%) | 100.0% | 0.00 | 2.39 | 0.00 | 1.0 |
| synthetic | classical+moku | 36 | 36 (100.0%) | 100.0% (n=36) | 0 | 36 | 35 (97.2%) | 100.0% | 0.03 | 3.42 | 0.00 | 1.4 |

Stones only, using the labelled corners (isolates the classifier from corner finding):

| Set | Boards | Perfect | Point accuracy | Errors / board |
|---|---|---|---|---|
| tengen | 18 | 17 (94.4%) | 99.97% | 0.06 |
| moku-v3 test | 44 | 20 (45.5%) | 97.8% | 7.77 |
| synthetic | 36 | 36 (100.0%) | 100.0% | 0.00 |

The low moku-v3 figure with labelled corners is largely label noise: several of its corner labels are on the board edge rather than the grid, which shifts every intersection.

### What this means

- **The grid is never silently wrong.** No photo in any set was accepted with a grid more than half a cell off. Correct grids scored ≥ 0.53 and wrong ones ≤ 0.42 on 137 labelled photos, and the acceptance threshold is 0.48. The rest (about 5–7% of 19×19 photos, 7% of the oblique moku-v3 set) go to manual corner placement.
- **A wrong board size is flagged, not trusted.** Neighbouring sizes can fit each other's grid well enough to pass the threshold (a 13×13 photo read as 11×11, or a 9×9 read as 19×19). After a grid is accepted, the app scores the photo at every size the same way and warns, with a one-tap retry, when another size fits better by at least 0.01. In the real sets the right size was never outscored, so there were no false warnings; the closest call was an empty 9×9 board that fit 11×11 only 0.015 worse. Of the wrong-size grids that would have been accepted, 112 of 116 were flagged (Tengen-Go 64/64, goban_data_set 48/52, on top of 15/15 synthetic). The four misses are 19×19 photos where automatic detection cannot find the true grid either. The check adds about 1 s per photo.
- **On real 13×13 phone photos** with both detectors (Tengen-Go): 75% of boards are perfect, the average is 0.25 wrong points per board, and **every wrong point was flagged "?"** (0 silent errors). You should expect to check about 4 "?" points per photo.
- **On very oblique, low-resolution photos** (moku-v3), expect 1–3 wrong points per 19×19 board, most of them flagged.
- **moku-v4 helps** on every real set: more grids found, fewer errors, fewer silent errors. It is downloaded by default (`models/moku-v4.onnx`). Without it the app falls back to the classical detector alone.

### Limitations (please read)

- **Real 19×19 and 9×9 photos with full positions** are covered only by the low-resolution moku-v3 test split; real full-resolution 19×19 games are not covered. Tengen-Go is 13×13 and comes from a few physical setups; its 41 game frames come from one game and are correlated.
- **11×11 is tested on synthetic photos only** (all 12 test renders perfect at the right size).
- **moku-v3 test numbers may be optimistic for `classical+moku`.** moku-v4 was trained on data from the same sources (different photos), and the stone classifier's weights were fitted on the moku-v3 validation split.
- **Not tested on iPhone photos or on the M1.** Timings above are from a 4-core x86 cloud VM. Use "Save as test case" on a few of your own photos and run `uv run gobot eval --sets user` on the Mac.
- **Hard cases:** very low camera angles, glare on stones, a board the same colour as the table, stones pushed far off their intersections, and partial boards (all four corners must be visible). One photo cannot show captured stones, move order or a current ko.

## Tips for good photos

- Get all four corners of the grid in the photo.
- Hold the phone above the board, ideally less than about 35° from vertical.
- Use even light, avoid reflections on the stones, and tap to focus.
- Choose the board size before taking the photo. If you choose the wrong size the app says so and offers the size the photo fits.
