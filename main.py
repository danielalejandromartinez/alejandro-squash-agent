import os
import json
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Depends
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.orm.attributes import flag_modified
from models import Base, Player, Match, WhatsAppUser, Club, Tournament
from elo import calculate_elo
from pydantic import BaseModel
from openai import OpenAI
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

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

# --- GESTOR WEBSOCKETS ---
class ConnectionManager:
    def __init__(self): self.active_connections = {}
    async def connect(self, websocket: WebSocket, club_id: int):
        await websocket.accept()
        if club_id not in self.active_connections: self.active_connections[club_id] = []
        self.active_connections[club_id].append(websocket)
    def disconnect(self, websocket: WebSocket, club_id: int):
        if club_id in self.active_connections and websocket in self.active_connections[club_id]:
            self.active_connections[club_id].remove(websocket)
    async def broadcast(self, message: str, club_id: int):
        if club_id in self.active_connections:
            for connection in self.active_connections[club_id]: await connection.send_text(message)

manager = ConnectionManager()

def enviar_whatsapp(telefono_destino, mensaje):
    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": telefono_destino, "type": "text", "text": {"body": mensaje}}
    try: requests.post(url, headers=headers, json=data)
    except: pass

# --- CONTEXTO ---
def generar_contexto_club(db: Session, club_id: int):
    torneo = db.query(Tournament).filter(Tournament.club_id == club_id, Tournament.status != "finished").first()
    info_torneo = "No hay torneos activos."
    if torneo:
        datos = torneo.smart_data if torneo.smart_data else {}
        inscritos = len(datos.get("inscritos", []))
        cat = torneo.category if hasattr(torneo, 'category') else "General"
        info_torneo = f"TORNEO ACTIVO: '{torneo.name}' ({cat}). Estado: {torneo.status}. Inscritos: {inscritos}."
    return f"CLUB ID {club_id}:\n- {info_torneo}"

# --- RUTAS ---
@app.on_event("startup")
def startup_event():
    db = SessionLocal()
    if not db.query(Club).filter_by(id=1).first():
        db.add(Club(name="Club Demo", admin_phone="573152405542"))
        db.commit()
    db.close()

@app.get("/")
async def home(): return "Ve a /club/1"

@app.get("/club/{club_id}")
async def ver_club(request: Request, club_id: int, db: Session = Depends(get_db)):
    club = db.query(Club).filter(Club.id == club_id).first()
    if not club: return "Club no encontrado"

    torneo = db.query(Tournament).filter(Tournament.club_id == club_id, Tournament.status != "finished").first()
    
    jugadores = []
    partidos_torneo = []
    titulo = f"Ranking - {club.name}"
    modo = "ranking"

    if torneo:
        datos = torneo.smart_data if torneo.smart_data else {}
        cat_torneo = torneo.category if hasattr(torneo, 'category') else "General"
        
        if torneo.status == "inscription":
            modo = "torneo_inscripcion"
            titulo = f"Inscritos: {torneo.name} ({cat_torneo})"
            ids = datos.get("inscritos", [])
            if ids:
                jugadores = db.query(Player).filter(Player.id.in_(ids)).all()
        elif torneo.status == "playing":
            modo = "torneo_brackets"
            titulo = f"En Juego: {torneo.name}"
            partidos_torneo = db.query(Match).filter(Match.tournament_id == torneo.id).all()
    else:
        jugadores = db.query(Player).filter(Player.club_id == club_id).order_by(Player.elo.desc()).all()

    return templates.TemplateResponse("ranking.html", {
        "request": request, "jugadores": jugadores, "partidos": partidos_torneo,
        "titulo": titulo, "modo": modo, "club_id": club_id
    })

# --- HERRAMIENTA DE DIAGNÓSTICO (ESTA ES LA QUE FALTABA) ---
@app.get("/debug")
def debug_db(db: Session = Depends(get_db)):
    torneo = db.query(Tournament).first()
    jugadores = db.query(Player).all()
    info_torneo = "No hay torneo"
    if torneo:
        info_torneo = {"nombre": torneo.name, "status": torneo.status, "smart_data": torneo.smart_data}
    lista_jugadores = [{"id": p.id, "nombre": p.name, "club": p.club_id} for p in jugadores]
    return {"TORNEO_ACTUAL": info_torneo, "JUGADORES_EN_BD": lista_jugadores}

@app.get("/nuclear-reset")
def nuclear_reset():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    if not db.query(Club).filter_by(id=1).first():
        db.add(Club(name="Club Demo", admin_phone="573152405542"))
        db.commit()
    db.close()
    return {"status": "✅ Base de datos renovada."}

@app.websocket("/ws/{club_id}")
async def websocket_endpoint(websocket: WebSocket, club_id: int):
    await manager.connect(websocket, club_id)
    try: while True: await websocket.receive_text()
    except WebSocketDisconnect: manager.disconnect(websocket, club_id)

