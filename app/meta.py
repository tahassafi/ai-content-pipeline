"""Meta Graph API: brand-aware IG/FB comment replies, DMs + publishing (with IG collab)."""
import hashlib
import hmac
import json as jsonlib
import logging
import httpx
from . import config, db, ai, web, inventory

log = logging.getLogger("meta")

def verify_signature(payload: bytes, signature_header: str) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        config.META_APP_SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header.split("=", 1)[1])

def _post(token: str, path: str, **params):
    params.setdefault("access_token", token)
    r = httpx.post(f"{config.GRAPH}/{path}", data=params, timeout=60)
    r.raise_for_status()
    return r.json()

def _get(token: str, path: str, **params):
    params.setdefault("access_token", token)
    r = httpx.get(f"{config.GRAPH}/{path}", params=params, timeout=60)
    r.raise_for_status()
    return r.json()

# ---------- comment replies ----------

def handle_webhook(body: dict):
    """Route incoming webhook events to the right brand."""
    obj = body.get("object", "")
    for entry in body.get("entry", []):
        entry_id = str(entry.get("id", ""))
        # DMs / Messenger / IG messaging events
        for ev in entry.get("messaging", []) or []:
            brand = (config.brand_by_page_id(entry_id)
                     or config.brand_by_ig_id(entry_id))
            if brand and config.DM_ENABLED:
                _handle_dm(brand, "instagram" if obj == "instagram" else "facebook", ev)
        for change in entry.get("changes", []):
            field = change.get("field")
            value = change.get("value", {})
            if obj == "instagram" and field == "comments":
                brand = config.brand_by_ig_id(entry_id)
                if brand:
                    _handle_ig_comment(brand, value)
                else:
                    log.warning("IG comment for unknown account id %s", entry_id)
            elif obj == "page" and field == "feed" and value.get("item") == "comment":
                brand = config.brand_by_page_id(entry_id)
                if brand:
                    _handle_fb_comment(brand, value)
                else:
                    log.warning("FB comment for unknown page id %s", entry_id)

def _handle_ig_comment(brand: dict, v: dict):
    comment_id = v.get("id")
    text = v.get("text", "")
    username = (v.get("from") or {}).get("username", "")
    from_id = (v.get("from") or {}).get("id", "")
    # ignore our own comments on ANY of our brands (avoid loops on collab posts)
    own_ids = {b["ig_user_id"] for b in config.BRANDS.values() if b["ig_user_id"]}
    if not comment_id or from_id in own_ids:
        return
    context = ""
    media_id = (v.get("media") or {}).get("id")
    if media_id:
        try:
            context = _get(brand["page_token"], media_id, fields="caption").get("caption", "")
        except Exception:
            pass
    _reply_flow(brand, "instagram", comment_id, username, text, context)

def _handle_fb_comment(brand: dict, v: dict):
    if v.get("verb") != "add":
        return
    comment_id = v.get("comment_id")
    text = v.get("message", "")
    frm = v.get("from") or {}
    own_ids = {b["page_id"] for b in config.BRANDS.values() if b["page_id"]}
    if not comment_id or frm.get("id") in own_ids:
        return
    context = ""
    post_id = v.get("post_id")
    if post_id:
        try:
            context = _get(brand["page_token"], post_id, fields="message").get("message", "")
        except Exception:
            pass
    _reply_flow(brand, "facebook", comment_id, frm.get("name", ""), text, context)

def _reply_flow(brand: dict, platform: str, comment_id: str, commenter: str,
                text: str, post_context: str = ""):
    if db.seen_comment(comment_id):
        return
    try:
        reply = ai.generate_reply(platform, commenter, text, brand["voice"], post_context)
    except Exception as e:
        log.exception("AI reply failed")
        db.log_reply(platform, comment_id, commenter, text, "", "failed", str(e),
                     brand["key"])
        return
    if reply.strip() == ai.SKIP:
        db.log_reply(platform, comment_id, commenter, text, "", "skipped",
                     "flagged by AI", brand["key"])
        db.log_activity(brand["key"], platform, "comment_skip", commenter,
                        f"flagged: {text[:120]}")
        return
    db.log_reply(platform, comment_id, commenter, text, reply, "pending",
                 "", brand["key"])
    if config.REPLY_MODE == "auto":
        reason = db.rate_limit_reason(brand["key"], platform, commenter)
        if reason:
            db.update_reply(comment_id, error=reason)
            return
        import random
        import time as _t
        _t.sleep(random.uniform(config.REPLY_DELAY_MIN, config.REPLY_DELAY_MAX))
        send_reply(platform, comment_id)

