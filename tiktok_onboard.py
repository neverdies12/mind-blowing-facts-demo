#!/usr/bin/env python
"""Connects a new TikTok account to the app and registers it in
tiktok_accounts.json, so auto_daily.py posts to it going forward.

This is the multi-user replacement for the old single-account flow (build
URL -> manually exchange -> overwrite one token file). Each account (you,
each tester) runs through this once.

Usage:
    python tiktok_onboard.py --label alex
    python tiktok_onboard.py --label alex --sandbox
"""
import argparse
import base64
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

REDIRECT_URI = "https://themindsblowingfacts.com/tiktok-callback.html"
SCOPES = "user.info.basic,video.publish"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
ACCOUNTS_FILE = ROOT / "tiktok_accounts.json"


def load_accounts() -> dict:
    if ACCOUNTS_FILE.exists():
        return json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    return {"accounts": []}


def save_accounts(registry: dict):
    ACCOUNTS_FILE.write_text(json.dumps(registry, indent=2), encoding="utf-8")


def build_auth_url(client_key: str) -> tuple[str, str, str]:
    code_verifier = secrets.token_urlsafe(64)[:128]
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).decode().rstrip("=")
    state = secrets.token_urlsafe(16)

    params = {
        "client_key": client_key,
        "scope": SCOPES,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    url = "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(params)
    return url, code_verifier, state


def exchange_code(client_key: str, client_secret: str, code: str, code_verifier: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
            "code_verifier": code_verifier,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    if "access_token" not in data:
        raise RuntimeError(f"Token exchange did not return an access_token: {data}")
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True, help="a name to identify this account, e.g. 'alex'")
    parser.add_argument("--sandbox", action="store_true", help="use TIKTOK_SANDBOX_CLIENT_KEY instead")
    args = parser.parse_args()

    if args.sandbox:
        client_key = os.environ.get("TIKTOK_SANDBOX_CLIENT_KEY")
        client_secret = os.environ.get("TIKTOK_SANDBOX_CLIENT_SECRET")
    else:
        client_key = os.environ.get("TIKTOK_CLIENT_KEY")
        client_secret = os.environ.get("TIKTOK_CLIENT_SECRET")
    if not client_key or not client_secret:
        raise SystemExit("Missing client key/secret in .env")

    url, code_verifier, state = build_auth_url(client_key)
    print(f"1. Open this URL, log into the TikTok account for '{args.label}', and approve access:\n")
    print(url)
    print(f"\n2. TikTok will redirect to {REDIRECT_URI}?code=...")
    print("   Copy the 'code' value shown on that page (use the Copy button).\n")

    code = input("Paste the code here: ").strip()
    if not code:
        raise SystemExit("No code entered, aborting.")

    print("\nExchanging code for a token...")
    token_data = exchange_code(client_key, client_secret, code, code_verifier)

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=token_data.get("expires_in", 0))

    registry = load_accounts()
    accounts = registry["accounts"]
    existing = next((a for a in accounts if a["open_id"] == token_data["open_id"]), None)
    record = {
        "open_id": token_data["open_id"],
        "label": args.label,
        "environment": "sandbox" if args.sandbox else "production",
        "access_token": token_data["access_token"],
        "refresh_token": token_data["refresh_token"],
        "scope": token_data["scope"],
        "access_token_expires_at": expires_at.isoformat(),
        "added_at": now.isoformat(),
    }
    if existing:
        accounts[accounts.index(existing)] = record
        print(f"\nUpdated existing account '{args.label}' (open_id {token_data['open_id']}).")
    else:
        accounts.append(record)
        print(f"\nAdded '{args.label}' (open_id {token_data['open_id']}) to tiktok_accounts.json.")

    save_accounts(registry)
    print(f"scope granted: {token_data['scope']}")


if __name__ == "__main__":
    main()
