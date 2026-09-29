#!/usr/bin/env python
"""Shared helper for getting a day's video onto a URL Instagram/TikTok can
fetch from.

Both Instagram's Content Publishing API and TikTok's Direct Post API
(PULL_FROM_URL source) fetch video by URL rather than accepting a direct
upload. This used to be done by committing each video into a git repo and
serving it via GitHub Pages — but git history keeps every pushed file
forever unless manually rewritten, which repeatedly ate through this
project's fixed, unexpandable VM disk.

**Instagram** now uses a private Backblaze B2 bucket instead: private
bucket + a short-lived presigned URL means no public-bucket/billing step
was needed to set up, and nothing accumulates locally or in git history.

**TikTok is different**: its Direct Post API requires the video URL's
domain/prefix to be pre-verified with TikTok (`PULL_FROM_URL` fails with
`url_ownership_unverified` otherwise), and the only domain already verified
is the `faceless-yt-media` GitHub Pages site — a B2 URL wouldn't pass. So
TikTok keeps using git-based hosting, but pushes to a single constant
filename (`media/tiktok_current.mp4`) and amends the previous commit
instead of adding a new one, so the repo's size stays constant forever
instead of growing with every post — this is what actually fixes the disk
problem for this path, since switching hosting providers wasn't an option
here.
"""
import os
import subprocess
import time
from pathlib import Path

import boto3
import requests
from botocore.config import Config
from botocore.exceptions import ClientError
from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

GIT_MEDIA_REPO = Path(os.environ.get("GIT_MEDIA_REPO_PATH", r"C:\Users\Administrator\Documents\faceless-yt-media"))
GIT_MEDIA_BASE_URL = "https://themindsblowingfacts.com"
GIT_CONSTANT_FILENAME = "tiktok_current.mp4"
GIT_COMMIT_MARKER = "Update tiktok_current.mp4 for publishing"

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=f"https://{os.environ['B2_ENDPOINT']}",
            aws_access_key_id=os.environ["B2_KEY_ID"],
            aws_secret_access_key=os.environ["B2_APPLICATION_KEY"],
            # B2 rejects the default SigV2-style presigned URLs boto3 falls
            # back to for some S3-compatible endpoints — force SigV4
            config=Config(signature_version="s3v4"),
        )
    return _client


def push_video_public(video_path: Path, name: str, expires_in: int = 3600) -> str:
    """Uploads the video to the private B2 bucket and returns a presigned
    URL valid for `expires_in` seconds (default 1h — plenty of time for
    Instagram/TikTok to fetch it)."""
    client = _get_client()
    bucket = os.environ["B2_BUCKET_NAME"]
    client.upload_file(str(video_path), bucket, name)

    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": name},
        ExpiresIn=expires_in,
    )

    # B2/S3 uploads are strongly consistent, so this should pass on the
    # first try — kept as a safety net for transient network hiccups rather
    # than the old long-poll design GitHub Pages needed. B2 rejects HEAD
    # against presigned GET URLs, so use a streamed GET and close without
    # reading the body. 8 attempts over ~90s gives real transient failures
    # room to clear instead of failing the whole platform's post on one blip.
    last_err = None
    for attempt in range(8):
        try:
            with requests.get(url, timeout=15, stream=True) as resp:
                if resp.status_code == 200:
                    return url
                last_err = f"HTTP {resp.status_code}"
        except requests.RequestException as e:
            last_err = str(e)
        time.sleep(min(2 * (attempt + 1), 15))
    raise RuntimeError(f"Uploaded {name} but the presigned URL isn't reachable yet ({last_err})")


