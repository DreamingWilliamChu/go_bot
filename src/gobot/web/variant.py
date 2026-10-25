"""REST endpoints for bonus-turn Go games (docs/BONUS_GO.md).

The server owns each game and its hidden schedule. A client sends *actions* (place, pass, undo, redo)
with the revision it last saw; the server decides who moves, so no click can grant moves, and a stale
revision is answered with 409 and the current game.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError, field_validator

from gobot.go.coords import SUPPORTED_SIZES
from gobot.settings import Settings
from gobot.variant.config import DEFAULT_BONUSES, VariantConfig
from gobot.variant.env import VariantGame
from gobot.variant.rules import COLOR_OF, Board, IllegalMove
from gobot.variant.serialization import RecordError, encode_action, game_from_export, game_to_json
from gobot.variant.state import imported_state, public_view
from gobot.variant.store import GameNotFound, GameStore


class ConfigIn(BaseModel):
    size: int = 11
    komi: float = 7.5
    black_bonuses: int = Field(default=DEFAULT_BONUSES, ge=0)
    white_bonuses: int = Field(default=DEFAULT_BONUSES, ge=0)
    moves_per_bonus: int = 3
    horizon: int | None = None  # None: default for the board size
    trigger: Literal["choice", "random"] = "choice"  # the app's default: players decide

    @field_validator("size")
    @classmethod
    def _size(cls, v: int) -> int:
        if v not in SUPPORTED_SIZES:
            raise ValueError(f"board size must be one of {SUPPORTED_SIZES}")
        return v

    def build(self) -> VariantConfig:
        d = self.model_dump()
        if d["horizon"] is None:
            d.pop("horizon")
        try:
            return VariantConfig.model_validate(d)
        except ValidationError as e:
            raise HTTPException(422, _first_error(e)) from e


class CreateRequest(BaseModel):
    config: ConfigIn = Field(default_factory=ConfigIn)


class Action(BaseModel):
    type: Literal["place", "pass", "bonus", "undo", "redo"]
    x: int | None = None
    y: int | None = None


class ActionRequest(BaseModel):
    expectedRevision: int
    action: Action


class StoneIn(BaseModel):
    color: Literal["B", "W"]
    x: int
    y: int


class PerColor(BaseModel):
    B: int = Field(ge=0)
    W: int = Field(ge=0)


class ImportRequest(BaseModel):
    """A position with an explicitly entered bonus state (photo or analysis branch)."""

    config: ConfigIn
    stones: list[StoneIn]
    toMove: Literal["B", "W"]
    remaining: PerColor
    turnsStarted: PerColor  # personal turns started, the current one of toMove included
    bonusActive: bool = False
    movesLeft: int = 1
    passStreak: int = 0
    provenance: Literal["photo", "branch"] = "branch"


def _first_error(e: ValidationError) -> str:
    errs = e.errors()
    return str(errs[0].get("msg")) if errs else str(e)


def game_view(game: VariantGame, new_events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    n = game.initial.config.size
    return {
        "gameId": game.id,
        "revision": game.revision,
        "provenance": game.provenance,
        "commitment": game.commitment,
        "state": public_view(game.state),
        "events": game.env.events,
        "newEvents": new_events or [],
        "actions": [encode_action(a, n) for a in game.actions],
        "canUndo": bool(game.actions),
        "canRedo": bool(game.redo_stack),
        # revealed only when the game is over, so the commitment can be checked
        "reveal": {"salt": game.salt, "schedule": game.schedule.to_json()} if game.state.terminal else None,
    }


def build_router(settings: Settings) -> tuple[APIRouter, GameStore]:
    store = GameStore(settings.variant_dir)
    router = APIRouter(prefix="/api/variant")

    def get_game(game_id: str) -> VariantGame:
        try:
            return store.load(game_id)
        except GameNotFound:
            raise HTTPException(404, "That bonus game no longer exists on the Mac.") from None
        except RecordError as e:
            raise HTTPException(500, f"Bonus game file is damaged: {e}") from e

    @router.post("/games")
    async def create(body: CreateRequest) -> dict[str, Any]:
        game = VariantGame.new(body.config.build())
        store.save(game)
        store.prune()
        return game_view(game, game.env.events)

    @router.get("/games/{game_id}")
    async def get(game_id: str) -> dict[str, Any]:
        return game_view(get_game(game_id))

    @router.post("/games/{game_id}/actions")
    async def act(game_id: str, body: ActionRequest) -> dict[str, Any]:
        game = get_game(game_id)
        if body.expectedRevision != game.revision:
            raise HTTPException(
                409, {"message": "The game changed on another device.", "game": game_view(game)}
            )
        a = body.action
        events: list[dict[str, Any]] = []
        try:
            if a.type == "undo":
                if not game.undo():
                    raise HTTPException(400, "Nothing to undo.")
            elif a.type == "redo":
                if not game.redo():
                    raise HTTPException(400, "Nothing to redo.")
            else:
                cfg = game.state.config
                if a.type == "pass":
                    action = cfg.pass_action
                elif a.type == "bonus":
                    action = cfg.bonus_action
                else:
                    if a.x is None or a.y is None or not (0 <= a.x < cfg.size and 0 <= a.y < cfg.size):
                        raise HTTPException(400, "A placement needs a point on the board.")
                    action = a.y * cfg.size + a.x
                events = game.play(action)
        except IllegalMove as e:
            raise HTTPException(400, {"message": str(e), "reason": e.reason}) from e
        store.save(game)
        return game_view(game, events)

    @router.post("/games/import")
    async def import_position(body: ImportRequest) -> dict[str, Any]:
        cfg = body.config.build()
        try:
            board = Board.from_stones(cfg.size, [(s.x, s.y, COLOR_OF[s.color]) for s in body.stones])
            st = imported_state(
                cfg,
                board,
                COLOR_OF[body.toMove],
                remaining=(body.remaining.B, body.remaining.W),
                turns_started=(body.turnsStarted.B, body.turnsStarted.W),
                bonus_active=body.bonusActive,
                moves_left=body.movesLeft,
                pass_streak=body.passStreak,
            )
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        game = VariantGame.from_imported(st, body.provenance)
        store.save(game)
        return game_view(game, game.env.events)

    @router.post("/games/open")
    async def open_record(body: dict[str, Any]) -> dict[str, Any]:
        """Open an exported record as a new game with the same moves (future bonuses drawn afresh)."""
        try:
            game = game_from_export(body)
        except RecordError as e:
            raise HTTPException(422, str(e)) from e
        store.save(game)
        return game_view(game, [])

    @router.get("/evaluators")
    async def evaluators() -> dict[str, Any]:
        """Analysis sources for bonus positions (docs/BONUS_GO.md §8)."""
        from gobot.variant.models.registry import list_models

        katago = settings.find_katago() is not None
        return {
            "sources": [
                {"id": "katago", "label": "Standard KataGo", "available": katago, "approximate": True},
                {
                    "id": "katago-net",
                    "label": "Bonus search · KataGo network",
                    "available": katago,
                    "approximate": True,
                },
                {
                    "id": "heuristic",
                    "label": "Bonus search · heuristic",
                    "available": True,
                    "approximate": True,
                },
            ],
            "models": [m.public() for m in list_models(settings.variant_dir)],
        }

    @router.get("/games/{game_id}/export")
    async def export(game_id: str) -> dict[str, Any]:
        return game_to_json(get_game(game_id), private=False)

    return router, store


def attach(app: Any, settings: Settings) -> None:
    router, store = build_router(settings)
    app.include_router(router)
    app.state.variant_store = store
