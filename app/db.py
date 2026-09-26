import sqlite3
from contextlib import contextmanager
from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_file TEXT NOT NULL,
    media_type TEXT NOT NULL,          -- image | video
    notes TEXT DEFAULT '',
    caption_ig TEXT DEFAULT '',
    caption_fb TEXT DEFAULT '',
    scheduled_at TEXT,                 -- ISO datetime (local tz)
    status TEXT DEFAULT 'draft',       -- draft | scheduled | published | failed
    result TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,            -- instagram | facebook | youtube
    comment_id TEXT NOT NULL UNIQUE,
    commenter TEXT DEFAULT '',
    comment_text TEXT DEFAULT '',
    reply_text TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',     -- pending | sent | failed | skipped
    error TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS dms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,            -- instagram | facebook | marketplace
    message_id TEXT UNIQUE,
    sender_id TEXT,
    sender_name TEXT DEFAULT '',
    message_text TEXT DEFAULT '',
    product TEXT DEFAULT '',
    reply_text TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',     -- pending | sent | failed | skipped | rejected
    error TEXT DEFAULT '',
    brand TEXT DEFAULT 'main',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'editor',        -- admin | editor
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand TEXT DEFAULT '',
    platform TEXT DEFAULT '',
    kind TEXT DEFAULT '',              -- comment_reply | comment_skip | dm_reply | dm_skip | publish | captions | error
    target TEXT DEFAULT '',            -- who/what it acted on
    detail TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);
