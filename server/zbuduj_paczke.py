"""
Sklada paczke na serwer: akvpn-serwer.tar.gz na pulpicie.

  python server\\zbuduj_paczke.py

Wstawia dane gościa z src\\serwer_prywatny.json do configu Xray i skryptu
sprawdzajacego, dolacza certyfikat i KLUCZ PRYWATNY API (server\\tajne\\tls.key) -
paczki nie wysylaj nikomu poza swoim serwerem.
"""
import io
import json
import tarfile
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PREFIX = "akvpn-serwer"


def main():
    priv = json.loads((ROOT / "src" / "serwer_prywatny.json").read_text(encoding="utf-8"))
    subst = {"@GUEST_UUID@": priv["guest_uuid"], "@GUEST_PATH@": priv["guest_path"],
             "@SNI@": priv["sni"], "@VPN_PATH@": priv["vpn_path"]}

    def fill(text):
        for k, v in subst.items():
            text = text.replace(k, v)
        assert "@GUEST_" not in text and "@SNI@" not in text and "@VPN_PATH@" not in text
        return text

    link = (f"vless://{{uuid}}@{priv['host']}:{priv['port']}?encryption=none&security=tls"
            f"&sni={priv['sni']}&alpn=http%2F1.1&type=ws&host={priv['sni']}"
            f"&path={urllib.parse.quote(priv['vpn_path'], safe='')}&ech=1#{{name}}")
    config = {
        "db": "/var/lib/akvpn/akvpn.db",
        "vpn_link_template": link,
        "admin_email": priv["admin_email"],
        "mail_from": f"Obfuskator VPN <{priv['admin_email']}>",
        "mail_cmd": ["/usr/bin/msmtp", "-C", "/etc/akvpn/msmtprc", "-t"],
    }
    apache = (f'ProxyPass "{priv["guest_path"]}" "http://127.0.0.1:10901{priv["guest_path"]}" '
              "upgrade=websocket\n")

    files = {
        "requirements.txt": (HERE / "requirements.txt").read_bytes(),
        "config.json": (json.dumps(config, indent=2, ensure_ascii=False) + "\n").encode(),
        "apache-linia.txt": apache.encode(),
        "tls.crt": (HERE / "tajne" / "tls.crt").read_bytes(),
        "tls.key": (HERE / "tajne" / "tls.key").read_bytes(),
        "deploy/xray_config.py": fill((HERE / "deploy" / "xray_config.py").read_text("utf-8")).encode(),
        "deploy/sprawdz.py": fill((HERE / "deploy" / "sprawdz.py").read_text("utf-8")).encode(),
        "deploy/akvpn-api.service": (HERE / "deploy" / "akvpn-api.service").read_bytes(),
        "deploy/akvpn-admin": (HERE / "deploy" / "akvpn-admin").read_bytes(),
    }
    for f in sorted((HERE / "akvpn").glob("*.py")):
        files[f"akvpn/{f.name}"] = f.read_bytes()

    desktop = Path.home() / "Desktop"
    out = (desktop if desktop.exists() else ROOT) / "akvpn-serwer.tar.gz"
    with tarfile.open(out, "w:gz") as tar:
        for name, data in files.items():
            if name.endswith((".py", ".service", "akvpn-admin", ".txt", ".json")):
                data = data.replace(b"\r\n", b"\n")  # konce linii z Windows psuja skrypty
            ti = tarfile.TarInfo(f"{PREFIX}/{name}")
            ti.size = len(data)
            ti.mtime = int(time.time())
            ti.mode = 0o600 if name == "tls.key" else 0o755 if name.startswith("deploy/") else 0o644
            tar.addfile(ti, io.BytesIO(data))
    print(f"Zapisano: {out}  ({len(files)} plikow)")


if __name__ == "__main__":
    main()
