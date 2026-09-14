import os
import time
import logging
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

import pandas as pd
from iqoptionapi.stable_api import IQ_Option
from telegram import Update, constants
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

# 1. Variables de entorno
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
IQ_EMAIL = os.getenv("IQ_EMAIL")
IQ_PASSWORD = os.getenv("IQ_PASSWORD")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# Servidor HTTP dummy para Render
class RenderKeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Atleon Terminal is Live!")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), RenderKeepAliveHandler)
    server.serve_forever()

# 2. Conexión Persistente a IQ Option
API = None

def conectar_iq():
    global API
    if not IQ_EMAIL or not IQ_PASSWORD:
        logging.error("Faltan credenciales IQ_EMAIL o IQ_PASSWORD.")
        return False
    
    API = IQ_Option(IQ_EMAIL, IQ_PASSWORD)
    check, reason = API.connect()
    if check:
        logging.info("✅ Conexión con IQ Option exitosa.")
        return True
    else:
        logging.error(f"❌ Error conectando a IQ: {reason}")
        return False

def asegurar_conexion():
    global API
    if API is None:
        return conectar_iq()
    if not API.check_connect():
        logging.warning("⚠️ Conexión perdida. Reconectando a IQ...")
        check, _ = API.connect()
        return check
    return True

# 3. Análisis Técnico
def analizar_velas(candles):
    df = pd.DataFrame(candles)
    df["close"] = df["close"].astype(float)
    
    ultima_vela = df.iloc[-1]
    penultima_vela = df.iloc[-2]
    
    if ultima_vela["close"] > penultima_vela["close"]:
        return "🟢 COMPRA (CALL)", ultima_vela["close"]
    elif ultima_vela["close"] < penultima_vela["close"]:
        return "🔴 VENTA (PUT)", ultima_vela["close"]
    else:
        return "⚪ NEUTRAL / CONSOLIDACIÓN", ultima_vela["close"]

def obtener_binarios_y_otc():
    candidatos = [
        "XAUUSD", "MCDON", "USDJPY-OTC", "AMAZON", 
        "JPM", "UK100", "NZDUSD-OTC", "EURGBP-OTC", 
        "GS", "GBPJPY-OTC", "AUDJPY-OTC", "SNAP",
        "INTEL", "APPLE", "MSFT", "AIG", "USDCAD", "MORSTAN", "UKOUSD", "EURJPY"
    ]
    abiertos = []
    timestamp = time.time()
    
    for par in candidatos:
        try:
            vela = API.get_candles(par, 60, 1, timestamp)
            if vela and len(vela) > 0 and "close" in vela[0]:
                abiertos.append(par)
        except Exception:
            continue
    return abiertos

# 4. Handlers de Telegram
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = (
        "💻 **Terminal Interactiva Atleon Terminal**\n\n"
        "Comandos y uso directo:\n"
        "• Escribe el nombre de cualquier par (ej: `EURUSD`, `USDJPY-OTC`, `NZDUSD-OTC`)\n"
        "• `/abiertos` - Escanea pares Binarios y OTC con cotización activa.\n"
        "• `/status` - Consulta el estado de enlace con los servidores de IQ."
    )
    await update.message.reply_text(texto, parse_mode=constants.ParseMode.MARKDOWN)

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if asegurar_conexion():
        await update.message.reply_text("✅ Enlace activo con servidores de IQ Option.")
    else:
        await update.message.reply_text("❌ Sin conexión con IQ Option. Verifica credenciales.")

async def abiertos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not asegurar_conexion():
        await update.message.reply_text("❌ Sin conexión con IQ Option.")
        return

    msg = await update.message.reply_text("⚡ Escaneando activos en vivo...")
    pares = obtener_binarios_y_otc()
    
    if pares:
        texto = f"🔹 **Pares Activos Detectados ({len(pares)}):**\n\n" + ", ".join([f"`{p}`" for p in pares])
    else:
        texto = "⚠️ No se detectaron cotizaciones activas en este instante."
        
    await msg.edit_text(texto, parse_mode=constants.ParseMode.MARKDOWN)

async def procesar_par(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not asegurar_conexion():
        await update.message.reply_text("❌ Conexión caída con el servidor.")
        return

    par = update.message.text.strip().upper()
    if par.startswith("/"):
        return

    msg = await update.message.reply_text(f"🔍 Analizando `{par}`...", parse_mode=constants.ParseMode.MARKDOWN)
    
    try:
        candles = API.get_candles(par, 60, 10, time.time())
        if candles and len(candles) > 0 and "close" in candles[0]:
            senal, ultimo_precio = analizar_velas(candles)
            timestamp_servidor = API.get_server_timestamp()
            hora_iq = time.strftime("%H:%M:%S", time.localtime(timestamp_servidor))
            
            respuesta = (
                f"✅ **Activo:** `{par}` (ACTIVO)\n"
                f"📊 **Hora Servidor:** `{hora_iq}`\n"
                f"📈 **Precio Cierre:** `{ultimo_precio}`\n"
                f"🎯 **Señal:** {senal}"
            )
            await msg.edit_text(respuesta, parse_mode=constants.ParseMode.MARKDOWN)
        else:
            await msg.edit_text(
                f"❌ El activo `{par}` no respondió o está fuera de horario.\n"
                f"_(Si operas en fin de semana, recuerda agregar `-OTC` al final)_",
                parse_mode=constants.ParseMode.MARKDOWN
            )
    except Exception as e:
        await msg.edit_text(f"❌ Error al consultar `{par}`: {e}")

# 5. Arranque
if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        print("ERROR: Falta TELEGRAM_BOT_TOKEN")
        exit(1)

    Thread(target=run_web_server, daemon=True).start()
    conectar_iq()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("abiertos", abiertos_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, procesar_par))

    print("--------------------------------------------------")
    print("💻 ATLEON TERMINAL ONLINE EN RENDER 💻")
    print("--------------------------------------------------")
    app.run_polling()
