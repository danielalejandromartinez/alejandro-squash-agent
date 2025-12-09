import os
import json
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Depends
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from models import Base, Player, Match, WhatsAppUser, Club, Tournament
from elo import calculate_elo
from pydantic import BaseModel
from openai import OpenAI

# --- IMPORTAMOS EL CEREBRO NUEVO ---
from prompts import obtener_system_prompt

# --- CONFIGURACIÓN ---
load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key)

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

# --- DATABASE ---
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./club_squash.db")
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
    try:
        yield db
    finally:
        db.close()

class ConnectionManager:
    def __init__(self):
        self.active_connections = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
    
    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
    
    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)

manager = ConnectionManager()

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
        requests.post(url, headers=headers, json=data)
    except Exception as e:
        print(f"❌ Error enviando: {e}")

# --- FUNCIÓN DE INTELIGENCIA DE CONTEXTO ---
def generar_contexto_club(db: Session):
    # 1. Buscar si hay torneos activos
    torneo = db.query(Tournament).filter(Tournament.status == "inscription").first()
    info_torneo = "No hay torneos activos en este momento."
    if torneo:
        # Manejo seguro del JSON por si está vacío
        datos = torneo.smart_data if torneo.smart_data else {}
        inscritos = len(datos.get("inscritos", []))
        info_torneo = f"TORNEO ACTIVO: '{torneo.name}'. Estado: Inscripciones Abiertas. Inscritos actuales: {inscritos}."
    
    # 2. Top 3 del Ranking
    top_players = db.query(Player).order_by(Player.elo.desc()).limit(3).all()
    ranking_txt = ", ".join([f"{p.name} ({p.elo})" for p in top_players])
    
    return f"""
    - {info_torneo}
    - Top 3 Ranking Global: {ranking_txt}
    """

# --- RUTAS ---
@app.on_event("startup")
def startup_event():
    # Crear el Club por defecto si no existe
    db = SessionLocal()
    club = db.query(Club).first()
    if not club:
        nuevo_club = Club(name="Club Demo", admin_phone="573152405542")
        db.add(nuevo_club)
        db.commit()
    db.close()

@app.get("/")
async def ver_ranking(request: Request, db: Session = Depends(get_db)):
    jugadores = db.query(Player).order_by(Player.elo.desc()).all()
    return templates.TemplateResponse("ranking.html", {"request": request, "jugadores": jugadores})

# --- AQUÍ ESTABA EL ERROR, YA ESTÁ CORREGIDO (IDENTACIÓN) ---
@app.websocket("/ws/ranking")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

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
            print(f"📩 Mensaje: {texto_usuario}")

            # 1. GENERAR CONTEXTO
            contexto = generar_contexto_club(db)

            # 2. PENSAR
            response = client.chat.completions.create(
                model="gpt-3.5-turbo-1106",
                messages=[
                    {"role": "system", "content": obtener_system_prompt(contexto)},
                    {"role": "user", "content": texto_usuario}
                ],
                response_format={ "type": "json_object" }
            )
            decision = json.loads(response.choices[0].message.content)
            
            # 3. ACTUAR
            respuesta_texto = decision.get('respuesta_whatsapp', "Procesado.")
            accion = decision.get('accion')
            datos = decision.get('datos', {})

            if accion == 'crear_jugador':
                nombre = datos.get('nombre')
                existe = db.query(Player).filter(Player.name == nombre).first()
                if not existe:
                    padrino = db.query(WhatsAppUser).filter_by(phone_number=telefono).first()
                    if not padrino:
                        padrino = WhatsAppUser(phone_number=telefono)
                        db.add(padrino); db.commit()
                    
                    nuevo = Player(name=nombre, owner_id=padrino.id, club_id=1)
                    db.add(nuevo); db.commit()
                    await manager.broadcast("update")

            elif accion == 'registrar_partido':
                g = db.query(Player).filter(Player.name == datos.get('ganador')).first()
                p = db.query(Player).filter(Player.name == datos.get('perdedor')).first()
                if g and p:
                    nuevo_elo_g, nuevo_elo_p, puntos = calculate_elo(g.elo, p.elo)
                    g.elo = nuevo_elo_g; p.elo = nuevo_elo_p; g.wins += 1; p.losses += 1
                    match = Match(player_1_id=g.id, player_2_id=p.id, winner_id=g.id, score=datos.get('score'))
                    db.add(match); db.commit()
                    await manager.broadcast("update")

            elif accion == 'crear_torneo':
                nuevo_torneo = Tournament(
                    name=datos.get('nombre'), 
                    club_id=1, 
                    status="inscription",
                    smart_data={"inscritos": []}
                )
                db.add(nuevo_torneo)
                db.commit()
                respuesta_texto = f"🏆 ¡Torneo '{datos.get('nombre')}' creado! Las inscripciones están abiertas."

            # 4. RESPONDER
            enviar_whatsapp(telefono, respuesta_texto)

    except Exception as e:
        print(f"Error: {e}")
    return {"status": "ok"}