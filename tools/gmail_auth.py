#!/usr/bin/env python3
"""One-time Gmail READ-ONLY consent for the inbox pull.

Why: the only inbox-capable token we inherited (kartik.pillai@lh2.ai, gmail.readonly) is revoked
(invalid_grant); every other Gmail token is gmail.send-only. Reading your inbox needs a fresh consent.

Run (opens your browser; sign in as the mailbox you want pulled, e.g. bhanu.enamala@lh2.ai):
    .venv/bin/python tools/gmail_auth.py
    .venv/bin/python tools/gmail_auth.py --client secrets/hubspot_client_secret_lh2pipeline.json   # alternate OAuth client

Writes secrets/gmail_readonly_token.json (chmod 600). Scope requested: gmail.readonly ONLY (cannot send, delete or modify).
If Google says the app is unverified / "access blocked", the OAuth consent screen must list your email as a test user
(Google Cloud console -> APIs & Services -> OAuth consent screen). If the consent screen is in 'Testing' mode,
refresh tokens expire after 7 days - that is the likely reason the old token died; publish the app to 'In production'
(internal, if your Workspace allows) to make it durable.
"""
import argparse, json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", default=str(ROOT / "secrets" / "companyops_client_secret.json"))
    ap.add_argument("--out", default=str(ROOT / "secrets" / "gmail_readonly_token.json"))
    ap.add_argument("--expect", default="bhanu.enamala@lh2.ai", help="warn if the consenting mailbox differs")
    ap.add_argument("--no-browser", action="store_true", help="print the URL instead of opening a browser")
    a = ap.parse_args()

    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    flow = InstalledAppFlow.from_client_secrets_file(a.client, SCOPES)
    creds = flow.run_local_server(port=0, open_browser=not a.no_browser, prompt="consent", access_type="offline")
    prof = build("gmail", "v1", credentials=creds, cache_discovery=False).users().getProfile(userId="me").execute()
    email = prof.get("emailAddress")
    print(f"Authorised mailbox: {email} | messages: {prof.get('messagesTotal')} | threads: {prof.get('threadsTotal')}")
    if a.expect and email and email.lower() != a.expect.lower():
        print(f"WARNING: expected {a.expect} but consented as {email}. Re-run if that is not the mailbox you want.", file=sys.stderr)

    data = json.loads(creds.to_json())
    data["account"] = email
    out = Path(a.out)
    out.write_text(json.dumps(data))
    os.chmod(out, 0o600)
    print(f"Saved token to {out} (never commit; secrets/ is gitignored).")


if __name__ == "__main__":
    main()
