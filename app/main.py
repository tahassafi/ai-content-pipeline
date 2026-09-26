import hashlib
import logging
import secrets
import time
from pathlib import Path
from typing import List, Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import ai, config, db, inventory, meta, scheduler, web, youtube

logging.basicConfig(level=logging.INFO)
db.init()

app = FastAPI(title="Social Agent")
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET)
app.mount("/media", StaticFiles(directory=config.UPLOAD_DIR), name="media")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

PLATFORM_META = {
    "instagram": {"name": "Instagram", "icon": "IG", "color": "#E1306C", "publish": True},
    "facebook":  {"name": "Facebook",  "icon": "FB", "color": "#1877F2", "publish": True},
    "tiktok":    {"name": "TikTok",    "icon": "TT", "color": "#00c3b5", "publish": False},
    "youtube":   {"name": "YouTube",   "icon": "YT", "color": "#FF0000", "publish": False},
}


@app.on_event("startup")
def _startup():
    scheduler.start()


def logged_in(request: Request) -> bool:
    return request.session.get("auth") is True


def current_user(request: Request) -> str:
    return request.session.get("user", "unknown")


def is_admin(request: Request) -> bool:
    return request.session.get("role") == "admin"


def is_master(request: Request) -> bool:
    return request.session.get("user") == "master"


def act(request: Request, kind: str, target: str = "", detail: str = ""):
    """Log a dashboard action with the user who did it."""
    db.log_activity("", "dashboard", kind, target, detail, current_user(request))


def ctx(request: Request, **extra):
    base = {"platforms_meta": PLATFORM_META, "brands": config.BRANDS,
            "reply_mode": config.REPLY_MODE,
            "user": current_user(request), "admin": is_admin(request),
            "master": is_master(request)}
    base.update(extra)
    return base


# ---------- auth ----------

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": ""})


@app.post("/login")
def login(request: Request, password: str = Form(...), username: str = Form("")):
    username = username.strip().lower()
    # 1) named user account
    if username:
        row = db.verify_user(username, password)
        if row:
            request.session["auth"] = True
            request.session["user"] = row["username"]
            request.session["role"] = row["role"]
            db.log_activity("", "dashboard", "login", row["username"],
                            "signed in", row["username"])
            return RedirectResponse("/", status_code=303)
        db.log_activity("", "dashboard", "login_failed", username,
                        "wrong credentials", username or "unknown")
        return templates.TemplateResponse(
            request, "login.html", {"error": "Wrong username or password"}, status_code=401)
    # 2) master password (no username) — acts as admin
    if secrets.compare_digest(password, config.DASHBOARD_PASSWORD):
        request.session["auth"] = True
        request.session["user"] = "master"
        request.session["role"] = "admin"
        db.log_activity("", "dashboard", "login", "master", "signed in (master password)", "master")
        return RedirectResponse("/", status_code=303)
    db.log_activity("", "dashboard", "login_failed", "master", "wrong master password", "unknown")
    return templates.TemplateResponse(
        request, "login.html", {"error": "Wrong password"}, status_code=401
    )


@app.get("/logout")
def logout(request: Request):
    if logged_in(request):
        act(request, "logout", current_user(request), "signed out")
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------- user management (admin only) ----------

@app.get("/users", response_class=HTMLResponse)
def users_page(request: Request):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    if not is_admin(request):
        return RedirectResponse("/", status_code=303)
    with db.connect() as con:
        users = con.execute("SELECT id, username, role, created_at FROM users "
                            "ORDER BY id").fetchall()
    return templates.TemplateResponse(request, "users.html",
        ctx(request, active="users", users=users, is_admin=True,
            error=request.query_params.get("error", "")))


@app.post("/users/create")
def users_create(request: Request, username: str = Form(...),
                 password: str = Form(...), role: str = Form("editor")):
    if not logged_in(request) or not is_admin(request):
        return RedirectResponse("/", status_code=303)
    if role not in ("admin", "editor"):
        role = "editor"
    if len(password) < 8:
        return RedirectResponse("/users?error=Password+must+be+8%2B+characters", status_code=303)
    try:
        db.create_user(username, password, role)
        act(request, "user_created", username.strip().lower(), f"role: {role}")
    except Exception:
        return RedirectResponse("/users?error=Username+already+exists", status_code=303)
    return RedirectResponse("/users", status_code=303)


@app.post("/users/{user_id}/delete")
def users_delete(request: Request, user_id: int):
    if not logged_in(request) or not is_admin(request):
        return RedirectResponse("/", status_code=303)
    with db.connect() as con:
        row = con.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
        con.execute("DELETE FROM users WHERE id=?", (user_id,))
    if row:
        act(request, "user_deleted", row["username"])
    return RedirectResponse("/users", status_code=303)


