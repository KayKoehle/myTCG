from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import RedirectResponse
from pydantic import ValidationError

from ..engine.transitions import register_custom_deck
from ..services import GameService
from ..services.lan import LanService
from .schemas import (
    ActionRequest,
    ActionResponse,
    AiMoveRequest,
    AiMoveResponse,
    CollectionResponse,
    DrawRequest,
    DrawResponse,
    ErrorResponse,
    MatchupStatsResponse,
    StateRequest,
    StateResponse,
)

# Connection manager stays here to handle multiple clients
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)


manager = ConnectionManager()
game_service = GameService()
lan_service = LanService(deck_registrar=register_custom_deck)
WEBAPP_DIR = Path(__file__).resolve().parents[1] / "webapp"
WEBAPP_MANIFEST = WEBAPP_DIR / "manifest.webmanifest"
WEBAPP_SW = WEBAPP_DIR / "sw.js"


async def _send_error(websocket: WebSocket, message: str):
    await manager.send_personal_message(ErrorResponse(error=message).model_dump_json(), websocket)

def register_ws_routes(app: FastAPI):
    """
    Attach all WebSocket endpoints to the FastAPI app.
    """

    # html=True serves index.html at /webapp/ so the page shares one set of
    # relative asset paths with the Capacitor build (which serves www/ at /).
    if WEBAPP_DIR.exists():
        app.mount("/webapp", StaticFiles(directory=str(WEBAPP_DIR), html=True), name="webapp")

    @app.get("/play")
    async def play_page():
        return RedirectResponse(url="/webapp/", status_code=307)

    @app.get("/manifest.webmanifest")
    async def manifest_file():
        if not WEBAPP_MANIFEST.exists():
            return {"ok": False, "error": f"Missing manifest file: {WEBAPP_MANIFEST}"}
        return FileResponse(WEBAPP_MANIFEST, media_type="application/manifest+json")

    @app.get("/sw.js")
    async def service_worker_file():
        if not WEBAPP_SW.exists():
            return {"ok": False, "error": f"Missing service worker file: {WEBAPP_SW}"}
        return FileResponse(WEBAPP_SW, media_type="application/javascript")

    @app.get("/")
    async def root_redirect():
        return RedirectResponse(url="/webapp/", status_code=307)

    @app.post("/api/state", response_model=StateResponse)
    async def get_state(request: StateRequest):
        game_service.get_or_create_match(
            match_id=request.match_id,
            seed=request.seed,
            deck_a=request.deck_a,
            deck_b=request.deck_b,
            deck_a_cards=request.deck_a_cards,
            deck_b_cards=request.deck_b_cards,
            decks=request.decks,
        )
        snapshot = game_service.state_snapshot(match_id=request.match_id, viewer_player_id=request.player_id)
        return StateResponse(snapshot=snapshot)

    @app.post("/api/action", response_model=ActionResponse)
    async def apply_action_http(request: ActionRequest):
        game_service.submit_action(
            match_id=request.match_id,
            player_id=request.player_id,
            action_kind=request.action_kind,
            card_id=request.card_id,
            location_id=request.location_id,
            option_id=request.option_id,
            seed=request.seed,
            deck_a=request.deck_a,
            deck_b=request.deck_b,
            deck_a_cards=request.deck_a_cards,
            deck_b_cards=request.deck_b_cards,
            decks=request.decks,
        )
        snapshot = game_service.state_snapshot(match_id=request.match_id, viewer_player_id=request.player_id)
        return ActionResponse(snapshot=snapshot)

    @app.post("/api/matchup-stats", response_model=MatchupStatsResponse)
    async def matchup_stats(request: dict | None = None):
        return MatchupStatsResponse(stats=game_service.matchup_stats.summary())

    @app.post("/api/collection", response_model=CollectionResponse)
    async def collection(request: dict | None = None):
        return CollectionResponse(decks=game_service.collection()["decks"])

    @app.post("/api/ai-move", response_model=AiMoveResponse)
    async def apply_ai_move(request: AiMoveRequest):
        action, snapshot = game_service.apply_ai_action(
            match_id=request.match_id,
            ai_player_id=request.ai_player_id,
            viewer_player_id=request.viewer_player_id,
            checkpoint_path=request.checkpoint_path,
            device=request.device,
            ai_elo=request.ai_elo,
            seed=request.seed,
            deck_a=request.deck_a,
            deck_b=request.deck_b,
            deck_a_cards=request.deck_a_cards,
            deck_b_cards=request.deck_b_cards,
            decks=request.decks,
        )
        return AiMoveResponse(action=action, snapshot=snapshot)

    # --- LAN multiplayer -------------------------------------------------------
    # Bodies/responses are plain dicts: the payloads are small and evolve with
    # the client, so Pydantic schemas would be pure overhead here. Guests reach
    # a *host's* instance for these; game play itself reuses /api/state and
    # /api/action on the host.

    @app.post("/api/lan/enable")
    async def lan_enable(request: dict):
        lan_service.start(self_name=request.get("name", "Player"), http_port=request.get("port", 8123))
        return {"ok": True, "status": lan_service.status()}

    @app.post("/api/lan/disable")
    async def lan_disable(request: dict | None = None):
        lan_service.stop()
        return {"ok": True}

    @app.post("/api/lan/peers")
    async def lan_peers(request: dict | None = None):
        return {"ok": True, "peers": lan_service.peers()}

    @app.post("/api/lan/host")
    async def lan_host(request: dict):
        lobby = lan_service.host_game(
            host_name=request.get("name", "Host"),
            deck_name=request["deck_name"],
            num_players=request.get("num_players", 2),
            deck_cards=request.get("deck_cards"),
            seed=request.get("seed"),
        )
        return {"ok": True, "lobby": lobby}

    @app.post("/api/lan/join")
    async def lan_join(request: dict):
        try:
            result = lan_service.join_game(
                lobby_id=request["lobby_id"],
                name=request.get("name", "Player"),
                deck_name=request["deck_name"],
                deck_cards=request.get("deck_cards"),
            )
        except (KeyError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, **result}

    @app.post("/api/lan/lobby")
    async def lan_lobby(request: dict):
        try:
            return {"ok": True, "lobby": lan_service.lobby(request["lobby_id"])}
        except KeyError as exc:
            return {"ok": False, "error": str(exc)}

    @app.post("/api/lan/start")
    async def lan_start(request: dict):
        try:
            params = lan_service.start_game(request["lobby_id"])
        except (KeyError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        # Build the authoritative match on the host so guests can immediately
        # drive it through /api/state and /api/action.
        game_service.create_match(
            match_id=params["match_id"],
            seed=params["seed"],
            decks=params["decks"],
        )
        return {"ok": True, **params}

    @app.post("/api/lan/trade/propose")
    async def lan_trade_propose(request: dict):
        return {"ok": True, "trade": lan_service.propose_trade(
            match_id=request["match_id"], a_pid=request["a_pid"], b_pid=request["b_pid"])}

    @app.post("/api/lan/trade/offer")
    async def lan_trade_offer(request: dict):
        try:
            return {"ok": True, "trade": lan_service.set_offer(
                request["trade_id"], request["player_id"], request.get("card_ids", []))}
        except (KeyError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    @app.post("/api/lan/trade/confirm")
    async def lan_trade_confirm(request: dict):
        try:
            return {"ok": True, "trade": lan_service.confirm_trade(
                request["trade_id"], request["player_id"])}
        except (KeyError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    @app.post("/api/lan/trade/cancel")
    async def lan_trade_cancel(request: dict):
        try:
            return {"ok": True, "trade": lan_service.cancel_trade(request["trade_id"])}
        except KeyError as exc:
            return {"ok": False, "error": str(exc)}

    @app.post("/api/lan/trade/state")
    async def lan_trade_state(request: dict):
        try:
            return {"ok": True, "trade": lan_service.trade(request["trade_id"])}
        except KeyError as exc:
            return {"ok": False, "error": str(exc)}

    @app.websocket("/ws/action")
    async def action_stream(websocket: WebSocket):
        await manager.connect(websocket)
        try:
            while True:
                data = await websocket.receive_text()
                try:
                    request = ActionRequest.model_validate_json(data)
                except ValidationError as exc:
                    await _send_error(websocket, f"Invalid action payload: {exc}")
                    continue

                try:
                    game_service.submit_action(
                        match_id=request.match_id,
                        player_id=request.player_id,
                        action_kind=request.action_kind,
                        card_id=request.card_id,
                        location_id=request.location_id,
                        option_id=request.option_id,
                        seed=request.seed,
                        deck_a=request.deck_a,
                        deck_b=request.deck_b,
                    )
                    snapshot = game_service.state_snapshot(
                        match_id=request.match_id,
                        viewer_player_id=request.player_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    await _send_error(websocket, str(exc))
                    continue

                response = ActionResponse(snapshot=snapshot)
                await manager.send_personal_message(response.model_dump_json(), websocket)

        except WebSocketDisconnect:
            manager.disconnect(websocket)

    @app.websocket("/ws/draw")
    async def draw_card_legacy(websocket: WebSocket):
        """Backward-compatible endpoint that maps to action_kind='draw_card'."""
        await manager.connect(websocket)
        try:
            while True:
                data = await websocket.receive_text()
                try:
                    request = DrawRequest.model_validate_json(data)
                except ValidationError as exc:
                    await _send_error(websocket, f"Invalid draw payload: {exc}")
                    continue

                try:
                    game_service.submit_action(
                        match_id=request.match_id,
                        player_id=request.player_id,
                        action_kind="draw_card",
                        seed=request.seed,
                        deck_a=request.deck_a,
                        deck_b=request.deck_b,
                    )
                    snapshot = game_service.state_snapshot(
                        match_id=request.match_id,
                        viewer_player_id=request.player_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    await _send_error(websocket, str(exc))
                    continue

                response = DrawResponse(snapshot=snapshot)
                await manager.send_personal_message(response.model_dump_json(), websocket)

        except WebSocketDisconnect:
            manager.disconnect(websocket)
