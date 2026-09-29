#!/usr/bin/env python
"""Publish a day's video to every connected TikTok account via the Direct
Post API (PULL_FROM_URL), mirroring instagram_post.py's shape but fanned
out across tiktok_accounts.json instead of a single token file.

Until the app passes TikTok's Content Posting API audit, unaudited apps are
forced to privacy_level SELF_ONLY (posts are private, visible only to the
poster) — see TIKTOK_PRIVACY_LEVEL in .env. Flip that to PUBLIC_TO_EVERYONE
once the app is approved.

Usage:
    python tiktok_post.py output/day_16
    python tiktok_post.py output/day_16 --dry-run
"""
import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from media_hosting import push_video_git

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

ACCOUNTS_FILE = ROOT / "tiktok_accounts.json"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
PRIVACY_LEVEL = os.environ.get("TIKTOK_PRIVACY_LEVEL", "SELF_ONLY")


def load_accounts() -> dict:
    if not ACCOUNTS_FILE.exists():
        raise SystemExit(f"Missing {ACCOUNTS_FILE} — run tiktok_onboard.py to connect at least one account first.")
    return json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))


def save_accounts(registry: dict):
    ACCOUNTS_FILE.write_text(json.dumps(registry, indent=2), encoding="utf-8")


def creds_for_account(account: dict) -> tuple[str, str]:
    """Sandbox-onboarded accounts have refresh_tokens tied to the sandbox
    app; refreshing them with the production client_key/secret always
    fails with invalid_grant, so each account must use whichever app
    actually issued its token."""
    if account.get("environment") == "sandbox":
        return (os.environ.get("TIKTOK_SANDBOX_CLIENT_KEY"), os.environ.get("TIKTOK_SANDBOX_CLIENT_SECRET"))
    return (os.environ.get("TIKTOK_CLIENT_KEY"), os.environ.get("TIKTOK_CLIENT_SECRET"))


def refresh_if_needed(account: dict, client_key: str, client_secret: str) -> bool:
    """Refreshes account['access_token'] in place if it's expired or about
    to expire. Returns True if the account dict was modified."""
    expires_at = datetime.fromisoformat(account["access_token_expires_at"])
    if expires_at - datetime.now(timezone.utc) > timedelta(minutes=5):
        return False

    resp = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": account["refresh_token"],
        },
    )
    resp.raise_for_status()
    data = resp.json()
    if "access_token" not in data:
        raise RuntimeError(f"Refresh failed for '{account['label']}': {data}")

    now = datetime.now(timezone.utc)
    account["access_token"] = data["access_token"]
    account["refresh_token"] = data.get("refresh_token", account["refresh_token"])
    account["access_token_expires_at"] = (now + timedelta(seconds=data.get("expires_in", 0))).isoformat()
    return True


def post_to_account(account: dict, video_url: str, title: str, dry_run: bool = False) -> str:
    """Returns the publish_id. Raises on failure."""
    if dry_run:
        print(f"  --dry-run: would post to '{account['label']}' (open_id {account['open_id']}), "
              f"privacy_level={PRIVACY_LEVEL}")
        return "dry-run"

    resp = requests.post(
        INIT_URL,
        headers={
            "Authorization": f"Bearer {account['access_token']}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json={
            "post_info": {
                "title": title,
                "privacy_level": PRIVACY_LEVEL,
            },
            "source_info": {
                "source": "PULL_FROM_URL",
                "video_url": video_url,
            },
        },
    )
    if not resp.ok:
        raise RuntimeError(f"init HTTP {resp.status_code} for '{account['label']}': {resp.text}")
    body = resp.json()
    if body.get("error", {}).get("code") not in (None, "ok"):
        raise RuntimeError(f"init failed for '{account['label']}': {body['error']}")
    publish_id = body["data"]["publish_id"]
    print(f"  [{account['label']}] post initiated: {publish_id}")

    status = None
    for _ in range(30):
        status_resp = requests.post(
            STATUS_URL,
            headers={
                "Authorization": f"Bearer {account['access_token']}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={"publish_id": publish_id},
        )
        status_resp.raise_for_status()
        status = status_resp.json().get("data", {}).get("status")
        print(f"    [{account['label']}] status: {status}")
        if status in ("PUBLISH_COMPLETE", "FAILED"):
            break
        time.sleep(10)

    if status != "PUBLISH_COMPLETE":
        raise RuntimeError(f"'{account['label']}' did not complete (last status: {status})")
    return publish_id


def publish_all(day_dir: Path, dry_run: bool = False):
    video_path = day_dir / "video.mp4"
    meta_path = day_dir / "metadata.json"
    for p in (video_path, meta_path):
        if not p.exists():
            raise SystemExit(f"Missing {p} — run generate_video.py first")

    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    title = metadata["title"][:2200]

    registry = load_accounts()
    accounts = registry["accounts"]
    if not accounts:
        raise SystemExit("tiktok_accounts.json has no accounts — run tiktok_onboard.py first.")

    video_name = f"{day_dir.name}.mp4"
    print(f"  pushing {video_name} to hosting...")
    if dry_run:
        print(f"  --dry-run: would push {video_name} to the verified GitHub Pages domain")
        video_url = "(dry-run, no URL generated)"
    else:
        # must use the pre-verified GitHub Pages domain, not B2 — TikTok's
        # PULL_FROM_URL requires domain verification and only this domain
        # has it (see media_hosting.py's module docstring)
        video_url = push_video_git(video_path, video_name)

    successes, failures = [], []
    for account in accounts:
        try:
            if not dry_run:
                client_key, client_secret = creds_for_account(account)
                if not client_key or not client_secret:
                    raise RuntimeError(f"Missing client key/secret for environment '{account.get('environment', 'production')}'")
                if refresh_if_needed(account, client_key, client_secret):
                    print(f"  [{account['label']}] refreshed access token")
                    save_accounts(registry)
            post_to_account(account, video_url, title, dry_run=dry_run)
            successes.append(account["label"])
        except Exception as e:
            print(f"  [{account['label']}] failed: {e}")
            failures.append(account["label"])

    if not dry_run:
        save_accounts(registry)

    print(f"\nTikTok: {len(successes)} succeeded ({', '.join(successes) or '-'}), "
          f"{len(failures)} failed ({', '.join(failures) or '-'})")
    if not successes:
        raise RuntimeError("TikTok posting failed for every connected account")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("day_dir")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    publish_all(Path(args.day_dir), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