def send_reply(platform: str, comment_id: str):
    with db.connect() as con:
        row = con.execute("SELECT * FROM replies WHERE comment_id=?", (comment_id,)).fetchone()
    if not row or row["status"] == "sent":
        return
    brand = config.BRANDS.get(row["brand"]) or next(iter(config.BRANDS.values()))
    token = brand["page_token"]
    try:
        if platform == "instagram":
            _post(token, f"{comment_id}/replies", message=row["reply_text"])
        else:
            _post(token, f"{comment_id}/comments", message=row["reply_text"])
        db.update_reply(comment_id, status="sent")
        db.log_activity(row["brand"], platform, "comment_reply", row["commenter"],
                        f"→ {row['reply_text'][:200]}")
    except Exception as e:
        log.exception("send reply failed")
        db.update_reply(comment_id, status="failed", error=str(e)[:500])
        db.log_activity(row["brand"], platform, "error", row["commenter"],
                        f"reply send failed: {str(e)[:200]}")

# ---------- DMs ----------

def _handle_dm(brand: dict, platform: str, ev: dict):
    msg = ev.get("message") or {}
    if not msg or msg.get("is_echo"):
        return  # delivery/read receipts or our own outgoing messages
    mid = msg.get("mid", "")
    sender_id = str((ev.get("sender") or {}).get("id", ""))
    own_ids = ({b["page_id"] for b in config.BRANDS.values() if b["page_id"]}
               | {b["ig_user_id"] for b in config.BRANDS.values() if b["ig_user_id"]})
    if not mid or not sender_id or sender_id in own_ids:
        return
    if db.seen_dm(mid):
        return

    text = (msg.get("text") or "").strip()
    attachments = msg.get("attachments") or []
    referral = ev.get("referral") or msg.get("referral") or {}
    product = ""
    if referral:
        prod = referral.get("product") or {}
        product = prod.get("title") or prod.get("id") or referral.get("ref") or ""
        if referral.get("source") == "SHOPS_PRODUCT_DETAIL_PAGE" or product:
            platform = "marketplace" if platform == "facebook" else platform

    # cheap pre-filter: attachment-only messages (shared reels/posts/stickers) → skip, no AI call
    if attachments and len(text) < 3:
        db.log_dm(platform, mid, sender_id, "", text, product, "", "skipped",
                  "attachment/share only — skipped without AI", brand["key"])
        db.log_activity(brand["key"], platform, "dm_skip", sender_id,
                        "attachment/share only (reel/post/sticker)")
        return
    if not text:
        return

    sender_name = _sender_name(brand, sender_id)
    inv = inventory.check_availability(product or text)
    if not inv and product:
        inv = web.check_inventory(product)  # fallback: site search if configured
    try:
        reply = ai.generate_dm_reply(platform, sender_name, text, product,
                                     inv, brand["voice"])
    except Exception as e:
        log.exception("AI DM reply failed")
        db.log_dm(platform, mid, sender_id, sender_name, text, product, "",
                  "failed", str(e)[:300], brand["key"])
        db.log_activity(brand["key"], platform, "error", sender_id, f"DM AI failed: {e}")
        return
    if reply.strip() == ai.SKIP:
        db.log_dm(platform, mid, sender_id, sender_name, text, product, "",
                  "skipped", "flagged by AI (unserious/spam/consign/promo)", brand["key"])
        db.log_activity(brand["key"], platform, "dm_skip", sender_name or sender_id,
                        f"flagged: {text[:120]}")
        return
    db.log_dm(platform, mid, sender_id, sender_name, text, product, reply,
              "pending", "", brand["key"])
    if config.REPLY_MODE == "auto":
        reason = db.dm_rate_limit_reason(brand["key"], platform, sender_id)
        if reason:
            db.update_dm(mid, error=reason)
            return
        import random
        import time as _t
        _t.sleep(random.uniform(config.DM_DELAY_MIN, config.DM_DELAY_MAX))
        send_dm(mid)

