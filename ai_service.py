import os
import json
from openai import OpenAI
from dotenv import load_dotenv
from sqlalchemy.orm import Session
from models import Player, Tournament
from prompts import obtener_system_prompt

# Cargar configuración
load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key)

# 1. Función para leer la mente del club (Contexto)
def generar_contexto_club(db: Session, club_id: int):
    # Buscar torneo activo
    torneo = db.query(Tournament).filter(Tournament.club_id == club_id, Tournament.status != "finished").first()
    info_torneo = "No hay torneos activos."
    
    if torneo:
        datos = torneo.smart_data if torneo.smart_data else {}
        inscritos = len(datos.get("inscritos", []))
        cat = torneo.category if hasattr(torneo, 'category') else "General"
        info_torneo = f"TORNEO ACTIVO: '{torneo.name}' ({cat}). Estado: {torneo.status}. Inscritos: {inscritos}."
    
    # Top 3 Ranking
    top = db.query(Player).filter(Player.club_id == club_id).order_by(Player.elo.desc()).limit(3).all()
    ranking_txt = ", ".join([f"{p.name} ({p.elo})" for p in top])
    
    return f"CLUB ID {club_id}:\n- {info_torneo}\n- Top 3: {ranking_txt}"

# 2. Función para preguntar a la IA
def consultar_alejandro(texto_usuario, contexto, telefono_usuario):
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo-1106", # Modelo rápido
            messages=[
                {"role": "system", "content": obtener_system_prompt(contexto, telefono_usuario)},
                {"role": "user", "content": texto_usuario}
            ],
            response_format={ "type": "json_object" }
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"❌ Error en IA: {e}")
        # Respuesta de emergencia si la IA falla
        return {"accion": "chat", "respuesta_whatsapp": "Estoy procesando mucha info, intenta de nuevo."}