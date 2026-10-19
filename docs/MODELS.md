# KataGo engine and networks

## Engine

| | Mac (production) | Linux (tests / CI only) |
|---|---|---|
| Install | `brew install katago` (done by `setup_mac.sh`) | `scripts/fetch_ci_engine.sh` |
| Version | 1.18.2 Homebrew formula (source `v1.18.2.tar.gz`, sha256 `fefd554b…f8bf9`). Upstream release notes: 1.18.2 changes only CUDA; 1.18.1 is the latest for all other backends | Official `katago-v1.18.1-eigenavx2-linux-x64.zip` (sha256 `33e79780…20d79`) |
| Backend | **Metal**: GPU via MPSGraph, optionally the Apple Neural Engine via CoreML. Docker is not used | Eigen (CPU, AVX2) |

KataGo does not publish macOS binaries on GitHub; upstream recommends Homebrew for the Metal build. On macOS 15, 26 and 27 Homebrew installs a prebuilt bottle; otherwise it builds from source with the Xcode Command Line Tools.

The app talks to `katago analysis` over its JSON protocol through one persistent process (`src/gobot/engine/katago.py`). It writes the config to `data/katago/analysis.cfg` from `data/hardware.json` and `data/engine.json`:

- `reportAnalysisWinratesAs = BLACK`
- the search-thread count and batch size from the benchmark
- `nnCacheSizePowerOfTwo` 17 on 8 GB Macs (about 0.4 GB maximum cache), 18 on 16 GB, 20 above
- Metal mode `gpu` (default) or `mux` (2 GPU + 2 Neural Engine server threads, upstream's recommendation for throughput), whichever the benchmark measured faster

## Networks

All candidates are from KataGo's public training run (katagotraining.org), under the MIT-style KataGo Neural Network License. Versions, URLs, sizes and sha256 are in [`models/manifest.json`](../models/manifest.json); checksums were computed from the downloaded files.

| id | File | Architecture | kata1 Elo¹ | Size | Role |
|---|---|---|---|---|---|
| `tf3-b11c768` | `kata1-tf3-b11c768-s11003M-d5973M-7gres` | transformer, 70.4M params | 14602 (strongest confidently rated, 2026-10-19) | 262 MB | max strength (`--max`) |
| `tf3-b10c512` | `kata1-tf3-b10c512-s3203M-d5937M` | transformer, 28.5M | 14167 | 94 MB | strong default before benchmarking |
| `b28c512-conv` | `kata1-b28c512nbt-s13255194368-d5935380940` | convolutional, 72.8M | 14103 | 271 MB | optional comparison (`--all`) |
| `tf2-b10c384` | `kata1-tf2-b10c384-s2941M-d5872M` | transformer, 10.5M | 13707 | 38 MB | light / fast fallback |
| `b18c384-conv` | `kata1-b18c384nbt-s9996604416-d4316597426` | convolutional, 26.3M | 13620 | 98 MB | compatibility fallback (Homebrew ships it) |

¹ Elo from kata1 rating games, copied from katagotraining.org. It compares the networks under the training run's rating settings. **It is not a fixed-thinking-time result on an M1**: a stronger-per-evaluation network that runs 3× slower on your Mac can be weaker at 6 seconds. This is why the default is chosen by measurement.

### Why these candidates

- Since v1.17, KataGo supports transformer networks. Upstream reports them as stronger per evaluation and "usually as fast or faster" than the convolutional nets of similar strength, but that was measured on NVIDIA GPUs. The kata1 run now trains transformers: tf3-b10c512 is rated above the best b28 convolutional net, and tf2-b10c384 above the best b18.
- **Metal compatibility.** KataGo 1.18.1's Metal backend implements the transformer blocks (RMSNorm, attention, and SwiGLU feed-forward only; `cpp/neuralnet/metalbackend.cpp`). The Eigen backend has the same SwiGLU-only restriction, and all five networks above **load and analyse on Eigen** in the cloud tests, so they use supported block types. Loading on Metal is **still to be confirmed on the M1** (checklist below).
- The b18 convolutional net is the safety net: KataGo has supported this architecture on Metal for longest, and Homebrew bundles it, so it works offline without any download.

### Default selection (assumption, made explicit)

`./scripts/benchmark.sh` measures each installed network on your Mac:

- `katago benchmark` visits/s over several thread counts, in Metal GPU and GPU+ANE modes;
- real analysis through the app on representative positions (19×19 opening, middle game and endgame; 13×13; 9×9) for each preset;
- peak memory.

It then picks the network with the highest **effective Elo** among those whose peak RSS is at most 35% of RAM, and keeps the fastest one as the light fallback:

```
effective Elo = kata1 Elo − 150 × log2(1000 / visits)    (only when visits < 1000)
```

where *visits* is what the network reached in the Standard (6 s) preset on the 19×19 middle-game position. In words: a network that reaches 1,000 visits keeps its full rating, and each halving below that costs it 150 Elo. Examples: 14167 at 500 visits → 14017; 14602 at 360 visits → 14381; 13707 at 12 visits → 12750.

**The 150 Elo per halving is an assumption**, not a measurement on your Mac. It is a rough figure for the value of doubling the search at low visit counts; the true value varies with the network and the position. It makes the trade-off explicit: a stronger network wins unless it is much slower. Override the choice any time on the Engine tab or with `--model`.

Until you run the benchmark, the app uses `tf3-b10c512` if it is downloaded.

## Measurements

### M1 MacBook Air: pending

Not measured, because no Mac was available here. Please run:

```bash
./scripts/benchmark.sh --quick     # ~10-20 min
cat data/benchmark/report.md
```

or `./scripts/benchmark.sh` (full: 5 thread counts and both Metal modes, 30-90 min). The report lists for each network: visits/s, best thread count, visits reached per preset on each position, peak memory and start-up time (the first Metal/CoreML start can take longer).

### Cloud CPU (not representative of the Mac)

Measured with `gobot bench --quick --visits 24` on the cloud VM used for development: Intel Xeon 2.1 GHz, 4 cores, no GPU, 15.7 GB RAM, official KataGo 1.18.1 **Eigen (CPU)** build. These numbers say nothing about Metal speed on an M1; they are here only to show that the benchmark runs end to end and how the selection rule behaves.

| Network | Best threads | visits/s (`katago benchmark`) | Standard (6 s) visits, 19×19 middle | Quick (2 s) visits, 19×19 middle | 13×13 Quick / Standard visits | Peak RSS | Start-up |
|---|---|---|---|---|---|---|---|
| `b18c384-conv` | 4 | 6.79 (4.23 with 2 threads) | 72 | 14 | 15 / 78 | 741 MB | 2.9 s |
| `tf2-b10c384` | 4 | 1.05 (0.58 with 2 threads) | 12 | 5 | 5 / 19 | 186 MB | 0.9 s |
| `tf3-b10c512` | 2 | 0.10 (about 0.14-0.18 with 4 threads, partial) | not run¹ | not run¹ | not run¹ | 444 MB | – |

¹ `katago benchmark` for tf3-b10c512 hit the tool's 3,600 s limit, so its analysis step was skipped. `tf3-b11c768` and `b28c512-conv` were not benchmarked here (they load and answer in the integration tests).

On this CPU the transformer networks were **about 6× (tf2-b10c384) and 40× (tf3-b10c512) slower** than the convolutional b18 at equal thread counts, the opposite of upstream's NVIDIA results. Applied to these results, the rule above selects **`b18c384-conv`** (effective Elo 13051 vs 12750 for tf2-b10c384; the run itself still used the earlier rule, which picked tf2). The Metal backend on the M1 GPU may rank them differently, which is why the default must be measured on the Mac rather than copied from here.

Note: the quick visit counts include process warm-up on a slow CPU (for example tf2 took 8.9 s to return its "2 s" Quick result), so they understate steady-state speed.

## Thinking-time presets

| Preset | Time per analysis | Notes |
|---|---|---|
| Quick | 2 s | instant feedback while entering moves |
| Standard | 6 s | default |
| Deep | 20 s | stronger, for key positions |
| Custom | 0.5-600 s | set on the Board tab |

Analysis streams partial results every 0.5 s, so you see the evaluation develop. **Stop** ends a search early and keeps what it found.
