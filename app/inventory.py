"""Availability checks against the dealer website's own MySQL cars table (read-only)."""
import logging
import re
from . import config

log = logging.getLogger("inventory")

STOP_WORDS = {
    "available", "still", "this", "that", "the", "car", "cars", "price", "much",
    "how", "for", "sale", "you", "have", "hello", "please", "want", "buy",
    "interested", "and", "with", "does", "your", "one", "can", "get", "info",
}

def _fetch_cars():
    import pymysql
    con = pymysql.connect(
        host=config.MYSQL_HOST, user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD, database=config.MYSQL_DB,
        cursorclass=pymysql.cursors.DictCursor, connect_timeout=8,
    )
    try:
        with con.cursor() as cur:
            cur.execute(
                f"SELECT title, year, status FROM {config.MYSQL_CARS_TABLE} "
                "WHERE deleted_at IS NULL"
            )
            return cur.fetchall()
    finally:
        con.close()

def match_cars(rows, query: str):
    """Score inventory rows against the query; returns top matches."""
    words = [w for w in re.findall(r"[a-z0-9]+", (query or "").lower())
             if len(w) >= 3 and w not in STOP_WORDS]
    if not words:
        return []
    scored = []
    for r in rows:
        title = (r.get("title") or "").lower()
        score = sum(1 for w in words if w in title)
        if score > 0:
            scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    best = scored[0][0] if scored else 0
    # keep only strong matches: at least 2 word hits, or the single best if only one word given
    keep = [r for s, r in scored if s >= 2] or ([scored[0][1]] if scored and len(words) == 1 else [])
    if not keep:
        keep = [r for s, r in scored if s == best and best >= 1]
    return keep[:8]

def get_car_by_url(url: str):
    """Look up the exact car row from the site DB using the listing URL slug."""
    if not (config.MYSQL_DB and config.MYSQL_USER) or not url:
        return None
    from urllib.parse import urlparse
    parts = [p for p in urlparse(url).path.split("/") if p]
    if not parts:
        return None
    import pymysql
    try:
        con = pymysql.connect(
            host=config.MYSQL_HOST, user=config.MYSQL_USER,
            password=config.MYSQL_PASSWORD, database=config.MYSQL_DB,
            cursorclass=pymysql.cursors.DictCursor, connect_timeout=8,
        )
        with con.cursor() as cur:
            if len(parts) >= 2:
                cur.execute(
                    f"SELECT * FROM {config.MYSQL_CARS_TABLE} WHERE slug_prefix=%s "
                    "AND ref_number=%s AND deleted_at IS NULL LIMIT 1",
                    (parts[-2], parts[-1]))
                row = cur.fetchone()
                if row:
                    return row
            cur.execute(
                f"SELECT * FROM {config.MYSQL_CARS_TABLE} WHERE slug_prefix=%s "
                "AND deleted_at IS NULL LIMIT 1", (parts[-1],))
            return cur.fetchone()
    except Exception as e:
        log.warning("car-by-url lookup failed: %s", e)
        return None
    finally:
        try:
            con.close()
        except Exception:
            pass


def check_availability(query: str) -> str:
    """Returns a text summary for the AI, or '' if the check isn't configured/fails."""
    if not (config.MYSQL_DB and config.MYSQL_USER):
        return ""
    try:
        rows = _fetch_cars()
    except Exception as e:
        log.warning("inventory DB check failed: %s", e)
        return ""
    matches = match_cars(rows, query)
    if not matches:
        return (f"No car matching '{query[:80]}' was found in the current inventory "
                "(it may have been sold or the name is unclear — do not claim availability).")
    lines = [f"- {m['title']} ({m.get('year') or 'n/a'}) — status: {m.get('status')}"
             for m in matches]
    return (f"Live inventory check from the dealership database "
            f"({len(matches)} matching car(s) shown — mention notable variants like "
            f"Brabus/Mansory editions if present):\n" + "\n".join(lines))