def push_video_git(video_path: Path, name: str, timeout: int = 300) -> str:
    """TikTok-specific: pushes to the pre-verified GitHub Pages domain,
    always overwriting the same filename so the repo's size never grows.
    `name` is only used in the commit message for a readable history —
    the actual served filename is always GIT_CONSTANT_FILENAME.
    """
    if not (GIT_MEDIA_REPO / ".git").exists():
        # first run on a fresh environment (e.g. a cloud deploy) — clone
        # instead of assuming a checkout already exists on disk
        token = os.environ["GITHUB_TOKEN"]
        user = os.environ["GITHUB_USER"]
        repo = os.environ["GITHUB_MEDIA_REPO"]
        GIT_MEDIA_REPO.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", f"https://{token}@github.com/{user}/{repo}.git", str(GIT_MEDIA_REPO)],
            check=True,
        )
        subprocess.run(["git", "config", "user.email", f"{user}@users.noreply.github.com"], cwd=GIT_MEDIA_REPO, check=True)
        subprocess.run(["git", "config", "user.name", user], cwd=GIT_MEDIA_REPO, check=True)

    dest_dir = GIT_MEDIA_REPO / "media"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / GIT_CONSTANT_FILENAME
    import shutil
    shutil.copy(video_path, dest)

    subprocess.run(["git", "add", f"media/{GIT_CONSTANT_FILENAME}"], cwd=GIT_MEDIA_REPO, check=True)

    last_msg = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"], cwd=GIT_MEDIA_REPO, capture_output=True, text=True
    ).stdout.strip()
    commit_msg = f"{GIT_COMMIT_MARKER} ({name})"
    # only amend if the previous commit was ALSO one of ours — otherwise
    # amending could clobber an unrelated commit (e.g. a manual website
    # edit) that happened to land as HEAD between two video pushes
    if last_msg.startswith(GIT_COMMIT_MARKER):
        commit_cmd = ["git", "commit", "--amend", "-m", commit_msg]
    else:
        commit_cmd = ["git", "commit", "-m", commit_msg]

    result = subprocess.run(commit_cmd, cwd=GIT_MEDIA_REPO, capture_output=True, text=True)
    already_committed = "nothing to commit" in result.stdout
    if result.returncode != 0 and not already_committed:
        raise RuntimeError(f"git commit failed: {result.stdout}\n{result.stderr}")
    subprocess.run(["git", "push", "origin", "main", "--force"], cwd=GIT_MEDIA_REPO, check=True)

    # keep the repo's actual on-disk size constant — amend alone leaves the
    # old blob as unreachable garbage until gc reclaims it. The reflog
    # keeps every amended-away commit "reachable" though, so gc alone does
    # nothing — it must be expired first, or the repo grows forever anyway
    # (this was a real bug: the repo hit 394MB and became unrecoverable via
    # gc, needing a full history reset to fix).
    subprocess.run(["git", "reflog", "expire", "--expire=now", "--all"],
                    cwd=GIT_MEDIA_REPO, capture_output=True, text=True)
    subprocess.run(["git", "gc", "--prune=now"], cwd=GIT_MEDIA_REPO, capture_output=True, text=True)

    # cache-bust: the same filename close together in time could otherwise
    # serve a stale cached copy from GitHub Pages' CDN (10-minute cache)
    url = f"{GIT_MEDIA_BASE_URL}/media/{GIT_CONSTANT_FILENAME}?v={int(time.time())}"

    waited = 0
    while waited < timeout:
        try:
            resp = requests.head(url, timeout=10)
            if resp.status_code == 200:
                return url
        except requests.RequestException:
            pass
        time.sleep(5)
        waited += 5
    raise RuntimeError(f"{url} never became reachable after {timeout}s")


def delete_video(name: str):
    """Removes a video from the bucket — used by prune_old_videos()."""
    client = _get_client()
    bucket = os.environ["B2_BUCKET_NAME"]
    client.delete_object(Bucket=bucket, Key=name)


def prune_old_videos(keep_recent: int = 3) -> int:
    """Keeps only the `keep_recent` most-recently-uploaded videos in the
    bucket, deleting the rest. Safe to call unconditionally every run —
    deletes are cheap/instant, no history to rewrite, no reason to
    threshold-gate this the way the old git-based prune had to. Returns
    the number of videos deleted."""
    client = _get_client()
    bucket = os.environ["B2_BUCKET_NAME"]
    resp = client.list_objects_v2(Bucket=bucket)
    objects = resp.get("Contents", [])
    if len(objects) <= keep_recent:
        return 0

    objects.sort(key=lambda o: o["LastModified"])
    to_delete = objects[:-keep_recent]
    for obj in to_delete:
        client.delete_object(Bucket=bucket, Key=obj["Key"])
    return len(to_delete)


def video_exists(name: str) -> bool:
    """Checks the B2 bucket (Instagram's backup) — used by cleanup.py."""
    client = _get_client()
    bucket = os.environ["B2_BUCKET_NAME"]
    try:
        client.head_object(Bucket=bucket, Key=name)
        return True
    except ClientError:
        return False


def git_video_pushed() -> bool:
    """Checks whether TikTok's git-hosted backup exists — used by
    cleanup.py as the other half of its safety check, since TikTok's
    successful push doesn't show up in the B2 bucket."""
    return (GIT_MEDIA_REPO / "media" / GIT_CONSTANT_FILENAME).exists()
