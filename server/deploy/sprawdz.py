#!/usr/bin/env python3
"""
Sprawdza na serwerze droge, ktora pojdzie aplikacja przed zalogowaniem:
klient Xray z UUID gościa -> wejscie gościa -> API logowania (TLS z certyfikatem
z /etc/akvpn/tls.crt). Dwa warianty:
  1) prosto na 127.0.0.1:10901 (Xray + API + reguly routingu),
  2) przez Apache na 127.0.0.1:80 (ProxyPass sciezki gościa - ten sam w kazdym vhoscie).
Nie sprawdza Cloudflare ani ECH - to zrobi dopiero aplikacja.

  python3 /opt/akvpn/deploy/sprawdz.py
"""
import http.client
import json
import socket
import ssl
import subprocess
import sys
import tempfile
import time

GUEST_UUID = "@GUEST_UUID@"
GUEST_PATH = "@GUEST_PATH@"
SNI = "@SNI@"
XRAY = "/usr/local/bin/xray"
CERT = "/etc/akvpn/tls.crt"


def client(port, via_apache):
    stream = {"network": "ws", "wsSettings": {"path": GUEST_PATH, "host": SNI}}
    return {"log": {"loglevel": "warning"},
            "inbounds": [{"listen": "127.0.0.1", "port": port, "protocol": "dokodemo-door",
                          "settings": {"address": "127.0.0.1", "port": 10910, "network": "tcp"}}],
            "outbounds": [{"protocol": "vless", "settings": {
                "address": "127.0.0.1", "port": 80 if via_apache else 10901,
                "id": GUEST_UUID, "encryption": "none"}, "streamSettings": stream}]}


def ping(port):
    ctx = ssl.create_default_context(cafile=CERT)
    ctx.check_hostname = False
    c = http.client.HTTPSConnection("127.0.0.1", port, timeout=10, context=ctx)
    c.request("GET", "/v1/ping")
    return json.loads(c.getresponse().read())


def wait(port):
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            return
        except OSError:
            time.sleep(0.1)


def main():
    ok = True
    for port, apache, name in ((10879, False, "Xray -> API (127.0.0.1:10901)"),
                               (10878, True, "Apache :80 -> Xray -> API")):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(client(port, apache), f)
        p = subprocess.Popen([XRAY, "run", "-c", f.name], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        try:
            wait(port)
            r = ping(port)
            print(f"OK    {name}: {r}")
        except Exception as e:
            ok = False
            print(f"BLAD  {name}: {e}")
        finally:
            p.kill()
    try:
        socket.create_connection(("127.0.0.1", 10910), 2).close()
        print("OK    API slucha tylko na 127.0.0.1:10910")
    except OSError as e:
        ok = False
        print(f"BLAD  API nie slucha na 127.0.0.1:10910: {e}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
