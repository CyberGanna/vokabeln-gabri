"""Bot Telegram "Vocabolario Tedesco".

Mandi la foto di una pagina in tedesco, il bot estrae le parole con un modello di visione,
scarta i doppioni e le salva nel database SQLite. Funziona in polling: basta lanciarlo
sul computer con `python bot.py`, non serve un server.
"""

import asyncio
import csv
import io
import json
import logging
import os
import shutil
import subprocess
import tempfile

import httpx

from dotenv import load_dotenv

load_dotenv()  # prima di importare estrazione, che legge ANTHROPIC_API_KEY

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update  # noqa: E402
from telegram.constants import ChatAction  # noqa: E402
from telegram.ext import (  # noqa: E402
    Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters,
)

from db import Vocabolario  # noqa: E402
from estrazione import (  # noqa: E402
    ISTRUZIONE_VOCABOLARIO, SCHEMA_VOCABOLARIO, EstrazioneFallita, estrai_da_immagine,
)

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

TOKEN = os.environ["TELEGRAM_TOKEN"]
FILTRO = os.environ.get("FILTRO", "comuni").strip().upper()
AUTORIZZATI = {int(x) for x in os.environ.get("UTENTI_AUTORIZZATI", "").replace(" ", "").split(",") if x}
LIVELLI = ["A1", "A2", "B1", "B2", "C1", "C2"]
MAX_PULSANTI = 40
APP_URL = os.environ.get("APP_URL", "").strip()      # Apps Script del Foglio Google dell'app di Gabri
FAMIGLIA = os.environ.get("FAMIGLIA", "").strip()    # codice famiglia usato nell'app
TIPI_APP = {"sostantivo": "s", "aggettivo": "a", "verbo": "v", "avverbio": "av"}

vocabolario = Vocabolario(os.environ.get("DB_PATH", "vocabolario.db"))


def autorizzato(update: Update) -> bool:
    return not AUTORIZZATI or (update.effective_user and update.effective_user.id in AUTORIZZATI)


def applica_filtro(parole: list[dict]) -> tuple[list[dict], int]:
    """Restituisce (parole tenute, quante scartate) secondo FILTRO."""
    if FILTRO == "NESSUNO":
        tenute = parole
    elif FILTRO in LIVELLI:
        soglia = LIVELLI.index(FILTRO)
        tenute = [p for p in parole if p["livello"] in LIVELLI and LIVELLI.index(p["livello"]) >= soglia]
    else:  # "COMUNI" (predefinito)
        tenute = [p for p in parole if not p["molto_comune"] and p["categoria"] != "articolo"]
    return tenute, len(parole) - len(tenute)


def etichetta(p) -> str:
    art = f"{p['articolo']} " if p["articolo"] else ""
    return f"{art}{p['forma_base']}"


async def invia_all_app(nuove) -> str:
    """Manda le parole nuove al Foglio Google, da cui le legge l'app di Gabri."""
    if not (APP_URL and FAMIGLIA and nuove):
        return ""
    corpo = {
        "app": "vokabeln-gabri", "tipo": "parole", "famiglia": FAMIGLIA,
        "parole": [{"de": etichetta(p), "it": p["traduzione"], "tipo": TIPI_APP.get(p["categoria"], "x")}
                   for p in nuove],
    }
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as http:
            r = await http.post(APP_URL, content=json.dumps(corpo, ensure_ascii=False).encode("utf-8"),
                                headers={"Content-Type": "text/plain;charset=utf-8"})
        r.raise_for_status()
        log.info("Risposta del foglio: %s", r.text[:200])
        if not r.text.strip().lower().startswith("ok"):
            return f"⚠️ Il Foglio Google ha risposto: {r.text.strip()[:100]!r} (le parole restano salvate qui)."
        return "Inviate all'app di Gabri ✅"
    except Exception:
        log.exception("Invio al Foglio Google fallito")
        return "⚠️ Non sono riuscito a mandarle all'app (restano salvate qui)."


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not autorizzato(update):
        await update.message.reply_text(f"Questo bot è privato. Il tuo id Telegram è {uid}.")
        return
    await update.message.reply_text(
        "Ciao! Mandami la foto di una pagina in tedesco e aggiungo le parole al vocabolario.\n\n"
        "Comandi:\n/parole – ultime 20 parole\n/esporta – tutto il vocabolario in CSV\n\n"
        f"Parole salvate: {vocabolario.conta()}. Filtro attivo: {FILTRO.lower()}.\n"
        f"Il tuo id Telegram è {uid}."
    )


FORMATI_OK = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_BYTE = 4_500_000  # il modello accetta immagini fino a circa 5 MB


def prepara_immagine(dati: bytes, media_type: str, nome: str) -> tuple[bytes, str]:
    """Converte HEIC (iPhone) e riduce le foto troppo grandi. Sul Mac usa `sips`, già installato."""
    estensione = nome.lower().rsplit(".", 1)[-1] if "." in nome else ""
    if media_type not in FORMATI_OK:
        media_type = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                      "webp": "image/webp"}.get(estensione, media_type)
    if media_type in FORMATI_OK and len(dati) <= MAX_BYTE:
        return dati, media_type
    if not shutil.which("sips"):
        raise ValueError("Questo formato non lo so leggere: mandami la foto normale (JPEG o PNG).")
    with tempfile.TemporaryDirectory() as cartella:
        sorgente = os.path.join(cartella, "in." + (estensione or "img"))
        destinazione = os.path.join(cartella, "out.jpg")
        with open(sorgente, "wb") as f:
            f.write(dati)
        esito = subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "85",
                                "-Z", "2400", sorgente, "--out", destinazione], capture_output=True)
        if esito.returncode != 0 or not os.path.exists(destinazione):
            raise ValueError("Non riesco ad aprire questo file: mandami la foto normale (JPEG o PNG).")
        with open(destinazione, "rb") as f:
            return f.read(), "image/jpeg"


