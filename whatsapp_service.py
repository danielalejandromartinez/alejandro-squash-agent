import os
import requests
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

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
        print(f"📤 Intentando enviar a {telefono_destino}...")
        response = requests.post(url, headers=headers, json=data)
        print(f"👉 Facebook Status: {response.status_code}")
    except Exception as e:
        print(f"❌ Error crítico enviando WhatsApp: {e}")