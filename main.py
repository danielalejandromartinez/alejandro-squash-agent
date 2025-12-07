import os
import json
import requests # <--- NUEVO: Para poder enviar mensajes a WhatsApp
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Depends
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from models import Base, Player, Match, WhatsAppUser
from elo import calculate_elo
from pydantic import BaseModel
from openai import OpenAI

# --- CONFIGURACIÓN INICIAL ---

# 1. Cargar llaves secretas
load_dotenv()

# 2. Configurar OpenAI (El Cerebro)
api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key)

# --- NUEVO: CONFIGURACIÓN DE WHATSAPP ---
# Pega aquí el Token Temporal de Meta (el largo que empieza con EAA...)
WHATSAPP_TOKEN = "EAAKTTJXNn20BQIbXpWnSCG3Avzj9Q2GsfxBFcSUmYrf2zQDI45Lz6KjcmeCiFxCD8twZCJzdF205QWxY22HsaAm8DiClTOa8CheUDD9wFy0a4ZACcwlEhlzXTvJikOByRfmjtFb7vGVPw7ZBBAZCPmpLMW8UMTOXUaHl46TO52iLr5bHKqqbbShPYrExlPBXzhUurkMmbO85SMZAcsJGuRJTcZCAQPXQNSmoa2G1TcsO8bgdfnr6fnAECT1VoSZCTTQCkhRZBkwZC62BIOnYXyi24cLy0A54ZD" 
# El ID del teléfono de prueba (lo tienes en la página de Meta)
PHONE_NUMBER_ID = "110811741996306" 

# 3. Configurar Base de Datos
DATABASE_URL = "sqlite:///./club_squash.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)

# 4. Crear la App
app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- UTILIDADES ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)

manager = ConnectionManager()

# --- NUEVO: FUNCIÓN PARA ENVIAR MENSAJES A WHATSAPP ---
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
        print(f"Error enviando: {e}")

# --- MODELOS DE DATOS (Pydantic) ---

class NuevoJugador(BaseModel):
    nombre: str
    celular_padrino: str

class NuevoPartido(BaseModel):
    jugador_1_nombre: str
    jugador_2_nombre: str
    ganador_nombre: str
    score: str

class MensajeChat(BaseModel):
    texto: str

# --- RUTAS (ENDPOINTS) ---

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

# Rutas API manuales (las dejamos por si acaso)
@app.post("/api/jugadores")
async def crear_jugador(datos: NuevoJugador, db: Session = Depends(get_db)):
    padrino = db.query(WhatsAppUser).filter_by(phone_number=datos.celular_padrino).first()
    if not padrino:
        padrino = WhatsAppUser(phone_number=datos.celular_padrino)
        db.add(padrino)
        db.commit()
        db.refresh(padrino)
    nuevo_jugador = Player(name=datos.nombre, owner_id=padrino.id)
    db.add(nuevo_jugador)
    db.commit()
    await manager.broadcast("update")
    return {"mensaje": f"Jugador {datos.nombre} creado"}

@app.post("/api/partidos")
async def registrar_partido(datos: NuevoPartido, db: Session = Depends(get_db)):
    # ... (Tu lógica manual sigue aquí igual) ...
    return {"mensaje": "Partido manual registrado"}

@app.post("/api/cerebro")
async def procesar_texto(mensaje: MensajeChat):
    # ... (Tu lógica de prueba sigue aquí igual) ...
    return {"mensaje": "Prueba cerebro"}

# WebSockets
@app.websocket("/ws/ranking")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.get("/ping")
async def ping():
    return {"mensaje": "¡Alejandro está vivo!"}

# --- CONEXIÓN CON WHATSAPP (EL OÍDO Y LA BOCA) ---

VERIFY_TOKEN = "alejandro_squash"

@app.get("/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return int(params.get("hub.challenge"))
    return {"error": "Token invalido"}

# --- AQUÍ ESTÁ LA MAGIA REAL ---
@app.post("/webhook")
async def receive_whatsapp(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    
    try:
        # 1. Extraer el mensaje que llegó
        entry = data['entry'][0]['changes'][0]['value']
        if 'messages' in entry:
            message = entry['messages'][0]
            telefono = message['from']
            texto_usuario = message['text']['body']
            
            print(f"📩 Mensaje recibido de {telefono}: {texto_usuario}")

            # 2. PENSAR (Usamos GPT-4o para decidir qué hacer)
            prompt = f"""
            Eres Alejandro, árbitro de Squash. Analiza: "{texto_usuario}".
            
            1. Si es resultado: {{ "accion": "partido", "ganador": "Nombre", "perdedor": "Nombre", "score": "3-0" }}
            2. Si crea jugador: {{ "accion": "crear", "nombre": "Nombre" }}
            3. Si saluda/pregunta: {{ "accion": "chat", "respuesta": "Respuesta corta y divertida" }}
            
            Responde SOLO el JSON.
            """
            
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "system", "content": prompt}],
                response_format={ "type": "json_object" }
            )
            decision = json.loads(response.choices[0].message.content)
            
            # 3. ACTUAR (Dependiendo de lo que decidió la IA)
            respuesta_texto = ""
            
            if decision['accion'] == 'chat':
                respuesta_texto = decision['respuesta']
                
            elif decision['accion'] == 'crear':
                nombre = decision['nombre']
                # Verificar si ya existe
                existe = db.query(Player).filter(Player.name == nombre).first()
                if not existe:
                    # Creamos un usuario "dummy" si no existe el padrino
                    padrino = db.query(WhatsAppUser).filter_by(phone_number=telefono).first()
                    if not padrino:
                        padrino = WhatsAppUser(phone_number=telefono)
                        db.add(padrino)
                        db.commit()
                    
                    nuevo = Player(name=nombre, owner_id=padrino.id)
                    db.add(nuevo)
                    db.commit()
                    await manager.broadcast("update") # ¡Actualizar TV!
                    respuesta_texto = f"✅ ¡Listo! {nombre} ya está en el ranking."
                else:
                    respuesta_texto = f"⚠️ {nombre} ya existe."

            elif decision['accion'] == 'partido':
                g = db.query(Player).filter(Player.name == decision['ganador']).first()
                p = db.query(Player).filter(Player.name == decision['perdedor']).first()
                
                if g and p:
                    # Calcular ELO
                    nuevo_elo_g, nuevo_elo_p, puntos = calculate_elo(g.elo, p.elo)
                    g.elo = nuevo_elo_g
                    p.elo = nuevo_elo_p
                    g.wins += 1
                    p.losses += 1
                    
                    # Guardar partido
                    match = Match(player_1_id=g.id, player_2_id=p.id, winner_id=g.id, score=decision['score'])
                    db.add(match)
                    db.commit()
                    
                    await manager.broadcast("update") # ¡Actualizar TV!
                    respuesta_texto = f"🏆 ¡Anotado! {g.name} (+{puntos}) vs {p.name} ({decision['score']})."
                else:
                    respuesta_texto = "❌ No encontré a uno de los jugadores. Revisa los nombres."

            # 4. RESPONDER (Enviar mensaje de vuelta a WhatsApp)
            enviar_whatsapp(telefono, respuesta_texto)

    except Exception as e:
        print(f"Error procesando: {e}")

    return {"status": "ok"}