#!/usr/bin/env python3
"""
Buduje nowy config Xray dla serwera z obecnego (zachowuje dotychczasowych
klientow wejscia VPN, dopoki nie dasz --drop-old).

  python3 xray_config.py /usr/local/etc/xray/config.json > /tmp/xray-new.json
  python3 xray_config.py --drop-old /usr/local/etc/xray/config.json > /tmp/xray-new.json

Wejscia:
  vpn   127.0.0.1:10900  ws <VPN_PATH>          - urzadzenia (dodaje akvpn-api przez API Xray)
  guest 127.0.0.1:10901  ws <GUEST_PATH>        - publiczny UUID gościa, tylko do API logowania
API Xray: 127.0.0.1:10085 (HandlerService - dodawanie/usuwanie, StatsService - ruch).
"""
import argparse
import json
import sys

GUEST_UUID = "@GUEST_UUID@"
GUEST_PATH = "@GUEST_PATH@"
VPN_PATH = "@VPN_PATH@"
VPN_PORT, GUEST_PORT, API_PORT, LOGIN_API_PORT = 10900, 10901, 10085, 10910
# z tuneli nie wolno dojsc do uslug serwera (MySQL z LAMP, Docker, OpenVPN/Amnezia...)
BLOCKED_IP = ["0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
              "172.16.0.0/12", "192.168.0.0/16", "224.0.0.0/3",
              "::/128", "::1/128", "fc00::/7", "fe80::/10", "ff00::/8"]


def ws_inbound(tag, port, path, clients):
    return {"tag": tag, "listen": "127.0.0.1", "port": port, "protocol": "vless",
            "settings": {"clients": clients, "decryption": "none"},
            "streamSettings": {"network": "ws", "wsSettings": {"path": path}}}


def build(old_clients):
    return {
        # bez logu dostepu - nie zapisujemy, kto sie z czym laczy (patrz PRYWATNOSC.txt)
        "log": {"loglevel": "warning", "access": "none"},
        "api": {"tag": "api", "listen": f"127.0.0.1:{API_PORT}",
                "services": ["HandlerService", "StatsService"]},
        "stats": {},
        "policy": {"levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}}},
        # jeden resolver z pamiecia podreczna dla routingu i wyjscia direct - domena
        # nie moze dla routingu wskazywac na publiczny adres, a dla polaczenia na 127.0.0.1
        "dns": {"servers": ["localhost"], "queryStrategy": "UseIP"},
        "inbounds": [
            ws_inbound("vpn", VPN_PORT, VPN_PATH, old_clients),
            ws_inbound("guest", GUEST_PORT, GUEST_PATH,
                       [{"id": GUEST_UUID, "email": "guest@akvpn"}]),
        ],
        "outbounds": [
            # nowsze Xray (finalRules) samo blokuje z wejsc VLESS adresy prywatne;
            # piszemy to jawnie, zeby dzialalo tak samo w kazdej wersji
            {"tag": "direct", "protocol": "freedom",
             "settings": {"finalRules": [{"action": "block", "ip": BLOCKED_IP}]},
             "streamSettings": {"sockopt": {"domainStrategy": "UseIP"}}},
            # jedyny wyjatek: API logowania na tym serwerze
            {"tag": "login-api", "protocol": "freedom",
             "settings": {"finalRules": [{"action": "allow", "network": "tcp",
                                          "ip": ["127.0.0.1/32"], "port": str(LOGIN_API_PORT)},
                                         {"action": "block", "ip": ["0.0.0.0/0", "::/0"]}]}},
            {"tag": "block", "protocol": "blackhole"},
        ],
        "routing": {"domainStrategy": "IPOnDemand", "rules": [
            {"type": "field", "inboundTag": ["guest", "vpn"], "ip": ["127.0.0.1"],
             "port": str(LOGIN_API_PORT), "outboundTag": "login-api"},
            {"type": "field", "inboundTag": ["guest"], "outboundTag": "block"},
            {"type": "field", "domain": ["domain:localhost"], "outboundTag": "block"},
            {"type": "field", "ip": BLOCKED_IP, "outboundTag": "block"},
            # SMTP - zeby nikt nie rozsylal spamu z adresu Mikrusa
            {"type": "field", "port": "25", "outboundTag": "block"},
            {"type": "field", "inboundTag": ["vpn"], "outboundTag": "direct"},
        ]},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("old_config", nargs="?", help="obecny config.json (klienci do zachowania)")
    ap.add_argument("--drop-old", action="store_true", help="nie przenos starych UUID")
    a = ap.parse_args()
    clients = []
    if a.old_config and not a.drop_old:
        with open(a.old_config, encoding="utf-8") as f:
            old = json.load(f)
        for ib in old.get("inbounds", []):
            if ib.get("port") == VPN_PORT:
                # bez "email" - akvpn-api nigdy ich nie usunie, zostaja do --drop-old
                clients = [{"id": c["id"]} for c in ib["settings"]["clients"]]
    json.dump(build(clients), sys.stdout, indent=2)
    sys.stdout.write("\n")
    print(f"Przeniesiono starych klientow: {len(clients)}", file=sys.stderr)


if __name__ == "__main__":
    main()