# ---------- overview ----------

@app.get("/", response_class=HTMLResponse)
def overview(request: Request):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    with db.connect() as con:
        stats = {
            "drafts": con.execute("SELECT COUNT(*) c FROM posts WHERE status='draft'").fetchone()["c"],
            "scheduled": con.execute("SELECT COUNT(*) c FROM posts WHERE status='scheduled'").fetchone()["c"],
            "published": con.execute("SELECT COUNT(*) c FROM posts WHERE status='published'").fetchone()["c"],
            "replies_sent": con.execute("SELECT COUNT(*) c FROM replies WHERE status='sent'").fetchone()["c"],
            "replies_pending": con.execute("SELECT COUNT(*) c FROM replies WHERE status='pending'").fetchone()["c"],
        }
        recent_posts = con.execute("SELECT * FROM posts ORDER BY id DESC LIMIT 6").fetchall()
        recent_replies = con.execute("SELECT * FROM replies ORDER BY id DESC LIMIT 8").fetchall()
    return templates.TemplateResponse(request, "overview.html",
        ctx(request, active="overview", stats=stats,
            recent_posts=recent_posts, recent_replies=recent_replies))


# ---------- brand pages ----------

@app.get("/b/{brand}/instagram", response_class=HTMLResponse)
def instagram_page(request: Request, brand: str, warn: str = ""):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    if brand not in config.BRANDS:
        return RedirectResponse("/", status_code=303)
    with db.connect() as con:
        posts = con.execute(
            "SELECT * FROM posts WHERE brand=? AND platforms LIKE '%instagram%' "
            "ORDER BY id DESC LIMIT 50", (brand,)).fetchall()
    return templates.TemplateResponse(request, "instagram.html",
        ctx(request, active=f"{brand}:instagram", brand=brand,
            bmeta=config.BRANDS[brand], posts=posts, warn=warn))


@app.get("/b/{brand}/facebook", response_class=HTMLResponse)
def facebook_page(request: Request, brand: str, warn: str = ""):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    if brand not in config.BRANDS:
        return RedirectResponse("/", status_code=303)
    with db.connect() as con:
        posts = con.execute(
            "SELECT * FROM posts WHERE brand=? AND platforms LIKE '%facebook%' "
            "ORDER BY id DESC LIMIT 50", (brand,)).fetchall()
    return templates.TemplateResponse(request, "facebook.html",
        ctx(request, active=f"{brand}:facebook", brand=brand,
            bmeta=config.BRANDS[brand], posts=posts, warn=warn))


@app.get("/b/{brand}/publish")
def legacy_publish_page(brand: str):
    return RedirectResponse(f"/b/{brand}/instagram", status_code=303)


@app.get("/b/{brand}/tiktok", response_class=HTMLResponse)
def tiktok_page(request: Request, brand: str, warn: str = ""):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    if brand not in config.BRANDS:
        return RedirectResponse("/", status_code=303)
    with db.connect() as con:
        posts = con.execute(
            "SELECT * FROM posts WHERE brand=? AND platforms LIKE '%tiktok%' "
            "ORDER BY id DESC LIMIT 50", (brand,)).fetchall()
    return templates.TemplateResponse(request, "tiktok.html",
        ctx(request, active=f"{brand}:tiktok", brand=brand,
            bmeta=config.BRANDS[brand], posts=posts, warn=warn))


@app.get("/b/{brand}/youtube", response_class=HTMLResponse)
def youtube_page(request: Request, brand: str, warn: str = ""):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    if brand not in config.BRANDS:
        return RedirectResponse("/", status_code=303)
    with db.connect() as con:
        rows = con.execute(
            "SELECT * FROM posts WHERE brand=? AND platforms LIKE '%youtube%' "
            "ORDER BY id DESC LIMIT 50", (brand,)).fetchall()
    posts = []
    for r in rows:
        p = dict(r)
        p["yt"] = ai.split_yt(p.get("caption_yt", ""))
        posts.append(p)
    return templates.TemplateResponse(request, "youtube.html",
        ctx(request, active=f"{brand}:youtube", brand=brand,
            bmeta=config.BRANDS[brand], posts=posts, warn=warn))


@app.get("/b/{brand}/captions")
def legacy_captions_page(brand: str):
    return RedirectResponse(f"/b/{brand}/youtube", status_code=303)