"""

MIGRATIONS = [
    "ALTER TABLE posts ADD COLUMN caption_tt TEXT DEFAULT ''",
    "ALTER TABLE posts ADD COLUMN caption_yt TEXT DEFAULT ''",
    "ALTER TABLE posts ADD COLUMN platforms TEXT DEFAULT 'instagram,facebook'",
    "ALTER TABLE posts ADD COLUMN post_type TEXT DEFAULT 'image'",
    "ALTER TABLE posts ADD COLUMN source_url TEXT DEFAULT ''",
    "ALTER TABLE posts ADD COLUMN brand TEXT DEFAULT 'main'",
    "ALTER TABLE posts ADD COLUMN collab_with TEXT DEFAULT ''",
    "ALTER TABLE posts ADD COLUMN media_hash TEXT DEFAULT ''",
    "ALTER TABLE replies ADD COLUMN brand TEXT DEFAULT 'main'",
    "ALTER TABLE activity ADD COLUMN username TEXT DEFAULT ''",
]

def init():
    with connect() as con:
        con.executescript(SCHEMA)
        for m in MIGRATIONS:
            try:
                con.execute(m)
            except sqlite3.OperationalError:
                pass  # column already exists

@contextmanager
def connect():
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()

def kv_get(key, default=None):
    with connect() as con:
        row = con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

def kv_set(key, value):
    with connect() as con:
        con.execute(
            "INSERT INTO kv(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

def seen_comment(comment_id: str) -> bool:
    with connect() as con:
        return con.execute(
            "SELECT 1 FROM replies WHERE comment_id=?", (comment_id,)
        ).fetchone() is not None

def log_reply(platform, comment_id, commenter, comment_text, reply_text, status,
              error="", brand="main"):
    with connect() as con:
        con.execute(
            "INSERT OR IGNORE INTO replies(platform,comment_id,commenter,comment_text,reply_text,status,error,brand) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (platform, comment_id, commenter, comment_text, reply_text, status, error, brand),
        )

def log_activity(brand, platform, kind, target="", detail="", username="agent"):
    with connect() as con:
        con.execute(
            "INSERT INTO activity(brand,platform,kind,target,detail,username) VALUES(?,?,?,?,?,?)",
            (brand, platform, kind, str(target)[:200], str(detail)[:800], username),
        )

# ---------- users ----------

def _hash_pw(password: str, salt: str = None) -> str:
    import hashlib
    import secrets as _secrets
    salt = salt or _secrets.token_hex(8)
    return f"{salt}${hashlib.sha256((salt + password).encode()).hexdigest()}"

def create_user(username: str, password: str, role: str = "editor"):
    with connect() as con:
        con.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                    (username.strip().lower(), _hash_pw(password), role))

def verify_user(username: str, password: str):
    """Returns the user row if credentials are valid, else None."""
    import hashlib
    with connect() as con:
        row = con.execute("SELECT * FROM users WHERE username=?",
                          (username.strip().lower(),)).fetchone()
    if not row:
        return None
    try:
        salt, h = row["password_hash"].split("$", 1)
    except ValueError:
        return None
    if hashlib.sha256((salt + password).encode()).hexdigest() == h:
        return row
    return None

def seen_dm(message_id: str) -> bool:
    with connect() as con:
        return con.execute("SELECT 1 FROM dms WHERE message_id=?", (message_id,)).fetchone() is not None

def log_dm(platform, message_id, sender_id, sender_name, message_text, product,
           reply_text, status, error="", brand="main"):
    with connect() as con:
        con.execute(
            "INSERT OR IGNORE INTO dms(platform,message_id,sender_id,sender_name,"
            "message_text,product,reply_text,status,error,brand) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (platform, message_id, sender_id, sender_name, message_text, product,
             reply_text, status, error, brand),
        )

def update_dm(message_id, **fields):
    sets = ",".join(f"{k}=?" for k in fields)
    with connect() as con:
        con.execute(f"UPDATE dms SET {sets} WHERE message_id=?", (*fields.values(), message_id))

def dm_rate_limit_reason(brand: str, platform: str, sender_id: str):
    """Caps for auto-sent DMs (reuses the same env limits as comments)."""
    from . import config
    with connect() as con:
        hour = con.execute(
            "SELECT COUNT(*) c FROM dms WHERE brand=? AND status='sent' "
            "AND created_at >= datetime('now','-1 hour')", (brand,)).fetchone()["c"]
        if hour >= config.MAX_REPLIES_PER_HOUR:
            return f"hourly DM cap ({config.MAX_REPLIES_PER_HOUR}/h) reached — approve manually"
        per_user = con.execute(
            "SELECT COUNT(*) c FROM dms WHERE brand=? AND sender_id=? AND status='sent' "
            "AND created_at >= datetime('now','-1 day')", (brand, sender_id)).fetchone()["c"]
        if per_user >= 3:
            return "already replied 3x to this person today — approve manually"
    return None

def rate_limit_reason(brand: str, platform: str, commenter: str):
    """Return a human-readable reason if auto-sending should be held, else None."""
    from . import config
    with connect() as con:
        hour = con.execute(
            "SELECT COUNT(*) c FROM replies WHERE brand=? AND platform=? AND status='sent' "
            "AND created_at >= datetime('now','-1 hour')", (brand, platform)).fetchone()["c"]
        if hour >= config.MAX_REPLIES_PER_HOUR:
            return f"hourly cap ({config.MAX_REPLIES_PER_HOUR}/h) reached — approve manually"
        day = con.execute(
            "SELECT COUNT(*) c FROM replies WHERE brand=? AND platform=? AND status='sent' "
            "AND created_at >= datetime('now','-1 day')", (brand, platform)).fetchone()["c"]
        if day >= config.MAX_REPLIES_PER_DAY:
            return f"daily cap ({config.MAX_REPLIES_PER_DAY}/day) reached — approve manually"
        if commenter:
            per_user = con.execute(
                "SELECT COUNT(*) c FROM replies WHERE brand=? AND platform=? AND commenter=? "
                "AND status='sent' AND created_at >= datetime('now','-1 day')",
                (brand, platform, commenter)).fetchone()["c"]
            if per_user >= config.MAX_REPLIES_PER_COMMENTER_DAY:
                return (f"already replied {per_user}x to this commenter today — approve manually")
    return None

def tiktok_conflict(media_hash: str, source_url: str, brand: str):
    """Return the conflicting post row if this content already has a TikTok post
    under a DIFFERENT brand (TikTok penalises duplicate content across accounts)."""
    conds, params = [], []
    if media_hash:
        conds.append("media_hash=?")
        params.append(media_hash)
    if source_url:
        conds.append("source_url=?")
        params.append(source_url)
    if not conds:
        return None
    with connect() as con:
        return con.execute(
            f"SELECT * FROM posts WHERE ({' OR '.join(conds)}) "
            "AND brand != ? AND platforms LIKE '%tiktok%' LIMIT 1",
            (*params, brand),
        ).fetchone()

def update_reply(comment_id, **fields):
    sets = ",".join(f"{k}=?" for k in fields)
    with connect() as con:
        con.execute(f"UPDATE replies SET {sets} WHERE comment_id=?", (*fields.values(), comment_id))
