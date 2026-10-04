"""
Test trybu "Własny serwer" (bez konta), na Windowsie, bez prawdziwego VPS-a:
skrypt instalacyjny z kreatora (skladnia bash, config Xray), normalizacja linkow
vless://, polaczenie przez lokalny "VPS" z configiem z kreatora i przeplyw w aplikacji.

  python server\\tests\\e2e_wlasny_serwer.py      (Python z Pillow - jak do aplikacji)
"""
import base64
import importlib.machinery
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
XRAY = ROOT / "bin" / "xray.exe"
W = Path(tempfile.mkdtemp(prefix="akvpn-own-"))
NOWIN = 0x08000000

loader = importlib.machinery.SourceFileLoader("akapp", str(ROOT / "src" / "obfuskator_vpn.pyw"))
spec = importlib.util.spec_from_loader("akapp", loader)
app = importlib.util.module_from_spec(spec)
loader.exec_module(app)
app.DATA_DIR = W / "data"
app.DATA_DIR.mkdir()
app.LOG_FILE = W / "data" / "app.log"
app.STATE_FILE = W / "data" / "ech_state.json"
app.SESSION_FILE = W / "data" / "session.bin"
app.DEVICE_FILE = W / "data" / "device.json"
app.OWN_FILE = W / "data" / "own_server.bin"

results, procs = [], []
UUID = "6f1d2c3b-4a5e-4f60-8a7b-9c0d1e2f3a4b"
WSPATH = "/abc123def4567890"


def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(("  OK   " if cond else "  BLAD ") + name + (f"  [{extra}]" if extra else ""), flush=True)


def xray_test(cfg) -> bool:
    r = subprocess.run([str(XRAY), "run", "-test", "-c", "stdin:"], input=json.dumps(cfg).encode(),
                       capture_output=True, creationflags=NOWIN)
    return b"Configuration OK" in r.stdout + r.stderr


def spawn(cfg, log):
    f = open(W / log, "w")
    p = subprocess.Popen([str(XRAY), "run", "-c", "stdin:"], stdin=subprocess.PIPE, stdout=f,
                         stderr=subprocess.STDOUT, creationflags=NOWIN)
    p.stdin.write(json.dumps(cfg).encode())
    p.stdin.close()
    procs.append(p)
    return p


def wait_port(port, t=15):
    end = time.time() + t
    while time.time() < end:
        if app.port_open(port):
            return True
        time.sleep(0.2)
    return False


def via_socks(port, host, dport, payload=b"GET / HTTP/1.0\r\n\r\n", t=6):
    try:
        s = app.socks_connect(port, host, dport, t)
    except Exception:
        return False
    try:
        s.settimeout(t)
        s.sendall(payload)
        return bool(s.recv(64))
    except Exception:
        return False
    finally:
        s.close()


