import os
import json
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Depends
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

# --- IMPORTAMOS TUS NUEVOS ÓRGANOS (CORREGIDO) ---
from database import engine, get_db # <--- AQUÍ QUITAMOS 'Base'
from models import Base, Player, Match, WhatsAppUser, Club, Tournament # <--- AQUÍ AGREGAMOS 'Base'
from connection_manager import manager
from whatsapp_service import enviar_whatsapp
from ai_service import generar_contexto_club, consultar_alejandro
from elo import calculate_elo

# --- CONFIGURACIÓN ---
load_dotenv()
VERIFY_TOKEN = "alejandro_squash"

# Crear tablas en la BD
Base.metadata.create_all(bind=engine)

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# --- RUTAS DE INICIO ---
@app.on_event("startup")
def startup_event():
    db = next(get_db()) 
    if not db.query(Club).filter_by(id=1).first():
        print("🏗️ Creando Club Demo...")
        db.add(Club(name="Club Demo", admin_phone="573152405542"))
        db.commit()
    db.close()

@app.get("/")
async def home():
    return "Alejandro está vivo. Ve a /club/1 para ver el ranking."

# --- VISTA WEB ---
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

# --- WEBSOCKETS ---
@app.websocket("/ws/{club_id}")
async def websocket_endpoint(websocket: WebSocket, club_id: int):
    await manager.connect(websocket, club_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, club_id)

# --- HERRAMIENTAS TÉCNICAS ---
@app.get("/debug")
def debug_db(db: Session = Depends(get_db)):
    torneo = db.query(Tournament).first()
    jugadores = db.query(Player).all()
    info_torneo = "No hay torneo"
    if torneo:
        info_torneo = {"nombre": torneo.name, "status": torneo.status, "smart_data": torneo.smart_data}
    lista_jugadores = [{"id": p.id, "nombre": p.name, "club": p.club_id} for p in jugadores]
    return {"TORNEO": info_torneo, "JUGADORES": lista_jugadores}

@app.get("/nuclear-reset")
def nuclear_reset():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = next(get_db())
    if not db.query(Club).filter_by(id=1).first():
        db.add(Club(name="Club Demo", admin_phone="573152405542"))
        db.commit()
    db.close()
    return {"status": "✅ Base de datos renovada (Modular)."}

# --- WHATSAPP ---
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
            print(f"📩 De {telefono}: {texto_usuario}")

            # 1. IDENTIFICAR CLUB
            club_usuario = db.query(Club).filter(Club.admin_phone == telefono).first()
            if not club_usuario:
                padrino = db.query(WhatsAppUser).filter_by(phone_number=telefono).first()
                if padrino and padrino.players:
                    club_usuario = padrino.players[0].club
            if not club_usuario:
                club_usuario = db.query(Club).filter(Club.id == 1).first()

            # 2. CONSULTAR AL CEREBRO (AI SERVICE)
            contexto = generar_contexto_club(db, club_usuario.id)
            decision = consultar_alejandro(texto_usuario, contexto, telefono)
            
            print(f"🤖 IA: {decision.get('accion')}")

            respuesta_texto = decision.get('respuesta_whatsapp', "Procesado.")
            accion = decision.get('accion')
            datos = decision.get('datos', {})

            # 3. EJECUTAR ACCIÓN (MANOS)
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

            # 4. RESPONDER
            enviar_whatsapp(telefono, respuesta_texto)

    except Exception as e:
        print(f"❌ Error procesando: {e}")
    return {"status": "ok"}