async def altro_messaggio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Qualsiasi altro messaggio: risponde sempre, così non resta mai in silenzio."""
    if not autorizzato(update) or not update.message:
        return
    log.info("Messaggio non gestito da %s", update.effective_user.id)
    await update.message.reply_text("Mandami la foto di una pagina in tedesco. Comandi: /parole, /esporta.")


async def ricevi_foto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not autorizzato(update):
        return
    msg = update.message
    log.info("Ricevuta immagine da %s", update.effective_user.id)
    if msg.photo:
        # Telegram manda la stessa foto in più risoluzioni: l'ultima è la più grande.
        file_id, media_type, nome = msg.photo[-1].file_id, "image/jpeg", "foto.jpg"
    else:  # immagine mandata come file (non compressa, migliore per l'OCR)
        doc = msg.document
        file_id, media_type, nome = doc.file_id, (doc.mime_type or ""), (doc.file_name or "")

    attesa = await msg.reply_text("Sto leggendo la pagina…")
    await context.bot.send_chat_action(msg.chat_id, ChatAction.TYPING)
    tg_file = await context.bot.get_file(file_id)
    immagine = bytes(await tg_file.download_as_bytearray())
    try:
        immagine, media_type = await asyncio.to_thread(prepara_immagine, immagine, media_type, nome)
    except ValueError as e:
        await attesa.edit_text(str(e))
        return

    try:
        dati = await asyncio.to_thread(
            estrai_da_immagine, immagine, media_type, ISTRUZIONE_VOCABOLARIO, SCHEMA_VOCABOLARIO)
    except EstrazioneFallita as e:
        await attesa.edit_text(f"Non sono riuscito a leggere la pagina: {e}")
        return
    except Exception:
        log.exception("Errore durante l'estrazione")
        await attesa.edit_text("Errore durante la lettura della foto. Riprova tra poco.")
        return

    tenute, filtrate = applica_filtro(dati["parole"])
    nuove, doppie = vocabolario.aggiungi(tenute, update.effective_user.id)

    righe = [f"Ho aggiunto {len(nuove)} parole nuove."]
    if doppie:
        righe.append(f"{len(doppie)} c'erano già.")
    if filtrate:
        righe.append(f"{filtrate} scartate dal filtro ({FILTRO.lower()}).")
    if nuove:
        righe.append("")
        righe += [f"• {etichetta(p)} – {p['traduzione']}" for p in nuove]
        righe.append("\nTocca una parola per toglierla.")
    esito_app = await invia_all_app(nuove)
    if esito_app:
        righe.append(esito_app)
    testo = "\n".join(righe)[:4000]

    pulsanti = [[InlineKeyboardButton(f"❌ {etichetta(p)}", callback_data=f"del:{p['id']}")]
                for p in nuove[:MAX_PULSANTI]]
    await attesa.edit_text(testo, reply_markup=InlineKeyboardMarkup(pulsanti) if pulsanti else None)


async def scarta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not autorizzato(update):
        await query.answer()
        return
    parola_id = int(query.data.split(":", 1)[1])
    riga = vocabolario.elimina(parola_id)
    await query.answer(f"Tolta: {etichetta(riga)}" if riga else "Già tolta")
    # Toglie il pulsante della parola eliminata, lascia gli altri.
    tastiera = [r for r in query.message.reply_markup.inline_keyboard if r[0].callback_data != query.data]
    await query.edit_message_reply_markup(InlineKeyboardMarkup(tastiera) if tastiera else None)


async def parole(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not autorizzato(update):
        return
    ultime = vocabolario.ultime(20)
    if not ultime:
        await update.message.reply_text("Il vocabolario è ancora vuoto.")
        return
    await update.message.reply_text("\n".join(f"• {etichetta(p)} – {p['traduzione']}" for p in ultime))


async def esporta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not autorizzato(update):
        return
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["tedesco", "plurale", "italiano", "categoria", "livello", "esempio", "aggiunta il"])
    for p in vocabolario.tutte():
        w.writerow([etichetta(p), p["plurale"], p["traduzione"], p["categoria"], p["livello"],
                    p["esempio"], p["creata_il"]])
    dati = buf.getvalue().encode("utf-8-sig")  # con BOM, così Excel legge bene gli accenti
    await update.message.reply_document(dati, filename="vocabolario.csv")


def main() -> None:
    app = (Application.builder().token(TOKEN)
           .connect_timeout(30).read_timeout(30).write_timeout(60).pool_timeout(30)
           .get_updates_connect_timeout(30).get_updates_read_timeout(60)
           .build())
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("parole", parole))
    app.add_handler(CommandHandler("esporta", esporta))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, ricevi_foto))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, altro_messaggio))
    app.add_handler(CallbackQueryHandler(scarta, pattern=r"^del:\d+$"))
    log.info("Bot avviato (filtro: %s). Ctrl+C per fermarlo.", FILTRO.lower())
    app.run_polling(bootstrap_retries=-1)  # se la rete non risponde, riprova invece di chiudersi


if __name__ == "__main__":
    main()
