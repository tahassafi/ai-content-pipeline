"""Background jobs: publish due posts, poll YouTube."""
import logging
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from . import config, db, meta, youtube

log = logging.getLogger("scheduler")
scheduler = BackgroundScheduler(timezone=config.TIMEZONE)

def publish_due_posts():
    now = datetime.now().strftime("%Y-%m-%dT%H:%M")
    with db.connect() as con:
        rows = con.execute(
            "SELECT * FROM posts WHERE status='scheduled' AND scheduled_at <= ?", (now,)
        ).fetchall()
    for row in rows:
        post = dict(row)
        brand = config.BRANDS.get(post.get("brand", "")) or next(iter(config.BRANDS.values()))
        log.info("Publishing post %s (%s)", post["id"], brand["key"])
        results = meta.publish_post(post, brand)
        ok = all(v.startswith("ok") for v in results.values())
        with db.connect() as con:
            con.execute(
                "UPDATE posts SET status=?, result=? WHERE id=?",
                ("published" if ok else "failed", str(results), post["id"]),
            )

def drain_pending():
    """Auto-send replies that were held only by rate limits, once capacity frees up.
    Runs only in auto mode; paces sends a few seconds apart; re-checks caps before
    every send so the hourly/daily/per-commenter limits are always respected."""
    import time as _time
    if config.REPLY_MODE != "auto":
        return
    with db.connect() as con:
        rows = con.execute(
            "SELECT * FROM replies WHERE status='pending' "
            "AND error LIKE '%approve manually%' ORDER BY id ASC LIMIT 40"
        ).fetchall()
    for row in rows:
        reason = db.rate_limit_reason(row["brand"], row["platform"], row["commenter"])
        if reason:
            continue  # still capped — try again next cycle
        db.update_reply(row["comment_id"], error="auto-drained")
        try:
            if row["platform"] == "youtube":
                youtube.send_reply(row["comment_id"])
            else:
                meta.send_reply(row["platform"], row["comment_id"])
        except Exception:
            log.exception("drain send failed for %s", row["comment_id"])
        _time.sleep(4)  # human pacing between sends

def start():
    # NOTE: auto-publishing removed — all posting is manual; the dashboard is
    # caption-only. Comment/DM automation stays.
    scheduler.add_job(drain_pending, "interval", minutes=15, id="drain")
    if config.YT_ENABLED:
        scheduler.add_job(youtube.poll_comments, "interval",
                          minutes=config.YT_POLL_MINUTES, id="yt_poll")
    scheduler.start()
