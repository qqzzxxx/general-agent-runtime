"""Private HTTPS worker. No CLI-selected policy, shell, cookies, redirects or proxies."""
from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import socket
import ssl
import sys
from urllib.parse import urlsplit

MAX_BYTES = 262144
TRANSLATED_NETWORKS = tuple(ipaddress.ip_network(n) for n in
                           ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32"))


def is_public(address):
    ip = ipaddress.ip_address(address)
    return (ip.is_global and not ip.is_multicast and not ip.is_reserved
            and not getattr(ip, "ipv4_mapped", None)
            and not any(ip in network for network in TRANSLATED_NETWORKS))


def public_addresses(host):
    rows = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not rows or any(not is_public(row[4][0]) for row in rows):
        raise ValueError("destination is not exclusively public unicast IP space")
    return rows


def fetch(url):
    # Parent validates exact sealed URL and syntax; worker repeats syntax checks.
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.port not in (None, 443)
            or parsed.fragment or "\\" in url or not url.isascii()
            or any(ord(c) <= 32 or ord(c) == 127 for c in url)):
        raise ValueError("invalid HTTPS URL")
    rows = public_addresses(parsed.hostname)
    family, socktype, proto, _, address = rows[0]
    conn = http.client.HTTPSConnection(parsed.hostname, timeout=10)
    raw = socket.socket(family, socktype, proto)
    raw.settimeout(10)
    try:
        # Connect to the validated numeric address; never resolve a second time.
        raw.connect(address)
        conn.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=parsed.hostname)
        raw = None
        conn.request("GET", (parsed.path or "/") + ("?" + parsed.query if parsed.query else ""),
                     headers={"Accept": "text/plain, text/html, application/json", "Accept-Encoding": "identity",
                              "User-Agent": "GeneralAgentRuntime/1.4"})
        reply = conn.getresponse()
        if not 200 <= reply.status < 300:
            raise ValueError(f"HTTP {reply.status}; redirects are not followed")
        if reply.getheader("Content-Encoding", "identity").lower() != "identity":
            raise ValueError("compressed responses are unsupported")
        body = reply.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("response exceeds 256 KiB")
        body.decode("utf-8-sig")
        return {"status_code": reply.status, "content_type": reply.getheader("Content-Type", "")[:256],
                "body": base64.b64encode(body).decode("ascii")}
    finally:
        conn.close()
        if raw is not None:
            raw.close()


if __name__ == "__main__":
    try:
        request = json.loads(sys.stdin.buffer.read(4096))
        result = {"ok": True, **fetch(request["url"])}
    except Exception as exc:
        # Never echo remote data or detailed socket/SSL paths into the contract.
        result = {"ok": False, "reason": str(exc) if isinstance(exc, ValueError)
                  and not isinstance(exc, UnicodeError) else type(exc).__name__}
    print(json.dumps(result, separators=(",", ":")))
