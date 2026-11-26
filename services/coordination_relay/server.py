"""Coordination Relay — shared JSON endpoint for Architect/Builder real-time messaging.

Breaks STDIO isolation between Claude instances. Any instance can POST a message
and GET the conversation thread. Deployed on mqtt-broker.local.

Endpoints:
    GET  /messages                — Get all messages (or ?since=timestamp for incremental)
    POST /messages                — Post a message {from, text, channel}
    GET  /messages/{channel}      — Get messages for a specific channel
    GET  /channels                — List active channels
    DELETE /messages              — Clear all messages (admin)
    GET  /health                  — Health check

Port: 8422
"""

from __future__ import annotations
import json
import logging
import os
import time
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote, urlparse, parse_qs

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("coordination_relay")

_messages: list[dict] = []
_lock = threading.Lock()
_start_time = time.time()

#: Channels the UI lists before any message arrives. CONFIGURATION, not code: a deployment sets
#: RELAY_SEED_CHANNELS (comma-separated), beside RELAY_HOST / RELAY_PORT.
DEFAULT_SEED_CHANNELS = ("haven-reactivation", "general")


def seed_channels() -> list[str]:
    raw = os.environ.get("RELAY_SEED_CHANNELS")
    if raw is None:
        return list(DEFAULT_SEED_CHANNELS)
    return [c.strip() for c in raw.split(",") if c.strip()]


class RelayHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        logger.info(format % args)

    def _send_json(self, data, status=200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        params = parse_qs(parsed.query)

        if path == "/" or path == "/ui":
            return self._serve_ui()

        if path == "/export":
            with _lock:
                all_msgs = list(_messages)
            body = json.dumps({"exported_at": time.time(), "messages": all_msgs}, indent=2).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Disposition", "attachment; filename=relay_export.json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/health":
            return self._send_json({"status": "ok", "uptime": int(time.time() - _start_time)})

        if path == "/channels":
            with _lock:
                channels = sorted(set(m.get("channel", "general") for m in _messages))
            return self._send_json({"channels": channels})

        if path == "/messages":
            since = float(params.get("since", [0])[0])
            with _lock:
                msgs = [m for m in _messages if m["timestamp"] > since]
            return self._send_json({"messages": msgs, "count": len(msgs)})

        if path.startswith("/messages/"):
            channel = path[len("/messages/"):]
            since = float(params.get("since", [0])[0])
            with _lock:
                msgs = [m for m in _messages if m.get("channel") == channel and m["timestamp"] > since]
            return self._send_json({"messages": msgs, "count": len(msgs), "channel": channel})

        self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/messages":
            try:
                body = json.loads(self._read_body())
            except Exception:
                return self._send_json({"error": "invalid JSON"}, 400)

            # Assign id + append atomically under the lock; with one thread per
            # request, reading len(_messages) outside the lock would race and
            # hand two concurrent posters the same id.
            with _lock:
                msg = {
                    "id": len(_messages),
                    "timestamp": time.time(),
                    "from": body.get("from", "anonymous"),
                    "channel": body.get("channel", "general"),
                    "text": body.get("text", ""),
                }
                _messages.append(msg)
            logger.info(f"[{msg['channel']}] {msg['from']}: {msg['text'][:80]}")
            return self._send_json(msg, 201)

        self._send_json({"error": "not found"}, 404)

    def do_DELETE(self):
        if self.path == "/messages":
            with _lock:
                _messages.clear()
            return self._send_json({"status": "cleared"})
        self._send_json({"error": "not found"}, 404)

    @staticmethod
    def _ui_template() -> str:
        return """<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>ComposeIO Coordination Relay</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #1a1a2e; color: #eaeaea; font-family: 'Menlo', 'Courier New', monospace; font-size: 13px; }
  #header { background: #16213e; padding: 12px 20px; border-bottom: 1px solid #2a2a4e; display: flex; align-items: center; gap: 16px; }
  #header h1 { font-size: 16px; color: #4A9EFF; }
  #header .status { font-size: 11px; color: #22c55e; }
  #channels { background: #0f0f23; padding: 8px 20px; border-bottom: 1px solid #2a2a4e; display: flex; gap: 8px; }
  .chan-btn { background: #2a2a4e; color: #8888aa; border: none; padding: 6px 14px; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 12px; }
  .chan-btn.active { background: #4A9EFF; color: white; }
  .chan-btn:hover { background: #3a3a6e; }
  #messages { flex: 1; overflow-y: auto; padding: 12px 20px; }
  .msg { margin-bottom: 8px; padding: 6px 10px; border-left: 3px solid #333; border-radius: 0 4px 4px 0; background: #16213e; }
  .msg .meta { font-size: 11px; color: #666; margin-bottom: 2px; }
  .msg .sender { font-weight: bold; }
  .msg .sender.architect { color: #FFD700; }
  .msg .sender.builder { color: #90EE90; }
  .msg .sender.david, .msg .sender.human { color: #e94560; }
  .msg .text { white-space: pre-wrap; line-height: 1.4; }
  #input-area { background: #16213e; padding: 12px 20px; border-top: 1px solid #2a2a4e; display: flex; gap: 8px; }
  #input-area input, #input-area select { background: #0f0f23; color: #eaeaea; border: 1px solid #2a2a4e; padding: 8px 12px; border-radius: 4px; font-family: inherit; font-size: 13px; }
  #msg-input { flex: 1; }
  #from-input { width: 160px; }
  #send-btn { background: #4A9EFF; color: white; border: none; padding: 8px 20px; border-radius: 4px; cursor: pointer; font-family: inherit; font-weight: bold; }
  #send-btn:hover { background: #3a7edf; }
  #export-btn { background: #333; color: #aaa; border: none; padding: 4px 12px; border-radius: 4px; cursor: pointer; font-size: 11px; }
  .container { display: flex; flex-direction: column; height: 100vh; }
</style>
</head><body>
<div class="container">
  <div id="header">
    <h1>ComposeIO Coordination Relay</h1>
    <span class="status" id="status">connecting...</span>
    <a href="/export" id="export-btn">Export JSON</a>
  </div>
  <div id="channels"></div>
  <div id="messages"></div>
  <div id="input-area">
    <input id="from-input" placeholder="Your name..." value="david">
    <input id="msg-input" placeholder="Type a message..." autofocus>
    <button id="send-btn" onclick="sendMsg()">Send</button>
  </div>
</div>
<script>
let currentChannel = 'haven-reactivation';
let lastTimestamp = 0;
let channels = new Set(__SEED_CHANNELS__);

function senderClass(name) {
  const n = name.toLowerCase();
  if (n.includes('architect')) return 'architect';
  if (n.includes('builder')) return 'builder';
  if (n === 'david' || n === 'human') return 'david';
  return '';
}

function renderMsg(m) {
  const time = new Date(m.timestamp * 1000).toLocaleTimeString();
  const cls = senderClass(m.from);
  return `<div class="msg"><div class="meta"><span class="sender ${cls}">${m.from}</span> &middot; ${time}</div><div class="text">${escHtml(m.text)}</div></div>`;
}

function escHtml(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

function renderChannels() {
  const el = document.getElementById('channels');
  el.innerHTML = '';
  channels.forEach(ch => {
    const btn = document.createElement('button');
    btn.className = 'chan-btn' + (ch === currentChannel ? ' active' : '');
    btn.textContent = '#' + ch;
    btn.onclick = () => { currentChannel = ch; lastTimestamp = 0; document.getElementById('messages').innerHTML = ''; renderChannels(); poll(); };
    el.appendChild(btn);
  });
}

async function poll() {
  try {
    const r = await fetch('/messages/' + currentChannel + '?since=' + lastTimestamp);
    const data = await r.json();
    if (data.messages && data.messages.length > 0) {
      const el = document.getElementById('messages');
      data.messages.forEach(m => {
        el.innerHTML += renderMsg(m);
        if (m.timestamp > lastTimestamp) lastTimestamp = m.timestamp;
      });
      el.scrollTop = el.scrollHeight;
    }
    // discover new channels
    const cr = await fetch('/channels');
    const cd = await cr.json();
    cd.channels.forEach(ch => channels.add(ch));
    renderChannels();
    document.getElementById('status').textContent = 'connected — ' + new Date().toLocaleTimeString();
  } catch(e) {
    document.getElementById('status').textContent = 'error: ' + e.message;
    document.getElementById('status').style.color = '#ef4444';
  }
}

async function sendMsg() {
  const text = document.getElementById('msg-input').value.trim();
  const from = document.getElementById('from-input').value.trim() || 'anonymous';
  if (!text) return;
  await fetch('/messages', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({from, text, channel: currentChannel})
  });
  document.getElementById('msg-input').value = '';
  poll();
}

document.getElementById('msg-input').addEventListener('keydown', e => { if (e.key === 'Enter') sendMsg(); });

renderChannels();
poll();
setInterval(poll, 2000);
</script>
</body></html>"""

    def _serve_ui(self):
        html = render_ui()
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


class _ThreadingServer(ThreadingHTTPServer):
    # One thread per request so a single slow/wedged request can't block others.
    daemon_threads = True  # request threads never block shutdown
    # Larger listen backlog than the default 5 so a burst of clients / a hung
    # request can't fill the accept queue and strand other callers.
    request_queue_size = 128
    allow_reuse_address = True


def render_ui() -> str:
    seeds = json.dumps(seed_channels()).replace("</", "<\\/")
    return RelayHandler._ui_template().replace("__SEED_CHANNELS__", seeds)


def main():
    host = os.environ.get("RELAY_HOST", "0.0.0.0")
    port = int(os.environ.get("RELAY_PORT", "8422"))
    server = _ThreadingServer((host, port), RelayHandler)
    logger.info(f"Coordination Relay listening on {host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
