from fastapi import WebSocket, WebSocketDisconnect, FastAPI
from api.schemas import DrawRequest, DrawResponse
from domain.card import Card
import json

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

def register_ws_routes(app: FastAPI):
    """
    Attach all WebSocket endpoints to the FastAPI app.
    """

    @app.websocket("/ws/draw")
    async def draw_card(websocket: WebSocket):
        await manager.connect(websocket)
        try:
            while True:
                data = await websocket.receive_text()
                request = DrawRequest.parse_raw(data)

                # Example: always return Iron Man
                card = Card(
                    id=1,
                    name="Iron Man",
                    type="Hero",
                    sub_type="Avenger",
                    power=3,
                    cost=2,
                )

                response = DrawResponse(
                    card=card,
                    message=f"Player {request.player_id} drew Iron Man."
                )

                await manager.send_personal_message(response.json(), websocket)

        except WebSocketDisconnect:
            manager.disconnect(websocket)