# --- WEBHOOK ---
VERIFY_TOKEN = "alejandro_squash"
@app.get("/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    if params.get("hub.verify_token") == VERIFY_TOKEN: return int(params.get("hub.challenge"))
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
            print(f"📩 De {telefono}: {texto_usuario}")

            club_usuario = db.query(Club).filter(Club.admin_phone == telefono).first()
            if not club_usuario:
                padrino = db.query(WhatsAppUser).filter_by(phone_number=telefono).first()
                if padrino and padrino.players: club_usuario = padrino.players[0].club
            if not club_usuario: club_usuario = db.query(Club).filter(Club.id == 1).first()

            contexto = generar_contexto_club(db, club_usuario.id)

            response = client.chat.completions.create(
                model="gpt-3.5-turbo-1106",
                messages=[
                    {"role": "system", "content": obtener_system_prompt(contexto)},
                    {"role": "user", "content": texto_usuario}
                ],
                response_format={ "type": "json_object" }
            )
            decision = json.loads(response.choices[0].message.content)
            print(f"🤖 IA: {decision['accion']}")

            respuesta_texto = decision.get('respuesta_whatsapp', "Procesado.")
            accion = decision.get('accion')
            datos = decision.get('datos', {})

            if accion == 'crear_jugador':
                nombre = datos.get('nombre')
                categoria = datos.get('categoria', 'General')
                existe = db.query(Player).filter(Player.name == nombre, Player.club_id == club_usuario.id).first()
                if not existe:
                    padrino = db.query(WhatsAppUser).filter_by(phone_number=telefono).first()
                    if not padrino:
                        padrino = WhatsAppUser(phone_number=telefono)
                        db.add(padrino); db.commit()
                    nuevo = Player(name=nombre, category=categoria, owner_id=padrino.id, club_id=club_usuario.id)
                    db.add(nuevo); db.commit()
                    await manager.broadcast("update", club_usuario.id)

            elif accion == 'crear_torneo':
                anteriores = db.query(Tournament).filter(Tournament.club_id == club_usuario.id, Tournament.status != "finished").all()
                for t in anteriores: t.status = "finished"
                
                categoria = datos.get('categoria', 'General')
                nuevo_torneo = Tournament(
                    name=datos.get('nombre'), 
                    category=categoria,
                    club_id=club_usuario.id, 
                    status="inscription", 
                    smart_data={"inscritos": []}
                )
                db.add(nuevo_torneo); db.commit()
                await manager.broadcast("update", club_usuario.id)

            elif accion == 'inscribir_en_torneo':
                nombre_jugador = datos.get('nombre_jugador') or datos.get('nombre')
                jugador = db.query(Player).filter(Player.name == nombre_jugador, Player.club_id == club_usuario.id).first()
                torneo = db.query(Tournament).filter(Tournament.club_id == club_usuario.id, Tournament.status == "inscription").first()
                
                if jugador and torneo:
                    db.refresh(torneo)
                    datos_actuales = dict(torneo.smart_data) if torneo.smart_data else {"inscritos": []}
                    lista_inscritos = list(datos_actuales.get("inscritos", []))
                    
                    if jugador.id not in lista_inscritos:
                        lista_inscritos.append(jugador.id)
                        datos_actuales["inscritos"] = lista_inscritos
                        torneo.smart_data = datos_actuales
                        flag_modified(torneo, "smart_data")
                        db.add(torneo); db.commit(); db.refresh(torneo)
                        await manager.broadcast("update", club_usuario.id)
                        respuesta_texto = f"✅ {nombre_jugador} inscrito en {torneo.name}."
                    else:
                        respuesta_texto = f"⚠️ {nombre_jugador} ya estaba inscrito."
                else:
                    respuesta_texto = "❌ No se pudo inscribir."

            elif accion == 'generar_cuadros':
                torneo = db.query(Tournament).filter(Tournament.club_id == club_usuario.id, Tournament.status == "inscription").first()
                if torneo:
                    ids = torneo.smart_data.get("inscritos", [])
                    if len(ids) >= 2:
                        jugadores = db.query(Player).filter(Player.id.in_(ids)).order_by(Player.elo.desc()).all()
                        n = len(jugadores)
                        for i in range(n // 2):
                            p1 = jugadores[i]
                            p2 = jugadores[n - 1 - i]
                            match = Match(player_1_id=p1.id, player_2_id=p2.id, tournament_id=torneo.id, score="VS", is_finished=False)
                            db.add(match)
                        torneo.status = "playing"
                        db.commit()
                        await manager.broadcast("update", club_usuario.id)
                        respuesta_texto = f"⚔️ ¡Cuadros generados! El torneo {torneo.name} ha comenzado."
                    else:
                        respuesta_texto = "⚠️ Necesitas al menos 2 jugadores."
                else:
                    respuesta_texto = "❌ No hay torneo en inscripción."

            enviar_whatsapp(telefono, respuesta_texto)

    except Exception as e:
        print(f"❌ Error: {e}")
    return {"status": "ok"}