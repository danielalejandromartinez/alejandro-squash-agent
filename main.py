import os
import json
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Depends
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from models import Base, Player, Match, WhatsAppUser
from elo import calculate_elo
from pydantic import BaseModel
from openai import OpenAI

# --- CONFIGURACIÓN INICIAL ---
load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key)

# --- LECTURA DE VARIABLES DE RENDER (ESTO ES LO IMPORTANTE) ---
# Ahora leemos la llave fresca de Render, no la vieja del archivo
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

# --- DATABASE ---
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./club_squash.db")
# Ajuste para que funcione en la nube de Render
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
    engine = create_engine(DATABASE_URL)
else:
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
    
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# --- UTILIDADES ---
def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

class ConnectionManager:
    def __init__(self): self.active_connections = []
    async def connect(self, websocket: WebSocket): await websocket.accept(); self.active_connections.append(websocket)
    def disconnect(self, websocket: WebSocket): self.active_connections.remove(websocket)
    async def broadcast(self, message: str):
        for connection in self.active_connections: await connection.send_text(message)

manager = ConnectionManager()

# --- FUNCIÓN DE ENVÍO CON REPORTE DE ERRORES ---
def enviar_whatsapp(telefono_destino, mensaje):
    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": telefono_destino,
        "type": "text",
        "text": {"body": mensaje}
    }
    try:
        print(f"📤 Intentando responder a {telefono_destino}...")
        response = requests.post(url, headers=headers, json=data)
        
        # AQUÍ ESTÁ EL DIAGNÓSTICO: Nos dirá si Meta acepta el mensaje
        print(f"👉 Status de Facebook: {response.status_code}")
        print(f"👉 Respuesta de Facebook: {response.text}")
        
    except Exception as e:
        print(f"❌ Error crítico enviando: {e}")

# --- RUTAS ---
@app.get("/")
async def ver_ranking(request: Request, db: Session = Depends(get_db)):
    jugadores = db.query(Player).order_by(Player.elo.desc()).all()
    return templates.TemplateResponse("ranking.html", {"request": request, "jugadores": jugadores})

@app.get("/programacion")
async def ver_partidos(request: Request, db: Session = Depends(get_db)):
    partidos = db.query(Match).order_by(Match.timestamp.desc()).all()
    return templates.TemplateResponse("partidos.html", {"request": request, "partidos": partidos})

@app.get("/design")
async def ver_diseno(request: Request):
    return templates.TemplateResponse("design_test.html", {"request": request})

@app.websocket("/ws/ranking")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True: await websocket.receive_text()
    except WebSocketDisconnect: manager.disconnect(websocket)

@app.get("/ping")
async def ping(): return {"mensaje": "Alejandro vivo"}

# --- WEBHOOK ---
VERIFY_TOKEN = "alejandro_squash"

@app.get("/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return int(params.get("hub.challenge"))
    return {"error": "Token invalido"}

@app.post("/webhook")
async def receive_whatsapp(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    try:
        entry = data['entry'][0]['changes'][0]['value']
        if 'messages' in entry:
            message = entry['messages'][0]
            telefono = message['from']
            texto_usuario = message['text']['body']
            print(f"📩 Mensaje recibido de {telefono}: {texto_usuario}")

            # 2. PENSAR (Usamos el modelo rápido para evitar Timeout)
            prompt = f"""
            Eres Alejandro, árbitro de Squash. Analiza: "{texto_usuario}".
            Si es resultado: {{ "accion": "partido", "ganador": "Nombre", "perdedor": "Nombre", "score": "3-0" }}
            Si crea jugador: {{ "accion": "crear", "nombre": "Nombre" }}
            Si saluda/pregunta: {{ "accion": "chat", "respuesta": "Respuesta corta y divertida" }}
            Responde SOLO el JSON.
            """
            
            # Usamos gpt-3.5-turbo para que sea veloz
            response = client.chat.completions.create(
                model="gpt-3.5-turbo-1106",
                messages=[{"role": "system", "content": prompt}],
                response_format={ "type": "json_object" }
            )
            decision = json.loads(response.choices[0].message.content)
            
            # 3. ACTUAR
            respuesta_texto = ""
            if decision['accion'] == 'chat':
                respuesta_texto = decision['respuesta']
            elif decision['accion'] == 'crear':
                nombre = decision['nombre']
                existe = db.query(Player).filter(Player.name == nombre).first()
                if not existe:
                    padrino = db.query(WhatsAppUser).filter_by(phone_number=telefono).first()
                    if not padrino:
                        padrino = WhatsAppUser(phone_number=telefono)
                        db.add(padrino); db.commit()
                    nuevo = Player(name=nombre, owner_id=padrino.id)
                    db.add(nuevo); db.commit()
                    await manager.broadcast("update")
                    respuesta_texto = f"✅ ¡Listo! {nombre} creado."
                else:
                    respuesta_texto = f"⚠️ {nombre} ya existe."
            elif decision['accion'] == 'partido':
                g = db.query(Player).filter(Player.name == decision['ganador']).first()
                p = db.query(Player).filter(Player.name == decision['perdedor']).first()
                if g and p:
                    nuevo_elo_g, nuevo_elo_p, puntos = calculate_elo(g.elo, p.elo)
                    g.elo = nuevo_elo_g; p.elo = nuevo_elo_p; g.wins += 1; p.losses += 1
                    match = Match(player_1_id=g.id, player_2_id=p.id, winner_id=g.id, score=decision['score'])
                    db.add(match); db.commit()
                    await manager.broadcast("update")
                    respuesta_texto = f"🏆 Anotado: {g.name} (+{puntos}) vs {p.name}."
                else:
                    respuesta_texto = "❌ Jugador no encontrado."

            # 4. RESPONDER
            enviar_whatsapp(telefono, respuesta_texto)

    except Exception as e:
        print(f"Error procesando: {e}")
    return {"status": "ok"}