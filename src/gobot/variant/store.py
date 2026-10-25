"""Server-side storage of bonus-turn Go games (``data/variant/games/<id>.json``, mode 0600).

The files contain the hidden schedule, so they are only ever read by the server; the browser gets
``public_view`` and exports without it.
"""

from __future__ import annotations

import collections
import contextlib
import json
import os
import re
import time
from pathlib import Path

from gobot.variant.env import VariantGame
from gobot.variant.serialization import game_from_json, game_to_json

ID_RE = re.compile(r"^[0-9a-f]{16}$")
MAX_GAMES = 300
KEEP_SECONDS = 90 * 24 * 3600
CACHE = 32


class GameNotFound(KeyError):
    pass


class GameStore:
    def __init__(self, root: Path):
        self.dir = root / "games"
        self._cache: collections.OrderedDict[str, VariantGame] = collections.OrderedDict()

    def _path(self, game_id: str) -> Path:
        if not ID_RE.match(game_id):
            raise GameNotFound(game_id)
        return self.dir / f"{game_id}.json"

    def save(self, game: VariantGame) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self._path(game.id)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(game_to_json(game, private=True), f, separators=(",", ":"))
        tmp.replace(path)
        self._remember(game)

    def load(self, game_id: str) -> VariantGame:
        g = self._cache.get(game_id)
        if g is not None:
            self._cache.move_to_end(game_id)
            return g
        path = self._path(game_id)
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            raise GameNotFound(game_id) from None
        g = game_from_json(data)
        self._remember(g)
        return g

    def _remember(self, game: VariantGame) -> None:
        self._cache[game.id] = game
        self._cache.move_to_end(game.id)
        while len(self._cache) > CACHE:
            self._cache.popitem(last=False)

    def prune(self) -> int:
        """Remove games untouched for KEEP_SECONDS and keep at most MAX_GAMES."""
        if not self.dir.is_dir():
            return 0
        files = sorted(self.dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        now = time.time()
        removed = 0
        for i, p in enumerate(files):
            if i >= MAX_GAMES or now - p.stat().st_mtime > KEEP_SECONDS:
                with contextlib.suppress(OSError):
                    p.unlink()
                    self._cache.pop(p.stem, None)
                    removed += 1
        return removed
