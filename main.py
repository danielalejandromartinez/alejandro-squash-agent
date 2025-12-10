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

manager = ConnectionManager()

def enviar_whatsapp(telefono_destino, mensaje):
    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": telefono_destino, "type": "text", "text": {"body": mensaje}}
    try: requests.post(url, headers=headers, json=data)
    except Exception as e: print(f"❌ Error enviando: {e}")

# --- CONTEXTO POR CLUB ---
def generar_contexto_club(db: Session, club_id: int):
    # Buscar torneo activo SOLO de este club
    torneo = db.query(Tournament).filter(Tournament.club_id == club_id, Tournament.status != "finished").first()
    info_torneo = "No hay torneos activos."
    
    if torneo:
        datos = torneo.smart_data if torneo.smart_data else {}
        inscritos_ids = datos.get("inscritos", [])
        nombres = []
        if inscritos_ids:
            jugadores = db.query(Player).filter(Player.id.in_(inscritos_ids)).all()
            nombres = [p.name for p in jugadores]
        info_torneo = f"TORNEO ACTIVO: '{torneo.name}'. Estado: {torneo.status}. Inscritos: {', '.join(nombres)}."
    
    # Ranking SOLO de este club
    top = db.query(Player).filter(Player.club_id == club_id).order_by(Player.elo.desc()).limit(3).all()
    ranking_txt = ", ".join([f"{p.name} ({p.elo})" for p in top])
    
    return f"CLUB ID {club_id}:\n- {info_torneo}\n- Top 3: {ranking_txt}"

# --- RUTAS WEB DINÁMICAS (SAAS) ---

@app.on_event("startup")
def startup_event():
    db = SessionLocal()
    # Asegurar Club Demo (ID 1)
    if not db.query(Club).filter_by(id=1).first():
        db.add(Club(name="Club Demo", admin_phone="573152405542"))
        db.commit()
    db.close()

# Ruta Home (Redirige al Club 1 por defecto o muestra lista)
@app.get("/")
async def home():
    return {"mensaje": "Bienvenido a Pasto.AI SaaS. Ve a /club/1 para ver el demo."}

# RUTA ESPECÍFICA POR CLUB
@app.get("/club/{club_id}")
async def ver_club(request: Request, club_id: int, db: Session = Depends(get_db)):
    club = db.query(Club).filter(Club.id == club_id).first()
    if not club: return "Club no encontrado"

    # Lógica Camaleón (Solo para este club)
    torneo = db.query(Tournament).filter(Tournament.club_id == club_id, Tournament.status != "finished").first()
    
    jugadores = []
    titulo = f"Ranking - {club.name}"
    modo = "ranking"

    if torneo and torneo.status == "inscription":
        modo = "torneo"
        titulo = f"Inscritos: {torneo.name}"
        datos = torneo.smart_data if torneo.smart_data else {}
        ids = datos.get("inscritos", [])
        if ids:
            jugadores = db.query(Player).filter(Player.id.in_(ids)).all()
    else:
        jugadores = db.query(Player).filter(Player.club_id == club_id).order_by(Player.elo.desc()).all()

    return templates.TemplateResponse("ranking.html", {
        "request": request, 
        "jugadores": jugadores, 
        "titulo": titulo,
        "modo": modo,
        "club_id": club_id # Pasamos el ID al HTML para el WebSocket
    })

# WEBSOCKET CON CANAL (ROOM) - CORREGIDO
@app.websocket("/ws/{club_id}")
async def websocket_endpoint(websocket: WebSocket, club_id: int):
    await manager.connect(websocket, club_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, club_id)

# --- WEBHOOK WHATSAPP ---
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

            # 1. IDENTIFICAR EL CLUB DEL USUARIO
            # Buscamos si es Admin de algún club
            club_usuario = db.query(Club).filter(Club.admin_phone == telefono).first()
            
            # Si no es admin, buscamos si es jugador de algún club
            if not club_usuario:
                padrino = db.query(WhatsAppUser).filter_by(phone_number=telefono).first()
                if padrino and padrino.players:
                    # Asumimos el club del primer jugador asociado (simplificación v1)
                    club_usuario = padrino.players[0].club
            
            # Si no tiene club, lo mandamos al Demo (ID 1) por defecto
            if not club_usuario:
                club_usuario = db.query(Club).filter(Club.id == 1).first()

            print(f"🏢 Contexto: {club_usuario.name} (ID: {club_usuario.id})")
            contexto = generar_contexto_club(db, club_usuario.id)

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
                existe = db.query(Player).filter(Player.name == nombre, Player.club_id == club_usuario.id).first()
                if not existe:
                    padrino = db.query(WhatsAppUser).filter_by(phone_number=telefono).first()
                    if not padrino:
                        padrino = WhatsAppUser(phone_number=telefono)
                        db.add(padrino); db.commit()
                    
                    nuevo = Player(name=nombre, owner_id=padrino.id, club_id=club_usuario.id)
                    db.add(nuevo); db.commit()
                    # AVISAR SOLO A LA TV DE ESTE CLUB
                    await manager.broadcast("update", club_usuario.id)

            elif accion == 'crear_torneo':
                # Cerrar anteriores de ESTE club
                anteriores = db.query(Tournament).filter(Tournament.club_id == club_usuario.id, Tournament.status == "inscription").all()
                for t in anteriores: t.status = "finished"
                
                nuevo_torneo = Tournament(
                    name=datos.get('nombre'), 
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
                    lista = list(torneo.smart_data.get("inscritos", []))
                    if jugador.id not in lista:
                        lista.append(jugador.id)
                        torneo.smart_data = dict(torneo.smart_data)
                        torneo.smart_data["inscritos"] = lista
                        from sqlalchemy.orm.attributes import flag_modified
                        flag_modified(torneo, "smart_data")
                        db.commit()
                        await manager.broadcast("update", club_usuario.id)
                        respuesta_texto = f"✅ {nombre_jugador} inscrito en {torneo.name}."
                    else:
                        respuesta_texto = f"⚠️ {nombre_jugador} ya estaba inscrito."
                else:
                    respuesta_texto = "❌ No se pudo inscribir. Verifica el nombre o si hay torneo."

            enviar_whatsapp(telefono, respuesta_texto)

    except Exception as e:
        print(f"❌ Error: {e}")
    return {"status": "ok"}