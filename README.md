# ai-content-pipeline

A self-hosted AI social media agent built for a luxury car dealership client.
It runs 24/7 on a small VPS and handles the conversational side of four
platforms — replying to comments and DMs in the brand's voice — while acting
as a caption studio for everything the client posts manually.

Built with FastAPI, SQLite, and the Anthropic Claude API.

## What it does

- **Auto-replies to comments** on Instagram, Facebook (via Meta webhooks,
  real-time) and YouTube (via polling) — multilingual, brand-voiced, grounded
  in the actual post content so it never confirms a wrong claim
- **Auto-replies to DMs** on Instagram, Messenger, and Facebook Marketplace —
  answers availability questions from the dealership's live inventory
  database, redirects everything to WhatsApp, and silently skips spam, bots,
  consignment offers, and shared reels (without spending an API call)
- **Generates platform-correct captions** for Instagram (carousel listing
  format, Reels, image posts), Facebook Reels, TikTok, and YouTube (separate
  Shorts and long-form formats with title / description / tags in separate
  copy boxes)
- **Multi-brand**: each brand has its own pages, tokens, voice, and footer;
  comments and DMs are routed to the right brand automatically
- **Safety rails**: hourly/daily/per-commenter rate caps, human-like random
  send delays, approval mode, and a complete audit log of every action
- **Multi-user dashboard** with roles (admin/editor), per-user action
  attribution, and a master-only activity log with CSV export

## Architecture

```
                       ┌─────────────────────────────────────────┐
                       │              VPS (systemd)              │
                       │                                         │
 Meta webhooks ───────▶│  FastAPI app (uvicorn, localhost:8000)  │
 (IG/FB comments, DMs) │  ├── webhook handler ──┐                │
                       │  ├── dashboard (Jinja) │                │
 Dashboard users ─────▶│  ├── APScheduler jobs  │                │
 (via nginx/Apache     │  │   ├── YouTube poll  ├──▶ Claude API  │
  reverse proxy+HTTPS) │  │   └── drain queue   │   (captions &  │
                       │  │                     │    replies)    │
 YouTube Data API ◀───▶│  ├── SQLite (posts,  ◀─┘                │
 (poll + reply)        │  │   replies, dms,                      │
                       │  │   activity, users)                   │
 Dealership MySQL ────▶│  └── read-only inventory lookups        │
 (cars table, RO user) └─────────────────────────────────────────┘
```

## Request / data flow

**Comment flow (IG/FB):** Meta POSTs to `/webhook/meta` → HMAC signature
verified with the app secret → event routed to the owning brand by page/IG
ID → post caption fetched for context → Claude drafts a reply in the brand
voice (or returns a skip marker for spam/sensitive comments) → rate caps
checked → in `auto` mode the reply is sent after a random human-like delay;
in `approve` mode it waits in the dashboard → everything lands in the audit
log.

**DM flow:** same webhook, `messaging` events → attachment-only messages
(shared reels etc.) are skipped before any AI call → the message (and
Marketplace product title, if present) is matched against the dealership's
own MySQL inventory → Claude answers availability from that data and always
redirects to WhatsApp → same caps/modes/logging.

**YouTube flow:** APScheduler polls each brand's channel every N minutes,
fetches video title/description for context, and replies through the
YouTube Data API using per-brand OAuth tokens.

**Caption flow:** the user pastes a car listing URL → the car row is looked
up directly in the site's database by URL slug (price, options, mileage,
overview) with a page-scrape fallback → Claude generates captions using
per-platform rule blocks (character limits, hashtag rules, fixed listing
templates) → the user copies and posts manually.

## Setup

1. **Server**: any Linux VPS. `python3 -m venv venv && venv/bin/pip install -r
   requirements.txt`, copy `.env.example` to `.env` and fill it in. Run with
   `venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000` behind any
   HTTPS reverse proxy. `DEPLOY.md` has a full nginx + systemd walkthrough;
   `DEPLOY-CPANEL.md` covers cPanel/Apache servers.
2. **Meta (IG + FB + DMs)**: create a Meta developer app → grant it
   `pages_show_list`, `pages_read_engagement`, `pages_manage_engagement`,
   `pages_manage_metadata`, `pages_messaging`, `instagram_basic`,
   `instagram_manage_comments`, `instagram_manage_messages` → exchange a
   Graph Explorer token for a long-lived one → collect each Page's ID, token,
   and IG account ID into the per-brand env vars.
3. **Webhooks**: in the app dashboard, subscribe the **Page** object
   (`feed`, `messages`) and the **Instagram** object (`comments`, `messages`)
   to `https://<BASE_URL>/webhook/meta` with your `META_VERIFY_TOKEN`, then
   subscribe each Page to the app via `POST /{page-id}/subscribed_apps`.
   The app must be switched to **Live** mode for Instagram events.
4. **YouTube**: create a Google Cloud project, enable YouTube Data API v3,
   create a Desktop OAuth client, run `scripts/yt_auth.py` once per brand
   channel on a machine with a browser, and copy the token files to `data/`.
5. **Inventory DB** (optional but recommended): create a read-only MySQL user
   with `SELECT` on the cars table only and fill the `MYSQL_*` vars.
6. **Scheduling**: no external cron is needed — APScheduler runs inside the
   app (YouTube polling and the rate-limit drain job). Keep the process alive
   with systemd (`Restart=always`).

## Design decisions

- **Webhooks where possible, polling where necessary.** Meta pushes comment
  and DM events in real time; YouTube has no comment webhooks, so a
  quota-cheap poll (1 unit/call) runs on an interval.
- **Prompt-layer platform rules instead of code-layer formatting.** Each
  platform's caption rules (character limits, hashtag caps, fixed listing
  templates, Shorts vs long-form) live as editable instruction blocks in
  `app/ai.py`, so format changes are text edits, not refactors.
- **Ground truth over generation.** Replies receive the actual post
  caption/video description as context so the model corrects wrong claims
  instead of agreeing with them, and DM availability answers come from the
  client's own database — the model is told explicitly when it must not
  claim availability.
- **Human-shaped automation.** Random send delays, hourly/daily/per-user
  caps, unique wording per reply, and a skip filter for spam keep the
  behavior inside platform norms; anything held by a cap drains gradually.
- **SQLite + single process.** At dealership scale (hundreds of
  interactions/day) a zero-ops embedded DB and one uvicorn process are
  simpler and more reliable than external infrastructure; the one external
  integration (inventory) is read-only by construction.
- **Auditability first.** Every action — sent, skipped, failed, human or
  agent, with the acting username — is written to an activity table with CSV
  export, because the client needed a complete record of what the agent does
  on their accounts.

## Notes

- TikTok has no comment/DM automation because its API doesn't allow it;
  the app generates TikTok captions only.
- All publishing is intentionally manual (caption studio model): the client
  posts through the native apps, and the agent handles conversations.

## License

All rights reserved — published for portfolio and code-review purposes only. See [LICENSE](LICENSE).
