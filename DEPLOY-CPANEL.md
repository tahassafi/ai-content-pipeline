# Deployment on a cPanel/WHM VPS (safe mode — replaces steps 2 & 6 of DEPLOY.md)

**Read first:** this guide does NOT install nginx (Apache already owns ports
80/443 on cPanel — installing nginx could break your live sites). Everything the
agent needs lives in its own folder (`/opt/ai-content-pipeline`), its own Python venv,
and its own service on localhost port 8000. Your 4 websites are never touched.

**Safety net (do this first):** WHM → Backup → run a full account backup of your
main account, or snapshot the VPS from your provider's panel. 5 minutes, total
peace of mind.

## Step A — Create the subdomain (fixes your domain-limit error)

You have root, so raise the limit yourself:

1. **WHM** (https://your-vps-ip:2087) → **List Accounts** → click the account
   for yourdomain.com → **Modify Account** → increase **Maximum Addon Domains**
   / **Maximum Subdomains** → Save.
2. Back in **cPanel → Domains → Create A New Domain** → enter
   `agent.yourdomain.com` (full name this time). Document root can stay default
   — it won't be used.
3. Wait a few minutes: cPanel's **AutoSSL** will issue a free HTTPS certificate
   for the subdomain automatically (check cPanel → SSL/TLS Status; click "Run
   AutoSSL" to speed it up).

## Step B — Install the agent (isolated, can't affect websites)

SSH in as root. These commands only create new files in /opt:

```bash
mkdir -p /opt/ai-content-pipeline
# upload the ai-content-pipeline folder contents there (scp/SFTP), then:
cd /opt/ai-content-pipeline
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env
nano .env    # fill values; BASE_URL=https://agent.yourdomain.com
```

If `python3 -m venv` fails, install Python without touching system packages:
AlmaLinux/CentOS: `dnf install -y python3.11` then use `python3.11 -m venv venv`.

## Step C — Run as a service

```bash
cat > /etc/systemd/system/ai-content-pipeline.service <<'EOF'
[Unit]
Description=Social Agent
After=network.target

[Service]
WorkingDirectory=/opt/ai-content-pipeline
ExecStart=/opt/ai-content-pipeline/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

systemctl enable --now ai-content-pipeline
systemctl status ai-content-pipeline        # should say "active (running)"
```

This adds ONE new service. It does not modify Apache, MySQL, or any cPanel
service. If anything goes wrong: `systemctl stop ai-content-pipeline` and your server
is exactly as before.

## Step D — Connect the subdomain to the agent (Apache proxy)

cPanel supports per-domain Apache include files — scoped to ONLY this
subdomain, other sites unaffected. Replace `exampleuser` below with the actual
cPanel username if different:

```bash
mkdir -p /etc/apache2/conf.d/userdata/ssl/2_4/exampleuser/agent.yourdomain.com
cat > /etc/apache2/conf.d/userdata/ssl/2_4/exampleuser/agent.yourdomain.com/proxy.conf <<'EOF'
ProxyPreserveHost On
ProxyPass /.well-known !
ProxyPass / http://127.0.0.1:8000/
ProxyPassReverse / http://127.0.0.1:8000/
EOF

# same for plain http (optional but nice)
mkdir -p /etc/apache2/conf.d/userdata/std/2_4/exampleuser/agent.yourdomain.com
cp /etc/apache2/conf.d/userdata/ssl/2_4/exampleuser/agent.yourdomain.com/proxy.conf \
   /etc/apache2/conf.d/userdata/std/2_4/exampleuser/agent.yourdomain.com/proxy.conf

/scripts/ensure_vhost_includes --user=exampleuser   # applies includes
apachectl configtest                                # MUST say "Syntax OK"
/scripts/restartsrv_httpd                           # graceful, sites stay up
```

If `configtest` ever shows an error, just delete the two proxy.conf files and
restart httpd — everything returns to normal.

Note: on AlmaLinux/CloudLinux cPanel servers the path is `/etc/apache2/...` as
above (cPanel's EA4 uses this path even though it's Apache httpd).

## Step E — Test

1. `curl -s http://127.0.0.1:8000/health` on the VPS → `{"ok":true}`
2. Open https://agent.yourdomain.com → login page appears.
3. Continue with DEPLOY.md **step 3 (Meta setup)** onward — steps 3, 4, 5, 7
   are unchanged.

## What this setup can and cannot break

- Can break: nothing shared. Worst case the agent itself doesn't run.
- Cannot break: your 4 websites, their databases, mail, DNS — none of their
  files or configs are modified. The only shared component touched is two small
  Apache include files scoped to agent.yourdomain.com, fully reversible by
  deleting them.
