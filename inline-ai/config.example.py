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

OPENCODE_ZEN_API_KEY = os.getenv("OPENCODE_ZEN_API_KEY", "")
OPENCODE_ZEN_BASE_URL = os.getenv(
    "OPENCODE_ZEN_BASE_URL", "https://opencode.ai/zen/v1"
)
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash-free")
BOT_NAME = os.getenv("BOT_NAME", "Your Bot Name")
BOT_USERNAME = os.getenv("BOT_USERNAME", "your_bot_username")
DEVELOPER = os.getenv("DEVELOPER", "@your_handle")
