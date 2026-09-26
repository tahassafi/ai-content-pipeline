import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

def env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()

BASE_URL = env("BASE_URL").rstrip("/")
DASHBOARD_PASSWORD = env("DASHBOARD_PASSWORD", "admin")
SESSION_SECRET = env("SESSION_SECRET", "dev-secret")
TIMEZONE = env("TIMEZONE", "UTC")

ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY")
CLAUDE_MODEL = env("CLAUDE_MODEL", "claude-sonnet-5")
REPLY_MODE = env("REPLY_MODE", "approve")  # auto | approve

# Public contact details used inside generated captions
CONTACT_PHONE = env("CONTACT_PHONE", "+0 000 000 0000")
CONTACT_EMAIL = env("CONTACT_EMAIL", "contact@example.com")
WEBSITE_URL = env("WEBSITE_URL", "https://example.com")
IG_HANDLE = env("IG_HANDLE", "@yourdealership")
# Auto-reply safety caps (per brand+platform). Over the cap → reply is held
# as pending for manual approval instead of being sent.
MAX_REPLIES_PER_HOUR = int(env("MAX_REPLIES_PER_HOUR", "12") or 12)
MAX_REPLIES_PER_DAY = int(env("MAX_REPLIES_PER_DAY", "60") or 60)
MAX_REPLIES_PER_COMMENTER_DAY = int(env("MAX_REPLIES_PER_COMMENTER_DAY", "2") or 2)

# Human-like pacing before auto-sending (seconds, random in range)
REPLY_DELAY_MIN = int(env("REPLY_DELAY_MIN", "25") or 25)
REPLY_DELAY_MAX = int(env("REPLY_DELAY_MAX", "75") or 75)
DM_DELAY_MIN = int(env("DM_DELAY_MIN", "5") or 5)
DM_DELAY_MAX = int(env("DM_DELAY_MAX", "20") or 20)

# AED → USD conversion rate used in captions (matches the website's rate)
USD_RATE = float(env("USD_RATE", "3.65") or 3.65)

# DMs
DM_ENABLED = env("DM_ENABLED", "true").lower() == "true"
WHATSAPP_LINK = env("WHATSAPP_LINK", "https://wa.me/00000000000")
# Optional: site search URL for availability checks; {query} is replaced by the car name
INVENTORY_SEARCH_URL = env("INVENTORY_SEARCH_URL", "")
# Preferred: direct read-only MySQL access to the dealer site's cars table
MYSQL_HOST = env("MYSQL_HOST", "localhost")
MYSQL_DB = env("MYSQL_DB", "")
MYSQL_USER = env("MYSQL_USER", "")
MYSQL_PASSWORD = env("MYSQL_PASSWORD", "")
MYSQL_CARS_TABLE = env("MYSQL_CARS_TABLE", "cars")

# Shared Meta app (one developer app serves all brands' pages)
META_APP_ID = env("META_APP_ID")
META_APP_SECRET = env("META_APP_SECRET")
META_VERIFY_TOKEN = env("META_VERIFY_TOKEN", "verify-token")
GRAPH_VER = env("GRAPH_API_VERSION", "v21.0")
GRAPH = f"https://graph.facebook.com/{GRAPH_VER}"

YT_ENABLED = env("YT_ENABLED", "false").lower() == "true"
YT_CLIENT_SECRET_FILE = str(BASE_DIR / env("YT_CLIENT_SECRET_FILE", "data/yt_client_secret.json"))
YT_POLL_MINUTES = int(env("YT_POLL_MINUTES", "5") or 5)

# ---------- brands ----------
# BRANDS=brand_one,brand_two  → per-brand vars: BRAND_BRAND_ONE_NAME,
# BRAND_BRAND_ONE_VOICE, BRAND_BRAND_ONE_META_PAGE_ID, BRAND_BRAND_ONE_META_PAGE_TOKEN,
# BRAND_BRAND_ONE_IG_USER_ID, BRAND_BRAND_ONE_IG_USERNAME, BRAND_BRAND_ONE_YT_CHANNEL_ID
BRAND_KEYS = [b.strip().lower() for b in env("BRANDS", "main").split(",") if b.strip()]

def _brand(key: str, first: bool) -> dict:
    p = f"BRAND_{key.upper()}_"
    # first brand falls back to the old single-brand variable names
    def fb(suffix, legacy=""):
        val = env(p + suffix)
        if not val and first and legacy:
            val = env(legacy)
        return val
    return {
        "key": key,
        "name": env(p + "NAME", key.title()),
        "voice": fb("VOICE", "BRAND_VOICE"),
        "page_id": fb("META_PAGE_ID", "META_PAGE_ID"),
        "page_token": fb("META_PAGE_TOKEN", "META_PAGE_TOKEN"),
        "ig_user_id": fb("IG_USER_ID", "IG_USER_ID"),
        "ig_username": env(p + "IG_USERNAME"),
        "yt_channel_id": fb("YT_CHANNEL_ID", "YT_CHANNEL_ID"),
        "yt_token_file": str(BASE_DIR / env(p + "YT_TOKEN_FILE", f"data/yt_token_{key}.json")),
        "color": env(p + "COLOR", "#2d5bff"),
        "hashtag": env(p + "HASHTAG", ""),
        "fb_prefix_link": env(p + "FB_PREFIX_LINK", ""),
    }

BRANDS = {k: _brand(k, i == 0) for i, k in enumerate(BRAND_KEYS)}

def brand_by_page_id(page_id: str):
    for b in BRANDS.values():
        if b["page_id"] == str(page_id):
            return b
    return None

def brand_by_ig_id(ig_id: str):
    for b in BRANDS.values():
        if b["ig_user_id"] == str(ig_id):
            return b
    return None

DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "agent.db"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
