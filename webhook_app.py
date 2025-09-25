# webhook_app.py — FastAPI webhook pour aiogram v3 (import paresseux)
import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from aiogram.types import Update

# Globals initialisés au démarrage
bot = None
dp = None
BOT_TOKEN = None

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET") or os.getenv("BOT_TOKEN") or "MISSING_SECRET"
WEBHOOK_BASE = os.getenv("WEBHOOK_BASE") or os.getenv("RENDER_EXTERNAL_URL")
WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"
WEBHOOK_URL = f"{WEBHOOK_BASE}{WEBHOOK_PATH}" if WEBHOOK_BASE else None

logging.basicConfig(level=logging.INFO)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot, dp, BOT_TOKEN
    # 🔹 Import paresseux pour éviter un crash à l'import du module
    try:
        from main import bot as _bot, dp as _dp, BOT_TOKEN as _TOKEN
        bot, dp, BOT_TOKEN = _bot, _dp, _TOKEN
        logging.info("✅ Import main.py OK")
    except Exception as e:
        logging.exception("❌ Échec import main.py (vérifie tes variables d'env du *Web Service*): %s", e)
        raise

    # Installation du webhook
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
            # on laisse l'app démarrer pour voir les /ping
    else:
        logging.warning("⚠️ WEBHOOK_BASE absent (ou RENDER_EXTERNAL_URL). Ajoute WEBHOOK_BASE avec l'URL Render publique (https).")

    yield
    # Optionnel: laisser le webhook en place
    # await bot.delete_webhook(drop_pending_updates=False)

app = FastAPI(title="Telegram Bot (Webhook)", lifespan=lifespan)

# ---- Health ----
@app.get("/")
async def root_get():
    return {"ok": True, "service": "telegram-bot"}

@app.get("/ping")
async def ping_get():
    return {"status": "ok"}

# ---- Webhook ----
@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    # Vérification du header secret (si Telegram l'envoie)
    token_hdr = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if token_hdr and token_hdr != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Bad secret header")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Bad JSON")

    if not bot or not dp:
        logging.error("Bot/Dispatcher non initialisés")
        raise HTTPException(status_code=500, detail="Bot not ready")

    try:
        update = Update.model_validate(payload)  # aiogram v3 / pydantic v2
        await dp.feed_update(bot, update)
    except Exception as e:
        logging.exception("Erreur pendant le traitement du webhook: %s", e)
        return {"ok": False}

    return {"ok": True}
