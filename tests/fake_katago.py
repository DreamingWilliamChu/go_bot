#!/usr/bin/env python3
"""Test double for KataGo's analysis protocol, used ONLY to reproduce failure modes
(crash, hang, slow terminate) that the real engine cannot be made to exhibit on demand.
It produces obviously synthetic numbers and is never used by the application itself.

Usage: fake_katago.py analysis -config CFG -model MODEL   (mode from env FAKE_KATAGO_MODE)
Modes: normal | crash_on_query | hang | no_terminate | exit_on_start | garbage_stdout
"""

import json
import os
import sys
import threading
import time

MODE = os.environ.get("FAKE_KATAGO_MODE", "normal")


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "version":
        print("KataGo v0.0-fake\nUsing FAKE backend")
        return
    if MODE == "exit_on_start":
        sys.stderr.write("fake: failed to load model\n")
        sys.exit(3)
    if MODE == "garbage_stdout":
        sys.stdout.write("Error: No suitable fusermount binary found on the $PATH\n")
        sys.stdout.flush()
    sys.stderr.write("fake: Model name: fake-net\n")
    sys.stderr.flush()
    running = {}

    def search(q):
        qid = q["id"]
        max_time = q.get("overrideSettings", {}).get("maxTime", 1.0)
        every = q.get("reportDuringSearchEvery")
        t0 = time.time()
        visits = 0
        last = t0
        while time.time() - t0 < max_time:
            if running.get(qid) == "stop":
                break
            time.sleep(0.02)
            visits += 1
            if every and time.time() - last >= every:
                last = time.time()
                out(resp(q, visits, True))
        out(resp(q, visits, False))
        running.pop(qid, None)

    for line in sys.stdin:
        q = json.loads(line)
        if q.get("action") == "query_version":
            out({**q, "version": "0.0-fake", "git_hash": "fake"})
            continue
        if q.get("action") == "query_models":
            out({**q, "models": [{"name": "fake.bin.gz", "internalName": "fake-net"}]})
            continue
        if q.get("action") == "terminate":
            out(q)
            if MODE != "no_terminate":
                running[q["terminateId"]] = "stop"
            continue
        if q.get("action") == "terminate_all":
            out(q)
            for k in running:
                running[k] = "stop"
            continue
        if MODE == "crash_on_query":
            sys.stderr.write("fake: segfault simulated\n")
            sys.stderr.flush()
            os._exit(139)
        if MODE == "hang":
            continue
        if q.get("moves") and q["moves"][-1][1] == "Z99":
            out({"error": "Illegal move", "field": "moves", "id": q["id"]})
            continue
        running[q["id"]] = "run"
        threading.Thread(target=search, args=(q,), daemon=True).start()


def resp(q, visits, during):
    n = q["boardXSize"]
    player = q.get("initialPlayer", "B")
    if q.get("moves"):
        player = "W" if q["moves"][-1][0] == "B" else "B"
    return {
        "id": q["id"],
        "isDuringSearch": during,
        "turnNumber": len(q.get("moves", [])),
        "rootInfo": {"currentPlayer": player, "visits": visits, "winrate": 0.5, "scoreLead": 0.0},
        "moveInfos": [
            {
                "move": "A1",
                "order": 0,
                "visits": visits,
                "winrate": 0.5,
                "scoreLead": 0.0,
                "prior": 1.0,
                "pv": ["A1"],
            }
        ],
        "ownership": [0.0] * (n * n),
    }


if __name__ == "__main__":
    main()
