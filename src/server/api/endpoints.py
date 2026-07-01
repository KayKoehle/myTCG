from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import RedirectResponse
from pydantic import ValidationError

from ..services import GameService
from .schemas import (
    ActionRequest,
    ActionResponse,
    AiMoveRequest,
    AiMoveResponse,
    DrawRequest,
    DrawResponse,
    ErrorResponse,
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
WEBAPP_DIR = Path(__file__).resolve().parents[1] / "webapp"
WEBAPP_INDEX = Path(__file__).resolve().parents[1] / "webapp" / "index.html"
WEBAPP_MANIFEST = WEBAPP_DIR / "manifest.webmanifest"
WEBAPP_SW = WEBAPP_DIR / "sw.js"


async def _send_error(websocket: WebSocket, message: str):
    await manager.send_personal_message(ErrorResponse(error=message).model_dump_json(), websocket)

def register_ws_routes(app: FastAPI):
    """
    Attach all WebSocket endpoints to the FastAPI app.
    """

    if WEBAPP_DIR.exists():
        app.mount("/webapp", StaticFiles(directory=str(WEBAPP_DIR)), name="webapp")

    @app.get("/play")
    async def play_page():
        if not WEBAPP_INDEX.exists():
            return {"ok": False, "error": f"Missing web app file: {WEBAPP_INDEX}"}
        return FileResponse(WEBAPP_INDEX)

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
        return RedirectResponse(url="/play", status_code=307)

    @app.post("/api/state", response_model=StateResponse)
    async def get_state(request: StateRequest):
        game_service.get_or_create_match(
            match_id=request.match_id,
            seed=request.seed,
            deck_a=request.deck_a,
            deck_b=request.deck_b,
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
        )
        snapshot = game_service.state_snapshot(match_id=request.match_id, viewer_player_id=request.player_id)
        return ActionResponse(snapshot=snapshot)

    @app.post("/api/ai-move", response_model=AiMoveResponse)
    async def apply_ai_move(request: AiMoveRequest):
        action, snapshot = game_service.apply_ai_action(
            match_id=request.match_id,
            ai_player_id=request.ai_player_id,
            viewer_player_id=request.viewer_player_id,
            checkpoint_path=request.checkpoint_path,
            device=request.device,
            seed=request.seed,
            deck_a=request.deck_a,
            deck_b=request.deck_b,
        )
        return AiMoveResponse(action=action, snapshot=snapshot)

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
