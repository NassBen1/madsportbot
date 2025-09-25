# webhook_api.py — FastAPI webhook pour aiogram v3 (Render) avec DEBUG
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import Response, JSONResponse
from aiogram.types import Update

logging.basicConfig(level=logging.INFO)

bot = None
dp = None
BOT_TOKEN = None

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET") or os.getenv("BOT_TOKEN") or "MISSING_SECRET"
WEBHOOK_BASE = os.getenv("WEBHOOK_BASE") or os.getenv("RENDER_EXTERNAL_URL")  # ex: https://ton-bot.onrender.com
WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"
WEBHOOK_URL = f"{WEBHOOK_BASE}{WEBHOOK_PATH}" if WEBHOOK_BASE else None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot, dp, BOT_TOKEN
    try:
        from main import bot as _bot, dp as _dp, BOT_TOKEN as _TOKEN
        bot, dp, BOT_TOKEN = _bot, _dp, _TOKEN
        logging.info("✅ Import main.py OK")
    except Exception as e:
        logging.exception("❌ Échec import main.py (env manquante ?): %s", e)
        raise

    # Installe/replace le webhook
    if WEBHOOK_URL:
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            await bot.set_webhook(
                url=WEBHOOK_URL,
                secret_token=WEBHOOK_SECRET,
                allowed_updates=list(dp.resolve_used_update_types()),
            )
            logging.info(f"✅ Webhook installé: {WEBHOOK_URL}")
        except Exception as e:
            logging.exception("❌ set_webhook a échoué: %s", e)
    else:
        logging.warning("⚠️ WEBHOOK_BASE/RENDER_EXTERNAL_URL absent -> pas de set_webhook.")

    yield

app = FastAPI(title="Telegram Bot (Webhook)", lifespan=lifespan)

# -------- Health / keep-alive --------
@app.get("/")
async def root_get():
    return {"ok": True, "service": "telegram-bot"}

@app.head("/")
async def root_head():
    return Response(status_code=200)

@app.get("/ping")
async def ping_get():
    return {"status": "ok"}

@app.head("/ping")
async def ping_head():
    return Response(status_code=200)

@app.options("/ping")
async def ping_options():
    return Response(status_code=200)

# -------- Debug --------
@app.get("/debug")
async def debug_info():
    return {
        "has_bot": bool(bot),
        "has_dp": bool(dp),
        "webhook_path": WEBHOOK_PATH,
        "webhook_base": WEBHOOK_BASE,
        "webhook_url": WEBHOOK_URL,
        "env_ok": bool(os.getenv("BOT_TOKEN")) and bool(os.getenv("SHEET_ID")),
    }

# -------- Webhook Telegram --------
@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    # log basique pour confirmer qu'on reçoit BIEN quelque chose
    logging.info("📩 POST webhook reçu")

    # Vérif header secret (si Telegram l'envoie)
    token_hdr = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if token_hdr and token_hdr != WEBHOOK_SECRET:
        logging.warning("❌ Mauvais secret header: %s", token_hdr)
        raise HTTPException(status_code=403, detail="Bad secret header")

    try:
        payload = await request.json()
    except Exception:
        logging.warning("❌ JSON invalide")
        raise HTTPException(status_code=400, detail="Bad JSON")

    if not bot or not dp:
        logging.error("❌ Bot/Dispatcher non initialisés")
        raise HTTPException(status_code=500, detail="Bot not ready")

    try:
        update = Update.model_validate(payload)  # aiogram v3 (pydantic v2)
        # log utile: on affiche le type d'update
        ut = None
        for k in ("message","callback_query","inline_query","my_chat_member","chat_member"):
            if payload.get(k) is not None:
                ut = k
                break
        logging.info(f"➡️ Type d'update: {ut or 'inconnu'}")
        await dp.feed_update(bot, update)
    except Exception as e:
        logging.exception("❌ Erreur pendant le traitement du webhook: %s", e)
        return JSONResponse({"ok": False}, status_code=200)

    return {"ok": True}