def main():
    print("== skrypt instalacyjny z kreatora")
    script = app.own_server_script("mojvpn.bieda.it", UUID, WSPATH)
    check("brak niewypelnionych pol", "@" not in re.sub(r'"\$\(curl[^)]*\)" @ install', "", script)
          .replace("@ install", ""))
    sh = W / "obfuskator-serwer.sh"
    sh.write_text(script, encoding="utf-8", newline="\n")
    bash = shutil.which("bash")
    r = subprocess.run([bash, "-n", str(sh)], capture_output=True, text=True)
    check("bash -n: składnia skryptu", r.returncode == 0, r.stderr.strip()[-200:])
    m = re.search(r"<<'XRAYCFG'\n(.*?)\nXRAYCFG", script, re.S)
    cfg = json.loads(m.group(1))
    check("xray -test: config VPS-a z kreatora", xray_test(cfg))
    check("config: bez logu polaczen", cfg["log"].get("access") == "none")
    check("ProxyPass na sciezke i port Xray",
          f'ProxyPass \\"$WSPATH\\" \\"http://127.0.0.1:$PORT$WSPATH\\" upgrade=websocket' in script
          and f"WSPATH='{WSPATH}'" in script)
    cmd = app.own_install_command(script)
    b64 = re.search(r"echo '([^']+)'", cmd).group(1)
    check("komenda SSH = jedna linia, dekoduje sie do skryptu",
          "\n" not in cmd and base64.b64decode(b64).decode() == script.replace("\r\n", "\n"))

    print("== linki z kreatora i wklejone")
    link = app.own_link("mojvpn.bieda.it", UUID, WSPATH, app.CF_FALLBACK_IP)
    p = app.Profile(link)
    check("link z kreatora: WS/TLS/ECH, ALPN, SNI = domena, adres IP",
          p.ech and p.network == "ws" and p.alpn == ["http/1.1"] and p.sni == "mojvpn.bieda.it"
          and app.is_ip(p.host) and p.path == WSPATH)
    check("adres Cloudflare rozpoznany", app.is_cloudflare("104.21.91.212")
          and not app.is_cloudflare("8.8.8.8"))
    cores = app.Cores(dict(app.DEFAULTS), app.EchService(dict(app.DEFAULTS)))
    pdf = (f"vless://{UUID}@crypto.cloudflare.com:443?encryption=none&security=tls"
           f"&sni=crypto.cloudflare.com&type=ws&host=crypto.cloudflare.com&path=%2Fx#akademik")
    new, notes = app.normalize_own_link(pdf, cores.ech_attempts)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(new).query))
    np_ = app.Profile(new)
    check("link jak w PDF: domena -> IP Cloudflare", app.is_cloudflare(np_.host), np_.host)
    check("link jak w PDF: dopisane ALPN http/1.1", q.get("alpn") == "http/1.1")
    check("link jak w PDF: ECH włączone automatycznie", np_.ech, notes)
    check("nazwa profilu zachowana", np_.name == "akademik")
    same, _ = app.normalize_own_link(link, cores.ech_attempts)
    check("link z IP i ECH zostaje bez zmian (poza kolejnością)", app.Profile(same).link and
          app.Profile(same).host == p.host and app.Profile(same).ech)
    for bad in ("vless://zly@host", "https://cos.pl", "vless://" + UUID + "@h:443?security=xyz"):
        try:
            app.normalize_own_link(bad, cores.ech_attempts)
            check(f"zły link odrzucony: {bad[:30]}", False)
        except ValueError:
            check(f"zły link odrzucony: {bad[:30]}", True)

    print("== połączenie przez lokalny 'VPS' z configiem z kreatora")
    srv = json.loads(json.dumps(cfg))
    srv["inbounds"][0]["port"] = 10950
    spawn(srv, "vps.log")
    s = dict(app.DEFAULTS)
    s.update(socks_port=10918, api_port=10960, ech_direct_port=10956, ech_router_port=10957)
    local = app.Profile(f"vless://{UUID}@127.0.0.1:10950?encryption=none&security=none&type=ws"
                        f"&path={urllib.parse.quote(WSPATH, safe='')}#t")
    spawn(app.build_xray(local, "127.0.0.1", s, "192.168.1.1", 10910), "client.log")
    check("serwer i klient wystartowały", wait_port(10950) and wait_port(10918))
    try:
        _t, status, _ = app.http_via_socks(10918, "www.gstatic.com", "/generate_204", False, 10)
        check("internet przez 'VPS'", status == 204)
    except Exception as e:
        check("internet przez 'VPS'", False, str(e))
    raw = {"log": {"loglevel": "warning"},
           "inbounds": [{"listen": "127.0.0.1", "port": 10970, "protocol": "socks",
                         "settings": {"auth": "noauth"}}],
           "outbounds": [app.vless_outbound(local, "127.0.0.1", s)]}
    spawn(raw, "raw.log")
    wait_port(10970)
    check("VPS: usługi lokalne serwera zablokowane", not via_socks(10970, "127.0.0.1", 10950))
    check("VPS: sieć lokalna zablokowana", not via_socks(10970, "192.168.1.1", 80, t=3))
    bad = app.Profile(f"vless://11111111-2222-4333-8444-555555555555@127.0.0.1:10950?security=none"
                      f"&type=ws&path={urllib.parse.quote(WSPATH, safe='')}#x")
    raw2 = json.loads(json.dumps(raw))
    raw2["inbounds"][0]["port"] = 10971
    raw2["outbounds"] = [app.vless_outbound(bad, "127.0.0.1", s)]
    spawn(raw2, "raw2.log")
    wait_port(10971)
    check("obcy UUID nie ma dostępu", not via_socks(10971, "www.gstatic.com", 80,
                                                     b"GET /generate_204 HTTP/1.0\r\n\r\n"))

    print("== przepływ w aplikacji (bez konta)")
    gui_flow()