@app.get("/b/{brand}/p/{platform}")
def legacy_platform_page(brand: str, platform: str):
    """Old URLs → new pages."""
    if platform in ("instagram", "facebook", "tiktok", "youtube"):
        return RedirectResponse(f"/b/{brand}/{platform}", status_code=303)
    return RedirectResponse("/", status_code=303)


# ---------- replies page ----------

RANGE_SQL = {"today": "date('now')", "7d": "datetime('now','-7 day')",
             "30d": "datetime('now','-30 day')"}

@app.get("/replies", response_class=HTMLResponse)
def replies_page(request: Request, brand: str = "", platform: str = "",
                 status: str = "", range: str = "7d", q: str = ""):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    conds, params = [], []
    if brand:
        conds.append("brand=?"); params.append(brand)
    if platform == "meta":
        conds.append("platform IN ('instagram','facebook')")
    elif platform:
        conds.append("platform=?"); params.append(platform)
    if status:
        conds.append("status=?"); params.append(status)
    if range in RANGE_SQL:
        conds.append(f"created_at >= {RANGE_SQL[range]}")
    if q:
        conds.append("(comment_text LIKE ? OR commenter LIKE ? OR reply_text LIKE ?)")
        params += [f"%{q}%"] * 3
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    with db.connect() as con:
        replies = con.execute(
            f"SELECT * FROM replies {where} ORDER BY id DESC LIMIT 300", params).fetchall()
        counts = {r["status"]: r["c"] for r in con.execute(
            f"SELECT status, COUNT(*) c FROM replies {where} GROUP BY status", params)}
    active = ("replies-yt" if platform == "youtube"
              else "replies-meta" if platform == "meta" else "replies")
    return templates.TemplateResponse(request, "replies.html",
        ctx(request, active=active, replies=replies, counts=counts,
            f_brand=brand, f_platform=platform, f_status=status,
            f_range=range, f_q=q))


@app.post("/replies/bulk")
def replies_bulk(request: Request, background: BackgroundTasks,
                 action: str = Form(...),
                 comment_ids: List[str] = Form([])):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)

    def run_send(ids):
        for cid in ids:
            with db.connect() as con:
                row = con.execute("SELECT * FROM replies WHERE comment_id=?", (cid,)).fetchone()
            if not row or row["status"] != "pending":
                continue
            if row["platform"] == "youtube":
                youtube.send_reply(cid)
            else:
                meta.send_reply(row["platform"], cid)

    if action == "send" and comment_ids:
        background.add_task(run_send, list(comment_ids))
        act(request, "replies_bulk_approved", f"{len(comment_ids)} replies")
    elif action == "reject" and comment_ids:
        with db.connect() as con:
            qmarks = ",".join("?" * len(comment_ids))
            con.execute(f"UPDATE replies SET status='rejected' WHERE comment_id IN ({qmarks}) "
                        "AND status='pending'", list(comment_ids))
        act(request, "replies_bulk_rejected", f"{len(comment_ids)} replies")
    return RedirectResponse(request.headers.get("referer", "/replies"), status_code=303)


# ---------- DMs page ----------

@app.get("/dms", response_class=HTMLResponse)
def dms_page(request: Request, brand: str = "", platform: str = "",
             status: str = "", range: str = "7d", q: str = ""):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    conds, params = [], []
    if brand:
        conds.append("brand=?"); params.append(brand)
    if platform:
        conds.append("platform=?"); params.append(platform)
    if status:
        conds.append("status=?"); params.append(status)
    if range in RANGE_SQL:
        conds.append(f"created_at >= {RANGE_SQL[range]}")
    if q:
        conds.append("(message_text LIKE ? OR sender_name LIKE ? OR reply_text LIKE ?)")
        params += [f"%{q}%"] * 3
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    with db.connect() as con:
        dms = con.execute(
            f"SELECT * FROM dms {where} ORDER BY id DESC LIMIT 300", params).fetchall()
        counts = {r["status"]: r["c"] for r in con.execute(
            f"SELECT status, COUNT(*) c FROM dms {where} GROUP BY status", params)}
    return templates.TemplateResponse(request, "dms.html",
        ctx(request, active="dms", dms=dms, counts=counts,
            f_brand=brand, f_platform=platform, f_status=status,
            f_range=range, f_q=q))


