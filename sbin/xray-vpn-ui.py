#!/usr/bin/env python3
"""Minimal VPN control panel — stdlib only."""

from __future__ import annotations

import json
import secrets
import subprocess
import sys
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE = Path("/etc/xray-vpn")
NODES_FILE = BASE / "nodes.json"
INDEX_FILE = BASE / "current_index"
STATE_FILE = BASE / "state.json"
TOKEN_FILE = BASE / "ui.token"
LOG_FILE = Path("/var/log/xray-vpn/manage.log")
MANAGE = Path("/usr/local/sbin/xray-vpn-manage.py")
HOST = "0.0.0.0"
PORT = 8787
SESSIONS: set[str] = set()


def load_token() -> str:
    return TOKEN_FILE.read_text(encoding="utf-8").strip()


def load_nodes() -> list[dict]:
    return json.loads(NODES_FILE.read_text(encoding="utf-8"))


def read_index(n: int) -> int:
    try:
        return int(INDEX_FILE.read_text().strip()) % n
    except (OSError, ValueError):
        return 0


def read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def service_active(name: str) -> bool:
    r = subprocess.run(
        ["systemctl", "is-active", name],
        capture_output=True,
        text=True,
    )
    return r.stdout.strip() == "active"


def run_manage(*args: str) -> tuple[int, str]:
    r = subprocess.run(
        [sys.executable, str(MANAGE), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode, out.strip()


def tail_log(n: int = 40) -> str:
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except OSError:
        return ""


HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VPN</title>
<style>
:root{--bg:#0f1115;--card:#171a21;--line:#2a3140;--txt:#e8ecf4;--muted:#8b95a8;--ok:#3ecf8e;--bad:#ff6b6b;--acc:#5b8cff}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,sans-serif;background:var(--bg);color:var(--txt)}
main{max-width:640px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:1.15rem;font-weight:650;margin:0 0 14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin:0 0 12px}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.muted{color:var(--muted);font-size:.9rem}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:.8rem;border:1px solid var(--line)}
.ok{color:var(--ok);border-color:#245c45}.bad{color:var(--bad);border-color:#6b3030}
button,.btn{appearance:none;border:1px solid var(--line);background:#212634;color:var(--txt);border-radius:8px;padding:8px 12px;cursor:pointer;font:inherit}
button:hover{border-color:var(--acc)}button.primary{background:#243356;border-color:#3a5aa0}
button:disabled{opacity:.5;cursor:wait}
table{width:100%;border-collapse:collapse;font-size:.92rem}
td,th{padding:8px 6px;border-bottom:1px solid var(--line);text-align:left}
tr.active td{background:#1d2433}
pre{margin:0;white-space:pre-wrap;word-break:break-word;font:12px/1.4 ui-monospace,monospace;color:var(--muted);max-height:240px;overflow:auto}
.login{max-width:360px;margin:12vh auto;padding:20px}
input{width:100%;padding:10px 12px;border-radius:8px;border:1px solid var(--line);background:#0c0e13;color:var(--txt);font:inherit;margin:8px 0 12px}
.err{color:var(--bad);font-size:.9rem;margin-top:8px}
</style>
</head>
<body>
<main id="app"></main>
<script>
async function api(path, opts){
  const r = await fetch(path, Object.assign({credentials:'same-origin'}, opts||{}));
  if(r.status===401){ location.href='/login'; return null; }
  const j = await r.json();
  if(!r.ok) throw new Error(j.error||r.statusText);
  return j;
}
function el(html){ const d=document.createElement('div'); d.innerHTML=html; return d.firstElementChild; }
async function render(){
  const app=document.getElementById('app');
  app.innerHTML='<p class="muted">загрузка…</p>';
  const s=await api('/api/status');
  if(!s) return;
  const nodes=s.nodes.map((n,i)=>{
    const on=i===s.index;
    return `<tr class="${on?'active':''}">
      <td>${on?'●':'○'} ${n.name}</td>
      <td class="muted">${n.address}:${n.port}</td>
      <td><button data-i="${i}" ${on?'disabled':''}>выбрать</button></td>
    </tr>`;
  }).join('');
  app.innerHTML=`
    <h1>VPN</h1>
    <div class="card">
      <div class="row" style="justify-content:space-between">
        <div>
          <div><strong>${s.name||'—'}</strong>
            <span class="pill ${s.healthy?'ok':'bad'}">${s.healthy?'online':'offline'}</span>
          </div>
          <div class="muted">exit IP: ${s.exit_ip||'—'} · ${s.updated_at||''}</div>
          <div class="muted">socks 127.0.0.1:10808 · http 127.0.0.1:10809</div>
        </div>
        <div class="muted">xray: ${s.xray?'on':'off'} · timer: ${s.timer?'on':'off'}</div>
      </div>
      <div class="row" style="margin-top:12px">
        <button class="primary" id="check">проверить</button>
        <button id="rotate">следующий</button>
        <button id="force">force rotate</button>
        <button id="refresh">обновить</button>
      </div>
      <p id="msg" class="muted" style="margin:10px 0 0"></p>
    </div>
    <div class="card">
      <table><thead><tr><th>узел</th><th>адрес</th><th></th></tr></thead>
      <tbody>${nodes}</tbody></table>
    </div>
    <div class="card"><div class="muted" style="margin-bottom:6px">лог</div><pre id="log">${escapeHtml(s.log||'')}</pre></div>`;
  const msg=document.getElementById('msg');
  async function act(path, body){
    [...app.querySelectorAll('button')].forEach(b=>b.disabled=true);
    msg.textContent='выполняется…';
    try{
      const j=await api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
      msg.textContent=j.message||'ok';
      await render();
    }catch(e){ msg.textContent=e.message; [...app.querySelectorAll('button')].forEach(b=>b.disabled=false); }
  }
  document.getElementById('check').onclick=()=>act('/api/check');
  document.getElementById('rotate').onclick=()=>act('/api/rotate');
  document.getElementById('force').onclick=()=>act('/api/force-rotate');
  document.getElementById('refresh').onclick=()=>render();
  app.querySelectorAll('button[data-i]').forEach(b=>b.onclick=()=>act('/api/apply',{index:+b.dataset.i}));
}
function escapeHtml(t){ return t.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
async function loginPage(err){
  document.getElementById('app').innerHTML=`
    <div class="card login">
      <h1>VPN · вход</h1>
      <form id="f">
        <label class="muted">токен</label>
        <input name="token" type="password" autocomplete="current-password" required>
        <button class="primary" style="width:100%">войти</button>
      </form>
      ${err?`<p class="err">${err}</p>`:''}
    </div>`;
  document.getElementById('f').onsubmit=async e=>{
    e.preventDefault();
    const token=new FormData(e.target).get('token');
    const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token}),credentials:'same-origin'});
    const j=await r.json();
    if(r.ok){ location.href='/'; } else loginPage(j.error||'ошибка');
  };
}
if(location.pathname==='/login') loginPage(); else render();
</script>
</body>
</html>
"""

LOGIN_ONLY = True  # unused marker


class Handler(BaseHTTPRequestHandler):
    server_version = "xray-vpn-ui/1.0"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _token_ok(self) -> bool:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        sid = cookie.get("vpn_session")
        return bool(sid and sid.value in SESSIONS)

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, code: int, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/login"):
            self._html(200, HTML)
            return
        if path == "/api/status":
            if not self._token_ok():
                self._json(401, {"error": "unauthorized"})
                return
            nodes = load_nodes()
            idx = read_index(len(nodes))
            state = read_state()
            node = nodes[idx] if nodes else {}
            healthy = bool(state.get("ok"))
            # cheap: trust last state; UI "check" refreshes
            self._json(
                200,
                {
                    "index": idx,
                    "name": node.get("name"),
                    "address": node.get("address"),
                    "healthy": healthy,
                    "exit_ip": state.get("detail") if healthy else None,
                    "updated_at": state.get("updated_at"),
                    "xray": service_active("xray-vpn.service"),
                    "timer": service_active("xray-vpn-health.timer"),
                    "nodes": [
                        {"name": n["name"], "address": n["address"], "port": n["port"]}
                        for n in nodes
                    ],
                    "log": tail_log(),
                },
            )
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        data = self._read_json()

        if path == "/api/login":
            token = str(data.get("token") or "")
            if secrets.compare_digest(token, load_token()):
                sid = secrets.token_urlsafe(24)
                SESSIONS.add(sid)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header(
                    "Set-Cookie",
                    f"vpn_session={sid}; Path=/; HttpOnly; SameSite=Strict; Max-Age=604800",
                )
                body = b'{"ok":true}'
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._json(401, {"error": "неверный токен"})
            return

        if not self._token_ok():
            self._json(401, {"error": "unauthorized"})
            return

        if path == "/api/check":
            code, out = run_manage("status")
            # status prints json to stdout — also refresh via rotate no-op health
            code2, out2 = run_manage("rotate")
            state = read_state()
            self._json(
                200,
                {
                    "ok": code2 == 0,
                    "message": f"healthy={state.get('ok')} ip={state.get('detail')}",
                    "log": out2[-500:],
                },
            )
            return

        if path == "/api/rotate":
            code, out = run_manage("rotate")
            state = read_state()
            self._json(
                200 if code == 0 else 500,
                {
                    "ok": code == 0,
                    "message": f"{state.get('name')}: {state.get('detail')}",
                    "log": out[-500:],
                },
            )
            return

        if path == "/api/force-rotate":
            code, out = run_manage("force-rotate")
            state = read_state()
            self._json(
                200 if code == 0 else 500,
                {
                    "ok": code == 0,
                    "message": f"{state.get('name')}: {state.get('detail')}",
                    "log": out[-500:],
                },
            )
            return

        if path == "/api/apply":
            try:
                idx = int(data.get("index"))
            except (TypeError, ValueError):
                self._json(400, {"error": "bad index"})
                return
            code, out = run_manage("apply", str(idx))
            state = read_state()
            self._json(
                200 if code == 0 else 500,
                {
                    "ok": code == 0,
                    "message": f"→ {state.get('name')}: {state.get('detail')}",
                    "log": out[-500:],
                },
            )
            return

        self._json(404, {"error": "not found"})


def main() -> None:
    if not TOKEN_FILE.exists():
        TOKEN_FILE.write_text(secrets.token_hex(16) + "\n")
        TOKEN_FILE.chmod(0o600)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"xray-vpn-ui on http://{HOST}:{PORT}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
