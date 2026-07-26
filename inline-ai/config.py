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

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "")
BOT_NAME = os.getenv("BOT_NAME", "Your Bot Name")
BOT_USERNAME = os.getenv("BOT_USERNAME", "your_bot_username")
DEVELOPER = os.getenv("DEVELOPER", "@your_handle")
EXA_API_KEY = os.getenv("EXA_API_KEY", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
PORT = int(os.getenv("PORT", 8080))
