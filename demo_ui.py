#!/usr/bin/env python
"""Minimal local web UI for the TikTok app review demo recording.

This is NOT part of the production pipeline (that stays fully automated via
auto_daily.py / the scheduled tasks). It exists purely because TikTok's app
reviewers require the demo video to show "the user interaction interface,
not the code" — this project is a headless automation pipeline with no
normal end-user UI, so this wraps the existing tiktok_onboard.py /
tiktok_post.py logic in two buttons or the sole purpose of having something
screen-recordable.

Usage:
    python demo_ui.py
Then open http://127.0.0.1:5055 in a browser.
"""
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template_string, request

import tiktok_onboard as onboard
import tiktok_post as poster

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

app = Flask(__name__)

_pending = {}  # holds code_verifier between /connect/start and /connect/complete


PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Mind-Blowing Facts — Creator Dashboard</title>
<style>
  :root { --bg:#0c0a12; --raised:#14111d; --purple:#7c3aed; --purple-bright:#9d5cf5;
          --text:#f5f3f9; --dim:#a89fc2; --border:rgba(245,243,249,0.12); --ok:#4ade80; --err:#f87171; }
  * { box-sizing: border-box; }
  body { background: var(--bg); color: var(--text); font-family: system-ui, sans-serif;
         max-width: 640px; margin: 48px auto; padding: 0 24px; line-height: 1.5; }
  h1 { font-size: 1.5rem; margin-bottom: 4px; }
  .sub { color: var(--dim); margin-bottom: 32px; }
  .card { background: var(--raised); border: 1px solid var(--border); border-radius: 14px;
          padding: 24px; margin-bottom: 20px; }
  .card h2 { font-size: 1.1rem; margin: 0 0 6px; }
  .card p { color: var(--dim); font-size: 0.9rem; margin: 0 0 16px; }
  button { background: var(--purple); color: white; border: none; border-radius: 8px;
           padding: 10px 18px; font-size: 0.95rem; cursor: pointer; font-weight: 600; }
  button:hover { background: var(--purple-bright); }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  input, select { width: 100%; padding: 10px 12px; border-radius: 8px; border: 1px solid var(--border);
         background: #1a1725; color: var(--text); font-size: 0.9rem; margin-bottom: 10px; }
  .status { margin-top: 14px; padding: 12px; border-radius: 8px; font-size: 0.88rem;
            white-space: pre-wrap; display: none; }
  .status.show { display: block; }
  .status.ok { background: rgba(74,222,128,0.12); color: var(--ok); border: 1px solid rgba(74,222,128,0.3); }
  .status.err { background: rgba(248,113,113,0.12); color: var(--err); border: 1px solid rgba(248,113,113,0.3); }
  .status.pending { background: rgba(157,92,245,0.12); color: var(--purple-bright); border: 1px solid rgba(157,92,245,0.3); }
  a.authlink { color: var(--purple-bright); word-break: break-all; }
  .step { display: none; }
  .step.show { display: block; }
</style>
</head>
<body>
  <h1>Mind-Blowing Facts — Creator Dashboard</h1>
  <p class="sub">Connect creator accounts and publish today's video.</p>

  <div class="card">
    <h2>1. Connect a TikTok Account</h2>
    <p>Sign in with the creator's TikTok account to authorize daily posting.</p>

    <div id="connect-step1" class="step show">
      <input id="label-input" placeholder="Label this account (e.g. 'you')" value="demo">
      <button onclick="startConnect()">Connect TikTok Account</button>
    </div>

    <div id="connect-step2" class="step">
      <p>Open the link below, sign in, and approve access. Then paste the code TikTok gives you back here.</p>
      <p><a class="authlink" id="auth-url" href="#" target="_blank"></a></p>
      <input id="code-input" placeholder="Paste authorization code here">
      <button onclick="completeConnect()">Finish Connecting</button>
    </div>

    <div id="connect-status" class="status"></div>
  </div>

  <div class="card">
    <h2>2. Publish Today's Video</h2>
    <p>Post the next ready video to every connected TikTok account.</p>
    <select id="day-select"></select>
    <button onclick="postVideo()">Publish to TikTok</button>
    <div id="post-status" class="status"></div>
  </div>

<script>
async function loadDays() {
  const r = await fetch('/days');
  const days = await r.json();
  const sel = document.getElementById('day-select');
  sel.innerHTML = days.map(d => `<option value="${d}">${d}</option>`).join('');
}
loadDays();

async function startConnect() {
  const label = document.getElementById('label-input').value || 'demo';
  const statusEl = document.getElementById('connect-status');
  statusEl.className = 'status show pending';
  statusEl.textContent = 'Generating authorization link...';
  const r = await fetch('/connect/start', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({label})
  });
  const data = await r.json();
  if (data.error) {
    statusEl.className = 'status show err';
    statusEl.textContent = data.error;
    return;
  }
  document.getElementById('auth-url').href = data.url;
  document.getElementById('auth-url').textContent = data.url;
  document.getElementById('connect-step1').classList.remove('show');
  document.getElementById('connect-step2').classList.add('show');
  statusEl.className = 'status';
  window.open(data.url, '_blank');
}

async function completeConnect() {
  const code = document.getElementById('code-input').value;
  const statusEl = document.getElementById('connect-status');
  statusEl.className = 'status show pending';
  statusEl.textContent = 'Exchanging code for access token...';
  const r = await fetch('/connect/complete', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({code})
  });
  const data = await r.json();
  if (data.error) {
    statusEl.className = 'status show err';
    statusEl.textContent = data.error;
    return;
  }
  statusEl.className = 'status show ok';
  statusEl.textContent = `Connected! open_id: ${data.open_id}\\nGranted scopes: ${data.scope}`;
  document.getElementById('connect-step2').classList.remove('show');
  document.getElementById('connect-step1').classList.add('show');
}

