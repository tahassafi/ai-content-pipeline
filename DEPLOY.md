# Social Agent — VPS Deployment Guide

An always-on agent that auto-replies to Instagram/Facebook/YouTube comments and
publishes scheduled posts with AI captions, via a web dashboard on your subdomain.

## 1. Upload & install (on the VPS)

```bash
# copy the ai-content-pipeline folder to the VPS, e.g.
scp -r ai-content-pipeline user@your-vps:/opt/ai-content-pipeline
ssh user@your-vps

cd /opt/ai-content-pipeline
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env
nano .env   # fill in values (steps below tell you where each comes from)
```

## 2. DNS + HTTPS (subdomain)

Point a subdomain at the VPS: create an **A record** `agent.yourdomain.com → your VPS IP`.

Install nginx + certbot:

```bash
sudo apt install nginx certbot python3-certbot-nginx -y
sudo nano /etc/nginx/sites-available/ai-content-pipeline
```

Paste:

```nginx
server {
    server_name agent.yourdomain.com;
    client_max_body_size 500M;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/ai-content-pipeline /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d agent.yourdomain.com   # free HTTPS cert
```

## 3. Meta setup (Instagram + Facebook)

You already have a Meta app. In [developers.facebook.com](https://developers.facebook.com) → your app:

1. **Permissions** (via Graph API Explorer or your app's login flow), your Page
   token needs: `pages_manage_engagement`, `pages_read_engagement`,
   `pages_show_list`, `instagram_basic`, `instagram_manage_comments`,
   `pages_manage_posts` (for publishing), `instagram_content_publish`.
2. **Get a long-lived Page token**: Graph API Explorer → select your app + the
   permissions above → Generate token → then exchange it:
   `GET /oauth/access_token?grant_type=fb_exchange_token&client_id=APP_ID&client_secret=APP_SECRET&fb_exchange_token=SHORT_TOKEN`
   → then `GET /me/accounts` with that token → copy the **Page access token**
   (Page tokens from long-lived user tokens don't expire). Put it in `.env` as `META_PAGE_TOKEN`.
3. **IDs**: `GET /me/accounts` gives your `META_PAGE_ID`. Then
   `GET /{page-id}?fields=instagram_business_account` gives `IG_USER_ID`.
4. **Webhooks**: App dashboard → Webhooks:
   - Object **Page** → Callback URL `https://agent.yourdomain.com/webhook/meta`,
     Verify token = your `META_VERIFY_TOKEN` → subscribe to field **feed**.
   - Object **Instagram** → same callback → subscribe to field **comments**.
   - Then subscribe your Page to the app:
     `POST /{page-id}/subscribed_apps?subscribed_fields=feed` (with Page token).
5. If your app is in Development mode it only receives webhooks for accounts with
   a role on the app — that's fine since it's your own account. For it to work
   long-term, switch the app to **Live** mode (the permissions above may require
   completing basic App Review / Business verification).

## 4. Claude API

Put your existing key in `.env` (`ANTHROPIC_API_KEY`). Set `BRAND_VOICE` — this
controls the tone of every caption and reply. `REPLY_MODE=auto` posts replies
instantly; switch to `approve` anytime to review them in the dashboard first.

## 5. YouTube (optional, enable later)

1. [console.cloud.google.com](https://console.cloud.google.com) → new project →
   enable **YouTube Data API v3**.
2. Credentials → **OAuth client ID** → type "Desktop app" → download the JSON.
3. On your **own PC** (needs a browser):
   `pip install google-auth-oauthlib` then
   `python scripts/yt_auth.py client_secret.json` → log into your channel account.
4. Copy the produced `data/yt_token.json` to the VPS at
   `/opt/ai-content-pipeline/data/yt_token.json`.
5. In `.env`: `YT_ENABLED=true`, set `YT_CHANNEL_ID` (YouTube Studio →
   Settings → Channel → Advanced → Channel ID). Restart the service.

Note: YouTube API default quota (10,000 units/day) comfortably covers polling
every 5 minutes + replies.

## 6. Run as a service (always on)

```bash
sudo nano /etc/systemd/system/ai-content-pipeline.service
```

```ini
[Unit]
Description=Social Agent
After=network.target

[Service]
WorkingDirectory=/opt/ai-content-pipeline
ExecStart=/opt/ai-content-pipeline/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
User=www-data
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

```bash
sudo chown -R www-data:www-data /opt/ai-content-pipeline
sudo systemctl enable --now ai-content-pipeline
sudo systemctl status ai-content-pipeline      # check it's running
journalctl -u ai-content-pipeline -f           # live logs
```

## 7. Test checklist

1. Open `https://agent.yourdomain.com` → log in with `DASHBOARD_PASSWORD`.
2. Upload a photo with notes → captions should appear (Claude API working).
3. Click **Publish now** → check IG + FB.
4. Comment on your own IG post **from another account** → reply should appear
   within seconds (webhook working). Check the Replies table on the dashboard.
5. Schedule a post 2 minutes ahead → confirm it publishes.

## Troubleshooting

- **Webhook verify fails**: `META_VERIFY_TOKEN` in `.env` must exactly match the
  token entered in the Meta webhook config; HTTPS must be valid.
- **No comment events**: make sure the Page is subscribed to the app (step 3.4)
  and the app has the Instagram/Page webhook fields subscribed.
- **IG publish error "media not reachable"**: `BASE_URL` must be your public
  HTTPS subdomain — Meta downloads media from `BASE_URL/media/<file>`.
- **Replies flagged/skipped**: the AI intentionally skips spam/sensitive
  comments; they appear as `skipped` in the dashboard.
