# xray-codex-proxy

Local **Xray VLESS Reality** client for a Linux VPS: rotating exit nodes, loopback HTTP/SOCKS proxies, and automatic proxy injection so **VS Code Remote / Cursor** and **OpenAI Codex** leave through the tunnel instead of the datacenter IP.

Xray is **not** a system-wide TUN VPN. It listens on localhost; apps use it only if `HTTP(S)_PROXY` (or SOCKS) is set. That is the usual failure mode: the daemon is healthy, Codex still OAuths from a blocked country.

## GitHub description

```
Loopback Xray VLESS client: rotating nodes, local HTTP/SOCKS, and proxy injection for VS Code Remote and OpenAI Codex.
```

**Topics:** `xray` `vless` `reality` `proxy` `vscode-remote` `openai-codex` `linux` `systemd`

## What it does

| Piece | Role |
|-------|------|
| `xray-vpn.service` | Xray client. SOCKS `127.0.0.1:10808`, HTTP `127.0.0.1:10809` |
| `xray-vpn-manage.py` | Apply a node, health-check via SOCKS (`api.ipify.org`), rotate on failure |
| `xray-vpn-health.timer` | Hourly keep-alive / failover |
| `xray-vpn-ui.py` | Token-gated panel to pick a node (bind `0.0.0.0:8787` — firewall it) |
| `proxy.env` + `server-env-setup` | Inject `HTTP_PROXY` into shells, PAM, VS Code Remote, Cursor Remote |

Codex on the VPS talks to `https://auth.openai.com/oauth/token`. Without the proxy that call returns `403 unsupported_country_region_territory`. Through Xray it reaches OpenAI (a dummy POST then fails with `missing client_id`, which is expected).

## Layout on the host

```
/etc/xray-vpn/          nodes.json, config.json, proxy.env, ui.token   (secrets — not in git)
/usr/local/sbin/        xray-vpn-manage.py, xray-vpn-ui.py
/etc/systemd/system/    xray-vpn*.service, xray-vpn-health.timer
/etc/profile.d/         xray-vpn.sh
~/.vscode-server/server-env-setup
~/.cursor-server/server-env-setup
```

## Configure nodes

Copy `templates/nodes.json.example` → `/etc/xray-vpn/nodes.json` (`chmod 600`). Fill in **your** VLESS Reality endpoints. Never commit that file.

```json
{
  "name": "Exit-1",
  "address": "YOUR_HOST",
  "port": 8444,
  "uuid": "YOUR_UUID",
  "pbk": "YOUR_REALITY_PUBLIC_KEY",
  "sid": "",
  "sni": "github.com",
  "fp": "chrome",
  "serviceName": "GunService"
}
```

## Proxy injection (Codex)

1. Install `templates/proxy.env` as `/etc/xray-vpn/proxy.env`.
2. Install profile / `environment.d` / VS Code + Cursor snippets from `templates/`.
3. **Reload the remote window** (or reconnect SSH). Already-running `codex app-server` processes keep the old environment until they restart.

`NO_PROXY` must include `127.0.0.1,localhost,::1` so the editor’s local sockets are not sent into Xray.

## Commands

```bash
python3 /usr/local/sbin/xray-vpn-manage.py status
python3 /usr/local/sbin/xray-vpn-manage.py apply 0
python3 /usr/local/sbin/xray-vpn-manage.py rotate
python3 /usr/local/sbin/xray-vpn-manage.py force-rotate
```

Quick check that Codex’s OAuth host is no longer geo-blocked:

```bash
curl -sS -x http://127.0.0.1:10809 -o /dev/null -w "%{http_code}\n" \
  -X POST https://auth.openai.com/oauth/token \
  -H 'Content-Type: application/json' -d '{"grant_type":"authorization_code"}'
# 400 = tunnel OK (missing client_id). 403 + unsupported_country_* = still the VPS IP.
```

## Not in this repo

- Live `nodes.json`, `config.json`, `ui.token`, exit IPs
- A TUN/iptables full-machine VPN
- An Xray **server** (this is the client side only)

## License

MIT