@app.post("/dms/bulk")
def dms_bulk(request: Request, background: BackgroundTasks,
             action: str = Form(...), message_ids: List[str] = Form([])):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)

    def run_send(ids):
        for mid in ids:
            meta.send_dm(mid)

    if action == "send" and message_ids:
        background.add_task(run_send, list(message_ids))
        act(request, "dms_bulk_approved", f"{len(message_ids)} DMs")
    elif action == "reject" and message_ids:
        with db.connect() as con:
            qmarks = ",".join("?" * len(message_ids))
            con.execute(f"UPDATE dms SET status='rejected' WHERE message_id IN ({qmarks}) "
                        "AND status='pending'", list(message_ids))
        act(request, "dms_bulk_rejected", f"{len(message_ids)} DMs")
    return RedirectResponse(request.headers.get("referer", "/dms"), status_code=303)


# ---------- activity log ----------

@app.get("/activity", response_class=HTMLResponse)
def activity_page(request: Request, brand: str = "", kind: str = "",
                  range: str = "7d", q: str = ""):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    if not is_master(request):
        return RedirectResponse("/", status_code=303)
    conds, params = [], []
    if brand:
        conds.append("brand=?"); params.append(brand)
    if kind:
        conds.append("kind=?"); params.append(kind)
    if range in RANGE_SQL:
        conds.append(f"created_at >= {RANGE_SQL[range]}")
    if q:
        conds.append("(target LIKE ? OR detail LIKE ?)")
        params += [f"%{q}%"] * 2
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    with db.connect() as con:
        rows = con.execute(
            f"SELECT * FROM activity {where} ORDER BY id DESC LIMIT 500", params).fetchall()
    return templates.TemplateResponse(request, "activity.html",
        ctx(request, active="activity", rows=rows,
            f_brand=brand, f_kind=kind, f_range=range, f_q=q))


@app.get("/activity.csv")
def activity_csv(request: Request, brand: str = "", kind: str = "", range: str = "all"):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    if not is_master(request):
        return RedirectResponse("/", status_code=303)
    import csv
    import io
    conds, params = [], []
    if brand:
        conds.append("brand=?"); params.append(brand)
    if kind:
        conds.append("kind=?"); params.append(kind)
    if range in RANGE_SQL:
        conds.append(f"created_at >= {RANGE_SQL[range]}")
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    with db.connect() as con:
        rows = con.execute(
            f"SELECT created_at, brand, platform, kind, target, detail "
            f"FROM activity {where} ORDER BY id DESC", params).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["time_utc", "brand", "platform", "kind", "target", "detail"])
    for r in rows:
        w.writerow([r["created_at"], r["brand"], r["platform"], r["kind"],
                    r["target"], r["detail"]])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=agent-activity.csv"})


# ---------- actions ----------

@app.post("/upload")
async def upload(request: Request,
                 file: Optional[UploadFile] = File(None),
                 notes: str = Form(""), url: str = Form(""),
                 post_type: str = Form("image"),
                 brand: str = Form("main"),
                 collab_with: str = Form(""),
                 platforms: List[str] = Form(["instagram"]),
                 origin: str = Form("/")):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    if brand not in config.BRANDS:
        brand = next(iter(config.BRANDS))
    bcfg = config.BRANDS[brand]
    platforms = [p for p in platforms if p in PLATFORM_META] or ["instagram"]
    if post_type not in ai.POST_TYPE_RULES:
        post_type = "image"

    name, media_type, media_hash = "", "", ""
    if file and file.filename:
        ext = Path(file.filename).suffix.lower()
        media_type = "video" if ext in (".mp4", ".mov", ".m4v", ".webm") else "image"
        data = await file.read()
        media_hash = hashlib.sha256(data).hexdigest()
        name = f"{int(time.time())}{ext}"
        (config.UPLOAD_DIR / name).write_bytes(data)

    page_text = ""
    url = url.strip()
    car_data = inventory.get_car_by_url(url) if url else None
    if url:
        try:
            page_text, web_img = web.fetch_car_page(url)
            if not name and web_img:
                name, media_type = web_img, "image"
        except Exception as e:
            logging.getLogger("web").warning("page fetch failed: %s", e)

    if not media_type:
        media_type = "video" if post_type in ("reel", "video") else "image"

    # TikTok duplicate-content guard: same content must not hit two brands' TikToks
    warn = ""
    if "tiktok" in platforms:
        conflict = db.tiktok_conflict(media_hash, url, brand)
        if conflict:
            platforms = [p for p in platforms if p != "tiktok"]
            warn = f"tiktok-dup-{conflict['brand']}"

    media_path = (config.UPLOAD_DIR / name) if name else None
    captions = ai.generate_captions(notes, media_type, media_path,
                                    platforms, post_type, page_text, bcfg["voice"],
                                    car_data, brand)
    with db.connect() as con:
        con.execute(
            "INSERT INTO posts(media_file, media_type, notes, platforms, post_type, "
            "source_url, brand, collab_with, media_hash, "
            "caption_ig, caption_fb, caption_tt, caption_yt) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (name, media_type, notes, ",".join(platforms), post_type, url,
             brand, collab_with.strip(), media_hash,
             captions.get("instagram", ""), captions.get("facebook", ""),
             captions.get("tiktok", ""), captions.get("youtube", "")),
        )
    act(request, "captions_generated", f"brand={brand}",
        f"platforms={','.join(platforms)} type={post_type} url={url[:120]}")
    sep = "&" if "?" in origin else "?"
    return RedirectResponse(f"{origin}{sep}warn={warn}" if warn else origin,
                            status_code=303)