async function postVideo() {
  const day = document.getElementById('day-select').value;
  const statusEl = document.getElementById('post-status');
  statusEl.className = 'status show pending';
  statusEl.textContent = 'Publishing to TikTok...';
  const r = await fetch('/post', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({day})
  });
  const data = await r.json();
  statusEl.className = 'status show ' + (data.ok ? 'ok' : 'err');
  statusEl.textContent = data.message;
}
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(PAGE)


@app.route("/days")
def days():
    out_dir = ROOT / "output"
    result = []
    for d in sorted(out_dir.iterdir()):
        if d.is_dir() and re.match(r"day_\d+", d.name) and (d / "video.mp4").exists():
            result.append(d.name)
    return jsonify(result)


@app.route("/connect/start", methods=["POST"])
def connect_start():
    label = request.json.get("label", "demo")
    client_key = os.environ.get("TIKTOK_SANDBOX_CLIENT_KEY")
    if not client_key:
        return jsonify(error="Missing TIKTOK_SANDBOX_CLIENT_KEY in .env"), 400
    url, code_verifier, state = onboard.build_auth_url(client_key)
    _pending["label"] = label
    _pending["code_verifier"] = code_verifier
    return jsonify(url=url)


@app.route("/connect/complete", methods=["POST"])
def connect_complete():
    code = request.json.get("code", "").strip()
    if not code:
        return jsonify(error="No code provided"), 400
    if "code_verifier" not in _pending:
        return jsonify(error="Start the connection first"), 400

    client_key = os.environ.get("TIKTOK_SANDBOX_CLIENT_KEY")
    client_secret = os.environ.get("TIKTOK_SANDBOX_CLIENT_SECRET")
    try:
        token_data = onboard.exchange_code(client_key, client_secret, code, _pending["code_verifier"])
    except Exception as e:
        return jsonify(error=f"Token exchange failed: {e}"), 400

    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=token_data.get("expires_in", 0))

    registry = onboard.load_accounts()
    accounts = registry["accounts"]
    existing = next((a for a in accounts if a["open_id"] == token_data["open_id"]), None)
    record = {
        "open_id": token_data["open_id"],
        "label": _pending["label"],
        "environment": "sandbox",
        "access_token": token_data["access_token"],
        "refresh_token": token_data["refresh_token"],
        "scope": token_data["scope"],
        "access_token_expires_at": expires_at.isoformat(),
        "added_at": now.isoformat(),
    }
    if existing:
        accounts[accounts.index(existing)] = record
    else:
        accounts.append(record)
    onboard.save_accounts(registry)
    _pending.clear()

    return jsonify(open_id=token_data["open_id"], scope=token_data["scope"])


@app.route("/post", methods=["POST"])
def post():
    day = request.json.get("day")
    if not day:
        return jsonify(ok=False, message="No day selected"), 400
    day_dir = ROOT / "output" / day
    try:
        poster.publish_all(day_dir, dry_run=False)
        return jsonify(ok=True, message=f"Published {day} to TikTok successfully.")
    except Exception as e:
        return jsonify(ok=False, message=str(e))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5055))
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    app.run(host=host, port=port, debug=False)
