from fastapi import WebSocket

# --- GESTOR DE WEBSOCKETS MULTI-CANAL (SAAS) ---
class ConnectionManager:
    def __init__(self):
        # Diccionario: { club_id: [lista_de_conexiones] }
        self.active_connections: dict[int, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, club_id: int):
        await websocket.accept()
        if club_id not in self.active_connections:
            self.active_connections[club_id] = []
        self.active_connections[club_id].append(websocket)

    def disconnect(self, websocket: WebSocket, club_id: int):
        if club_id in self.active_connections:
            if websocket in self.active_connections[club_id]:
                self.active_connections[club_id].remove(websocket)

    async def broadcast(self, message: str, club_id: int):
        # Solo enviamos mensaje a las TVs de ESTE club específico
        if club_id in self.active_connections:
            for connection in self.active_connections[club_id]:
                await connection.send_text(message)

# Instancia global para usar en todo el proyecto
manager = ConnectionManager()