def gui_flow():
    import queue as _q
    a = app.App()
    a.s.update(socks_port=10918, api_port=10960, guest_api_port=10961)
    a._init_account()
    a.gui = app.Gui(a)
    connects = []
    a.connect_async = lambda: connects.append(time.time())

    def pump(until, t=30):
        end = time.time() + t
        while time.time() < end:
            try:
                while True:
                    cmd = a.ui.get_nowait()
                    if callable(cmd):
                        cmd()
            except _q.Empty:
                pass
            a.gui.root.update()
            if until():
                return True
            time.sleep(0.05)
        return False

    check("start bez konta i serwera -> wybór", a.session is None and not a.own_active())
    a.gui.auth_view("start")
    a.gui.auth_view("wiz1")
    a.own_wizard("Zła domena!")
    check("kreator: zła domena odrzucona", "subdomenę" in a.gui._auth_status.cget("text"))
    a.own_wizard("https://MojVPN.bieda.it/")
    check("kreator: komenda dla oczyszczonej domeny", a.gui.auth_current == "wiz2"
          and a.own["wizard"]["domain"] == "mojvpn.bieda.it"
          and (app.DATA_DIR / "obfuskator-serwer.sh").exists())
    wiz = dict(a.own["wizard"])
    a.gui.auth_view("wiz1")
    a.own_wizard("mojvpn.bieda.it")
    check("kreator: powtórka = te same UUID i ścieżka", a.own["wizard"] == wiz)
    a.own_wizard_connect()
    check("kreator: Połącz -> okno główne i łączenie",
          pump(lambda: connects and a.gui.auth_current is None))
    pr = a.cores.profile
    check("profil z kreatora w rdzeniach", isinstance(pr, app.Profile) and pr.uuid == wiz["uuid"]
          and pr.path == wiz["path"] and pr.sni == "mojvpn.bieda.it" and pr.ech)
    check("zapisane zaszyfrowane", app.OWN_FILE.exists()
          and b"mojvpn" not in app.OWN_FILE.read_bytes() and app.load_own()["active"])
    a.gui.refresh()
    check("karta: domena zamiast e-maila", a.gui.c.itemcget(a.gui.prof_name, "text") == "mojvpn.bieda.it")
    a.logout_async()
    check("wylogowanie -> ekran wyboru serwera", pump(lambda: a.gui.auth_current == "start"))
    own = app.load_own()
    check("po wylogowaniu: tryb wyłączony, link zapamiętany", not own["active"] and own["link"])
    check("brak profilu po wylogowaniu", not isinstance(a.cores.profile, app.Profile))
    a.gui.auth_view("own")
    pasted = (f"vless://{UUID}@104.21.91.212:443?encryption=none&security=tls&sni=inny.bieda.it"
              "&alpn=http%2F1.1&type=ws&host=inny.bieda.it&path=%2Fq&ech=1#inny")
    connects.clear()
    a.own_connect(pasted)
    check("wklejony link -> połączenie", pump(lambda: connects and not a.auth_busy))
    check("wklejony link aktywny", a.own_active() and a.cores.profile.sni == "inny.bieda.it")
    a.gui.auth_view("own")
    a.own_connect("https://zly")
    check("link nie-vless odrzucony", "vless://" in a.gui._auth_status.cget("text"))
    a.cores.stop_guest()
    a.gui.root.destroy()
    b = app.App()
    b._init_account()
    check("ponowne uruchomienie: tryb własnego serwera wraca", b.own_active()
          and isinstance(b.cores.profile, app.Profile))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        results.append(("wyjatek", False))
    finally:
        for p in procs:
            try:
                p.kill()
            except OSError:
                pass
        ok = sum(1 for _n, r in results if r)
        print(f"\nWynik: {ok}/{len(results)} OK   (logi: {W})")
        sys.exit(0 if ok == len(results) else 1)
