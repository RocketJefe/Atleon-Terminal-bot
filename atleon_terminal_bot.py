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

# Servidor HTTP keep-alive para Render
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

# 2. Conexión Persistente y Diagnóstico
API = None
ultimo_error_iq = "No se ha intentado conectar aún"

def conectar_iq():
    global API, ultimo_error_iq
    if not IQ_EMAIL or not IQ_PASSWORD:
        ultimo_error_iq = "Variables IQ_EMAIL o IQ_PASSWORD vacías en Render"
        logging.error(ultimo_error_iq)
        return False, ultimo_error_iq
    
    try:
        API = IQ_Option(IQ_EMAIL.strip(), IQ_PASSWORD.strip())
        check, reason = API.connect()
        if check:
            ultimo_error_iq = "Conectado"
            logging.info("✅ Conexión con IQ Option exitosa.")
            return True, "OK"
        else:
            ultimo_error_iq = str(reason)
            logging.error(f"❌ Error conectando a IQ: {reason}")
            return False, str(reason)
    except Exception as e:
        ultimo_error_iq = str(e)
        return False, str(e)

def asegurar_conexion():
    global API, ultimo_error_iq
    if API is None:
        ok, _ = conectar_iq()
        return ok
    try:
        if not API.check_connect():
            check, reason = API.connect()
            if not check:
                ultimo_error_iq = str(reason)
            return check
        return True
    except Exception as e:
        ultimo_error_iq = str(e)
        return False

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
        "EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC", "EURGBP-OTC",
        "NZDUSD-OTC", "AUDCAD-OTC", "GBPJPY-OTC", "XAUUSD"
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
        "Comandos directos:\n"
        "• Escribe un par directo (ej: `EURUSD-OTC`)\n"
        "• `/abiertos` - Consulta pares disponibles\n"
        "• `/status` - Diagnóstico de conexión en tiempo real"
    )
    await update.message.reply_text(texto, parse_mode=constants.ParseMode.MARKDOWN)

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if asegurar_conexion():
        await update.message.reply_text("✅ Servidor enlazado con éxito a IQ Option.")
    else:
        await update.message.reply_text(f"❌ Sin conexión con IQ Option.\n\n🔍 **Motivo exacto:** `{ultimo_error_iq}`", parse_mode=constants.ParseMode.MARKDOWN)

async def abiertos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not asegurar_conexion():
        await update.message.reply_text(f"❌ Sin conexión con IQ Option.\nMotivo: `{ultimo_error_iq}`", parse_mode=constants.ParseMode.MARKDOWN)
        return

    msg = await update.message.reply_text("⚡ Escaneando pares en vivo...")
    pares = obtener_binarios_y_otc()
    
    if pares:
        texto = f"🔹 **Pares Activos ({len(pares)}):**\n\n" + ", ".join([f"`{p}`" for p in pares])
    else:
        texto = "⚠️ No se detectaron velas activas en los pares escaneados."
        
    await msg.edit_text(texto, parse_mode=constants.ParseMode.MARKDOWN)

async def procesar_par(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not asegurar_conexion():
        await update.message.reply_text(f"❌ Conexión caída con IQ.\nMotivo: `{ultimo_error_iq}`", parse_mode=constants.ParseMode.MARKDOWN)
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
                f"✅ **Activo:** `{par}`\n"
                f"📊 **Hora Servidor:** `{hora_iq}`\n"
                f"📈 **Precio Cierre:** `{ultimo_precio}`\n"
                f"🎯 **Señal:** {senal}"
            )
            await msg.edit_text(respuesta, parse_mode=constants.ParseMode.MARKDOWN)
        else:
            await msg.edit_text(
                f"❌ El par `{par}` no devolvió datos en este momento.",
                parse_mode=constants.ParseMode.MARKDOWN
            )
    except Exception as e:
        await msg.edit_text(f"❌ Excepción al leer `{par}`: {e}")

async def eco_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text(
            "⚠️ Formato incorrecto.\nUsa: `/eco @NombreDeTuCanal Tu mensaje aquí`",
            parse_mode=constants.ParseMode.MARKDOWN
        )
        return

    destino_canal = context.args[0]
    mensaje_a_replicar = " ".join(context.args[1:])

    try:
        await context.bot.send_message(
            chat_id=destino_canal,
            text=mensaje_a_replicar,
            parse_mode=constants.ParseMode.MARKDOWN
        )
        await update.message.reply_text(f"✅ Publicado con éxito en `{destino_canal}`.", parse_mode=constants.ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ Error al enviar: {e}\n_(Verifica que el bot sea Administrador con permiso de publicar)_")

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
    app.add_handler(CommandHandler("eco", eco_cmd))

    app.run_polling()
