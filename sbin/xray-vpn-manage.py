#!/usr/bin/env python3
"""VLESS Reality client with round-robin health checks."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError, HTTPError
from urllib.request import ProxyHandler, Request, build_opener

BASE = Path("/etc/xray-vpn")
NODES_FILE = BASE / "nodes.json"
INDEX_FILE = BASE / "current_index"
STATE_FILE = BASE / "state.json"
CONFIG_FILE = BASE / "config.json"
LOG_FILE = Path("/var/log/xray-vpn/manage.log")
SOCKS_PORT = 10808
HTTP_PORT = 10809
SERVICE = "xray-vpn.service"
HEALTH_URL = "https://api.ipify.org"
CONNECT_TIMEOUT = 12
HEALTH_RETRIES = 2


def log(msg: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S") + f" {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def load_nodes() -> list[dict]:
    with NODES_FILE.open(encoding="utf-8") as fh:
        nodes = json.load(fh)
    if not nodes:
        raise SystemExit("nodes.json is empty")
    return nodes


def read_index(count: int) -> int:
    try:
        idx = int(INDEX_FILE.read_text().strip())
    except (OSError, ValueError):
        idx = 0
    return idx % count


def write_index(idx: int) -> None:
    INDEX_FILE.write_text(str(idx) + "\n")


def write_state(node: dict, idx: int, ok: bool, detail: str = "") -> None:
    STATE_FILE.write_text(
        json.dumps(
            {
                "index": idx,
                "name": node.get("name"),
                "address": node.get("address"),
                "ok": ok,
                "detail": detail,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


def build_config(node: dict) -> dict:
    reality = {
        "show": False,
        "fingerprint": node.get("fp") or "chrome",
        "serverName": node.get("sni") or "github.com",
        "publicKey": node["pbk"],
        "shortId": node.get("sid") or "",
        "spiderX": "",
    }
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "socks-in",
                "listen": "127.0.0.1",
                "port": SOCKS_PORT,
                "protocol": "socks",
                "settings": {"udp": True},
                "sniffing": {
                    "enabled": True,
                    "destOverride": ["http", "tls", "quic"],
                },
            },
            {
                "tag": "http-in",
                "listen": "127.0.0.1",
                "port": HTTP_PORT,
                "protocol": "http",
                "settings": {"allowTransparent": False},
                "sniffing": {
                    "enabled": True,
                    "destOverride": ["http", "tls", "quic"],
                },
            },
        ],
        "outbounds": [
            {
                "tag": "proxy",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": node["address"],
                            "port": int(node["port"]),
                            "users": [
                                {
                                    "id": node["uuid"],
                                    "encryption": "none",
                                    "flow": "",
                                }
                            ],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "grpc",
                    "security": "reality",
                    "realitySettings": reality,
                    "grpcSettings": {
                        "serviceName": node.get("serviceName") or "GunService",
                        "multiMode": False,
                    },
                },
            },
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ],
        "routing": {
            "domainStrategy": "AsIs",
            "rules": [
                {
                    "type": "field",
                    "outboundTag": "direct",
                    "ip": ["geoip:private"],
                }
            ],
        },
    }


def apply_node(node: dict, idx: int) -> None:
    config = build_config(node)
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")
    write_index(idx)
    log(f"applied node[{idx}] {node['name']} ({node['address']}:{node['port']})")


def restart_service() -> None:
    subprocess.run(
        ["systemctl", "restart", SERVICE],
        check=True,
        capture_output=True,
        text=True,
    )
    # Wait for inbound ports
    deadline = time.time() + 8
    while time.time() < deadline:
        try:
            out = subprocess.check_output(["ss", "-ltn"], text=True)
            if f"127.0.0.1:{SOCKS_PORT}" in out:
                return
        except subprocess.CalledProcessError:
            pass
        time.sleep(0.3)
    log("warning: socks port not seen yet, continuing")


def health_check() -> tuple[bool, str]:
    proxy = f"socks5h://127.0.0.1:{SOCKS_PORT}"
    opener = build_opener(ProxyHandler({"http": proxy, "https": proxy}))
    last = "no attempts"
    for attempt in range(1, HEALTH_RETRIES + 1):
        try:
            req = Request(HEALTH_URL, headers={"User-Agent": "xray-vpn-health/1.0"})
            with opener.open(req, timeout=CONNECT_TIMEOUT) as resp:
                body = resp.read().decode("utf-8", errors="replace").strip()
                if resp.status == 200 and body:
                    return True, body
                last = f"http {resp.status} body={body!r}"
        except (URLError, HTTPError, TimeoutError, OSError) as exc:
            last = str(exc)
            log(f"health attempt {attempt}/{HEALTH_RETRIES} failed: {last}")
            time.sleep(1)
    return False, last


def ensure_service_exists() -> None:
    unit = Path(f"/etc/systemd/system/{SERVICE}")
    if not unit.exists():
        raise SystemExit(f"missing systemd unit {unit}")


def cmd_status() -> int:
    nodes = load_nodes()
    idx = read_index(len(nodes))
    node = nodes[idx]
    ok, detail = health_check()
    print(
        json.dumps(
            {
                "index": idx,
                "name": node["name"],
                "address": node["address"],
                "healthy": ok,
                "detail": detail,
                "socks": f"127.0.0.1:{SOCKS_PORT}",
                "http": f"127.0.0.1:{HTTP_PORT}",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if ok else 1


def cmd_apply(idx: int | None = None) -> int:
    nodes = load_nodes()
    if idx is None:
        idx = read_index(len(nodes))
    idx %= len(nodes)
    apply_node(nodes[idx], idx)
    restart_service()
    ok, detail = health_check()
    write_state(nodes[idx], idx, ok, detail)
    log(f"health={'OK' if ok else 'FAIL'} detail={detail}")
    return 0 if ok else 1


def cmd_rotate(force: bool = False) -> int:
    """Keep current node if healthy; otherwise walk keys in a circle."""
    ensure_service_exists()
    nodes = load_nodes()
    n = len(nodes)
    start = read_index(n)

    # Ensure service is running with current config
    if not CONFIG_FILE.exists():
        apply_node(nodes[start], start)
        restart_service()
    else:
        # Make sure daemon is up
        subprocess.run(["systemctl", "start", SERVICE], check=False)

    if not force:
        ok, detail = health_check()
        if ok:
            write_state(nodes[start], start, True, detail)
            log(f"node[{start}] {nodes[start]['name']} healthy ip={detail}")
            return 0
        log(f"node[{start}] {nodes[start]['name']} unhealthy: {detail}")

    for step in range(n):
        # unhealthy current -> start from next; force -> start from current
        idx = (start + step) % n if force else (start + 1 + step) % n
        node = nodes[idx]
        apply_node(node, idx)
        try:
            restart_service()
        except subprocess.CalledProcessError as exc:
            log(f"restart failed for {node['name']}: {exc.stderr}")
            continue
        ok, detail = health_check()
        write_state(node, idx, ok, detail)
        if ok:
            log(f"switched to node[{idx}] {node['name']} ip={detail}")
            return 0
        log(f"node[{idx}] {node['name']} still unhealthy: {detail}")

    log("ERROR: all nodes failed health check")
    return 2


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: xray-vpn-manage.py {status|apply|rotate|force-rotate}")
        return 2
    cmd = sys.argv[1]
    if cmd == "status":
        return cmd_status()
    if cmd == "apply":
        idx = int(sys.argv[2]) if len(sys.argv) > 2 else None
        return cmd_apply(idx)
    if cmd == "rotate":
        return cmd_rotate(force=False)
    if cmd == "force-rotate":
        return cmd_rotate(force=True)
    print(f"unknown command: {cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
