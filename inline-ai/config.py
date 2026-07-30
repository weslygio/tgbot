import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

ALLOWED_USER_IDS = [
    int(x.strip())
    for x in os.getenv("ALLOWED_USER_IDS", "").split(",")
    if x.strip()
]
ALLOWED_GROUP_IDS = [
    int(x.strip())
    for x in os.getenv("ALLOWED_GROUP_IDS", "").split(",")
    if x.strip()
]

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL_ID = os.getenv("OPENROUTER_MODEL_ID", "")
OPENROUTER_FALLBACK_MODEL_ID = os.getenv("OPENROUTER_FALLBACK_MODEL_ID", "")
INPUT_MODALITIES = {
    x.strip().lower()
    for x in os.getenv("OPENROUTER_MODEL_INPUT_MODALITY", "").split(",")
    if x.strip().lower() in {"text", "file", "audio", "image", "video"}
}
FALLBACK_INPUT_MODALITIES = {
    x.strip().lower()
    for x in os.getenv("OPENROUTER_FALLBACK_MODEL_INPUT_MODALITY", "").split(",")
    if x.strip().lower() in {"text", "file", "audio", "image", "video"}
}
BOT_NAME = os.getenv("BOT_NAME", "Your Bot Name")
BOT_USERNAME = os.getenv("BOT_USERNAME", "your_bot_username")
DEVELOPER = os.getenv("DEVELOPER", "@your_handle")
EXA_API_KEY = os.getenv("EXA_API_KEY", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
PORT = int(os.getenv("PORT", 8080))
REASONING_EFFORT = os.getenv("REASONING_EFFORT", "high")