def _sender_name(brand: dict, sender_id: str) -> str:
    try:
        data = _get(brand["page_token"], sender_id, fields="name,username")
        return data.get("name") or data.get("username") or ""
    except Exception:
        return ""

def send_dm(message_id: str):
    with db.connect() as con:
        row = con.execute("SELECT * FROM dms WHERE message_id=?", (message_id,)).fetchone()
    if not row or row["status"] == "sent":
        return
    brand = config.BRANDS.get(row["brand"]) or next(iter(config.BRANDS.values()))
    try:
        _post(brand["page_token"], "me/messages",
              recipient=jsonlib.dumps({"id": row["sender_id"]}),
              message=jsonlib.dumps({"text": row["reply_text"]}),
              messaging_type="RESPONSE")
        db.update_dm(message_id, status="sent")
        db.log_activity(row["brand"], row["platform"], "dm_reply",
                        row["sender_name"] or row["sender_id"],
                        f"→ {row['reply_text'][:200]}")
    except Exception as e:
        log.exception("send DM failed")
        db.update_dm(message_id, status="failed", error=str(e)[:500])
        db.log_activity(row["brand"], row["platform"], "error",
                        row["sender_id"], f"DM send failed: {str(e)[:200]}")

# ---------- publishing ----------

def publish_post(post: dict, brand: dict) -> dict:
    """Publish one post row to the brand's channels. FB only if 'facebook' is in
    the post's platforms (IG→FB crossposting usually covers Facebook)."""
    media_url = f"{config.BASE_URL}/media/{post['media_file']}"
    token = brand["page_token"]
    results = {}
    platforms = post.get("platforms") or "instagram"
    collab = (post.get("collab_with") or "").strip()
    # Instagram
    try:
        ig_params = {"caption": post["caption_ig"]}
        if collab:
            ig_params["collaborators"] = jsonlib.dumps([collab])
        if post["media_type"] == "video":
            c = _post(token, f"{brand['ig_user_id']}/media", media_type="REELS",
                      video_url=media_url, **ig_params)
            _wait_ig_container(token, c["id"])
        else:
            c = _post(token, f"{brand['ig_user_id']}/media",
                      image_url=media_url, **ig_params)
        pub = _post(token, f"{brand['ig_user_id']}/media_publish", creation_id=c["id"])
        results["instagram"] = f"ok:{pub.get('id')}" + (f" (collab invite → {collab})" if collab else "")
    except Exception as e:
        results["instagram"] = f"error:{str(e)[:300]}"
    # Facebook — only when explicitly requested (IG→FB crossposting covers it otherwise)
    if "facebook" in platforms:
        try:
            if post["media_type"] == "video":
                pub = _post(token, f"{brand['page_id']}/videos",
                            file_url=media_url, description=post["caption_fb"])
            else:
                pub = _post(token, f"{brand['page_id']}/photos",
                            url=media_url, message=post["caption_fb"])
            results["facebook"] = f"ok:{pub.get('id') or pub.get('post_id')}"
        except Exception as e:
            results["facebook"] = f"error:{str(e)[:300]}"
    db.log_activity(brand["key"], "instagram,facebook", "publish",
                    f"post #{post.get('id')}", str(results)[:400])
    return results

def _wait_ig_container(token: str, container_id: str, tries=30):
    import time
    for _ in range(tries):
        st = _get(token, container_id, fields="status_code")
        if st.get("status_code") == "FINISHED":
            return
        if st.get("status_code") == "ERROR":
            raise RuntimeError(f"IG container error: {st}")
        time.sleep(10)
    raise TimeoutError("IG video container not ready after 5 minutes")
