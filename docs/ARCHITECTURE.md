# Architecture

```
iPhone Safari / desktop browser                         Mac (Apple Silicon)
┌───────────────────────────────┐   HTTP(S) + WebSocket   ┌──────────────────────────────────────────┐
│ Preact + TypeScript app       │ ─────────────────────▶ │ FastAPI (uvicorn), one origin              │
│  board, SGF, rules (@sabaki)  │   /api/photos …        │  ├─ vision: OpenCV + moku-v4 (ONNX, CPU)   │
│  photo capture / correction   │   /api/analyze (ws)    │  ├─ engine manager ── stdin/stdout JSON ──┐│
│  analysis display             │ ◀───────────────────── │  └─ static frontend (frontend/dist)       ││
└───────────────────────────────┘                        │        katago analysis (Metal) ◀─────────┘│
                                                         └──────────────────────────────────────────┘
```

## Backend (`src/gobot`)

| Module | Role |
|---|---|
| `app.py` | FastAPI app: `/api/status`, `/api/models`, `/api/engine/*`, pairing, `/api/analyze` websocket, static files |
| `auth.py` | Pairing token, HttpOnly cookie, ASGI middleware (HTTP and websocket). Loopback is always allowed, except on the tunnel port (`TunnelMarker`) |
| `accounts.py` | Sign-in account: scrypt password hash, hashed session cookies, failed-sign-in limits (`data/account.json`) |
| `tunnel.py` | Starts/stops `cloudflared` (quick or named tunnel) and reads the public address from its log |
| `web/account.py` | `/api/auth/info`, `/api/login`, `/api/logout`, `/api/account`, `/api/tunnel` |
| `server.py`, `certs.py`, `netinfo.py` | Startup banner and QR code, LAN address, local CA and HTTPS, CA download helper |
| `engine/katago.py` | Persistent `katago analysis` process; request ids, terminate, timeouts, crash and hang recovery, stale-result suppression, model fallback chain |
| `engine/query.py` | Builds queries (`initialStones` + `initialPlayer`, then real `moves`) and normalises responses (Black perspective, explicit field names) |
| `engine/config.py`, `engine/service.py`, `engine/models.py` | Per-host config (Metal GPU/ANE, threads, cache), model manifest, verified downloads |
| `vision/*` | Photo normalisation, grid detection, stone classification, moku fusion (see RECOGNITION.md) |
| `bench/*`, `eval/*` | Benchmark and recognition evaluation tools |

### Analysis request lifecycle

1. The browser sends `{"type":"analyze","requestId":"r7",…}` over the websocket.
2. The manager gives the query a unique id (`<client>-<seq>`). It sends `terminate` for that client's previous query, so only one search per client runs.
3. KataGo streams partial results (`reportDuringSearchEvery = 0.5 s`) and then exactly one final result. Responses for superseded ids are drained but never forwarded. The browser also drops any message whose `requestId` is not its current one and records which game revision a result belongs to, so an old result can never be drawn on a newer position.
4. If there is no final answer by `maxTime + grace`, the manager sends `terminate`. If KataGo still says nothing, it restarts the engine and reports the error. A crash (EOF on stdout) fails all pending requests with the last log lines, and the next request restarts the engine.
5. If the chosen network cannot start (missing file, Metal error, out of memory), the manager tries the fallback chain from `data/engine.json` (default: `tf2-b10c384`, then `b18c384-conv`) and says so in the UI.

### Perspective

The KataGo config always sets `reportAnalysisWinratesAs = BLACK`. The API names the fields `blackWinrate`, `blackScoreLead` and `blackOwnership`. The UI always says "Black's win probability", shows White's alongside, and writes the score as "Black +2.3" or "White +0.5". Candidate moves show the win probability of the player to move ("White win 63%").

### Coordinates

Everything internal uses `(x, y)` with `y` counted from the top. That matches SGF (`aa` = top-left), the rectified photo, KataGo's ownership array and Sabaki. GTP strings (`Q16`, no `I`) are produced only at the KataGo boundary. Python and TypeScript are both tested against `tests/fixtures/coords.json`.

## Frontend (`frontend/src`)

- `game/`: immutable game state built on `@sabaki/go-board` (captures, suicide, simple ko; KataGo enforces the full ruleset) and SGF via `@sabaki/sgf`.
- `board/BoardView.tsx`: SVG board with candidates, PV numbers, ownership, "?" marks.
- `analysis/`: websocket hook and panel (presets, top-3, PV stepping, engine info, summary text built only from KataGo numbers).
- `photo/` and `screens/PhotoScreen.tsx`: capture, corner editor (loupe, live grid preview), review and correction, orientation, confirmation.

## Data on disk

| Path | Content | In git |
|---|---|---|
| `models/manifest.json` | Exact network and recognition-model versions, URLs, sizes, sha256 | yes |
| `models/*.bin.gz`, `models/*.onnx` | Downloaded weights | no |
| `data/engine.json` | Selected network and KataGo settings (from the benchmark) | no |
| `data/katago/analysis.cfg` | Generated KataGo config | no |
| `data/token`, `data/certs/` | Pairing code, local CA and server certificate | no |
| `data/account.json`, `data/tunnel_token` | Sign-in account and sessions (hashed); optional named-tunnel token | no |
| `data/uploads/` | Normalised photos (pruned after 7 days or 300 files) | no |
| `data/user_testset/` | Photos you saved as recognition test cases | no |
| `data/benchmark/`, `data/eval/` | Benchmark and evaluation reports | no |
