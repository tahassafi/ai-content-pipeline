"""YouTube: poll new comments per brand channel and auto-reply."""
import logging
import os
from . import config, db, ai

log = logging.getLogger("youtube")

def _service(token_file: str):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(
        token_file, ["https://www.googleapis.com/auth/youtube.force-ssl"]
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(token_file, "w") as f:
            f.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)

def poll_comments():
    if not config.YT_ENABLED:
        return
    for brand in config.BRANDS.values():
        if brand["yt_channel_id"] and os.path.exists(brand["yt_token_file"]):
            _poll_brand(brand)

def _poll_brand(brand: dict):
    try:
        yt = _service(brand["yt_token_file"])
        resp = yt.commentThreads().list(
            part="snippet",
            allThreadsRelatedToChannelId=brand["yt_channel_id"],
            order="time",
            maxResults=50,
            textFormat="plainText",
        ).execute()
    except Exception:
        log.exception("YouTube poll failed for %s", brand["key"])
        return

    own_channels = {b["yt_channel_id"] for b in config.BRANDS.values() if b["yt_channel_id"]}
    new_items = []
    for item in resp.get("items", []):
        top = item["snippet"]["topLevelComment"]
        sn = top["snippet"]
        author_channel = (sn.get("authorChannelId") or {}).get("value", "")
        if author_channel in own_channels or db.seen_comment(top["id"]):
            continue
        new_items.append((top["id"], sn))

    # fetch video titles/descriptions so the AI knows what each comment is about
    videos = {}
    vids = {sn.get("videoId") for _, sn in new_items if sn.get("videoId")}
    if vids:
        try:
            vresp = yt.videos().list(part="snippet", id=",".join(list(vids)[:50])).execute()
            for v in vresp.get("items", []):
                vsn = v["snippet"]
                videos[v["id"]] = f"{vsn.get('title','')} — {vsn.get('description','')[:600]}"
        except Exception:
            log.warning("video context fetch failed")

    for comment_id, sn in new_items:
        _reply_flow(brand, comment_id, sn.get("authorDisplayName", ""),
                    sn.get("textDisplay", ""), videos.get(sn.get("videoId"), ""))

def _reply_flow(brand, comment_id, commenter, text, post_context=""):
    try:
        reply = ai.generate_reply("youtube", commenter, text, brand["voice"], post_context)
    except Exception as e:
        db.log_reply("youtube", comment_id, commenter, text, "", "failed",
                     str(e), brand["key"])
        return
    if reply.strip() == ai.SKIP:
        db.log_reply("youtube", comment_id, commenter, text, "", "skipped",
                     "flagged by AI", brand["key"])
        db.log_activity(brand["key"], "youtube", "comment_skip", commenter,
                        f"flagged: {text[:120]}")
        return
    db.log_reply("youtube", comment_id, commenter, text, reply, "pending",
                 "", brand["key"])
    if config.REPLY_MODE == "auto":
        reason = db.rate_limit_reason(brand["key"], "youtube", commenter)
        if reason:
            db.update_reply(comment_id, error=reason)
            return
        import random
        import time as _t
        _t.sleep(random.uniform(config.REPLY_DELAY_MIN, config.REPLY_DELAY_MAX))
        send_reply(comment_id)

def send_reply(comment_id: str):
    with db.connect() as con:
        row = con.execute("SELECT * FROM replies WHERE comment_id=?", (comment_id,)).fetchone()
    if not row or row["status"] == "sent":
        return
    brand = config.BRANDS.get(row["brand"]) or next(iter(config.BRANDS.values()))
    try:
        yt = _service(brand["yt_token_file"])
        yt.comments().insert(
            part="snippet",
            body={"snippet": {"parentId": comment_id, "textOriginal": row["reply_text"]}},
        ).execute()
        db.update_reply(comment_id, status="sent")
        db.log_activity(row["brand"], "youtube", "comment_reply", row["commenter"],
                        f"→ {row['reply_text'][:200]}")
    except Exception as e:
        log.exception("YouTube reply failed")
        db.update_reply(comment_id, status="failed", error=str(e)[:500])
        db.log_activity(row["brand"], "youtube", "error", row["commenter"],
                        f"reply send failed: {str(e)[:200]}")
