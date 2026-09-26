"""One-time YouTube OAuth. Run ON YOUR OWN PC (needs a browser), then copy
data/yt_token.json to the VPS.

    pip install google-auth-oauthlib
    python scripts/yt_auth.py path/to/client_secret.json
"""
import sys
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

def main():
    secret = sys.argv[1] if len(sys.argv) > 1 else "client_secret.json"
    flow = InstalledAppFlow.from_client_secrets_file(secret, SCOPES)
    creds = flow.run_local_server(port=0)
    out = Path("data/yt_token.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(creds.to_json())
    print(f"Saved {out}. Copy it to the VPS at <project>/data/yt_token_<brand>.json")

if __name__ == "__main__":
    main()
