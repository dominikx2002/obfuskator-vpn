"""Ustawienia z /etc/akvpn/config.json (sciezke zmienia zmienna AKVPN_CONFIG)."""
import json
import os

DEFAULTS = {
    "db": "/var/lib/akvpn/akvpn.db",
    "listen_host": "127.0.0.1",
    "listen_port": 10910,
    # TLS miedzy aplikacja a API (certyfikat przypiety w aplikacji) - Cloudflare
    # rozszyfrowuje WebSocket, wiec bez tego widzialby hasla wewnatrz strumienia VLESS
    "tls_cert": "/etc/akvpn/tls.crt",
    "tls_key": "/etc/akvpn/tls.key",
    # Xray
    "xray_bin": "/usr/local/bin/xray",
    "xray_api": "127.0.0.1:10085",
    "vpn_inbound_tag": "vpn",
    # link wydawany zaakceptowanym urzadzeniom; {uuid} i {name} wstawia serwer
    "vpn_link_template": "",
    "device_email_domain": "akvpn",       # w Xray urzadzenie to "dev12@akvpn"
    # poczta
    "admin_email": "",
    "mail_cmd": ["/usr/bin/msmtp", "-t"],
    "mail_from": "",                      # puste = msmtp wstawi "from" z konfiguracji
    "mail_subject_prefix": "Obfuskator VPN",
    # zasady
    "device_limit": 3,
    "password_min": 8,
    "code_ttl_min": 15,
    "code_attempts": 5,
    "unverified_ttl_h": 24,
    "sync_interval_s": 30,
    "stats_interval_s": 60,
    # limity (wszyscy przychodza z 127.0.0.1, wiec liczymy na e-mail i globalnie)
    "limit_login_fail": [5, 900],         # nieudane logowania na konto / okno (s)
    "limit_login_global": [30, 60],
    "limit_register_global": [10, 3600],
    "limit_code_global": [30, 60],        # proby wpisania kodow
    "limit_mail_per_email": [3, 3600],
    "limit_mail_global": [200, 86400],
}


def load(path: str | None = None) -> dict:
    path = path or os.environ.get("AKVPN_CONFIG", "/etc/akvpn/config.json")
    cfg = dict(DEFAULTS)
    with open(path, encoding="utf-8") as f:
        cfg.update(json.load(f))
    if "{uuid}" not in cfg["vpn_link_template"]:
        raise ValueError("config: vpn_link_template musi zawierac {uuid}")
    return cfg