@app.post("/posts/{post_id}/save")
def save_post(request: Request, post_id: int,
              caption_ig: Optional[str] = Form(None),
              caption_fb: Optional[str] = Form(None),
              caption_tt: Optional[str] = Form(None),
              caption_yt: Optional[str] = Form(None),
              yt_title: Optional[str] = Form(None),
              yt_desc: Optional[str] = Form(None),
              yt_tags: Optional[str] = Form(None),
              scheduled_at: str = Form(""), action: str = Form("save"),
              origin: str = Form("/")):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    if caption_yt is None and any(v is not None for v in (yt_title, yt_desc, yt_tags)):
        caption_yt = ai.join_yt(yt_title or "", yt_desc or "", yt_tags or "")
    fields, values = [], []
    for col, val in (("caption_ig", caption_ig), ("caption_fb", caption_fb),
                     ("caption_tt", caption_tt), ("caption_yt", caption_yt)):
        if val is not None:
            fields.append(f"{col}=?")
            values.append(val)
    status = "scheduled" if action == "schedule" and scheduled_at else "draft"
    fields += ["scheduled_at=?", "status=?"]
    values += [scheduled_at, status, post_id]
    with db.connect() as con:
        con.execute(f"UPDATE posts SET {','.join(fields)} WHERE id=?", values)
    return RedirectResponse(origin, status_code=303)


@app.post("/posts/{post_id}/regen")
def regen_captions(request: Request, post_id: int, platform: str = Form(...),
                   origin: str = Form("/")):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    with db.connect() as con:
        row = con.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    if row and platform in ai.CAPTION_COLS:
        bcfg = config.BRANDS.get(row["brand"]) or next(iter(config.BRANDS.values()))
        media_path = (config.UPLOAD_DIR / row["media_file"]) if row["media_file"] else None
        page_text = ""
        car_data = inventory.get_car_by_url(row["source_url"]) if row["source_url"] else None
        if row["source_url"] and not car_data:
            try:
                page_text, _ = web.fetch_car_page(row["source_url"])
            except Exception:
                pass
        captions = ai.generate_captions(row["notes"], row["media_type"],
                                        media_path, [platform],
                                        row["post_type"] or "image", page_text,
                                        bcfg["voice"], car_data, row["brand"])
        with db.connect() as con:
            con.execute(f"UPDATE posts SET {ai.CAPTION_COLS[platform]}=? WHERE id=?",
                        (captions.get(platform, ""), post_id))
    return RedirectResponse(origin, status_code=303)


@app.post("/posts/{post_id}/delete")
def delete_post(request: Request, post_id: int, origin: str = Form("/")):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    with db.connect() as con:
        con.execute("DELETE FROM posts WHERE id=?", (post_id,))
    act(request, "post_deleted", f"post #{post_id}")
    return RedirectResponse(origin, status_code=303)


@app.post("/replies/{platform}/send")
def approve_reply(request: Request, platform: str, comment_id: str = Form(...)):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    if platform == "youtube":
        youtube.send_reply(comment_id)
    else:
        meta.send_reply(platform, comment_id)
    return RedirectResponse("/replies", status_code=303)


# ---------- Meta webhook ----------

@app.get("/webhook/meta")
def meta_verify(request: Request):
    qp = request.query_params
    if (qp.get("hub.mode") == "subscribe"
            and qp.get("hub.verify_token") == config.META_VERIFY_TOKEN):
        return PlainTextResponse(qp.get("hub.challenge", ""))
    return PlainTextResponse("verification failed", status_code=403)


@app.post("/webhook/meta")
async def meta_webhook(request: Request, background: BackgroundTasks):
    payload = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not meta.verify_signature(payload, sig):
        return PlainTextResponse("bad signature", status_code=403)
    body = await request.json()
    background.add_task(meta.handle_webhook, body)
    return PlainTextResponse("ok")


@app.get("/health")
def health():
    return {"ok": True}
