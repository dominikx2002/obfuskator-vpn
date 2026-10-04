"""
Test end-to-end na Windowsie, bez Mikrusa: lokalny "serwer" Xray (config z
deploy/xray_config.py, wejscia WS bez TLS - zamiast Apache i Cloudflare), API kont
(TLS z prawdziwym certyfikatem), CLI admina z prawdziwym `xray api` i klient
z kodu aplikacji (configi z build_guest_xray / build_xray).

  set AKVPN_VENV_PY=...\\venv\\Scripts\\python.exe   (fastapi, uvicorn, argon2-cffi)
  python server\\tests\\e2e_lokalny.py              (Python z Pillow - jak do aplikacji)
"""
import email
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.parse
from email import policy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
XRAY = ROOT / "bin" / "xray.exe"
SBOX = ROOT / "bin" / "sing-box.exe"
VENV_PY = os.environ.get("AKVPN_VENV_PY", sys.executable)
W = Path(tempfile.mkdtemp(prefix="akvpn-e2e-"))
NOWIN = 0x08000000

sys.path.insert(0, str(ROOT / "src"))  # _wbudowane.py - sprawdzamy odszyfrowanie
loader = importlib.machinery.SourceFileLoader("akapp", str(ROOT / "src" / "obfuskator_vpn.pyw"))
spec = importlib.util.spec_from_loader("akapp", loader)
app = importlib.util.module_from_spec(spec)
loader.exec_module(app)
# aplikacja nie moze pisac do prawdziwego folderu data
app.DATA_DIR = W / "data"
app.DATA_DIR.mkdir()
app.LOG_FILE = W / "data" / "app.log"
app.STATE_FILE = W / "data" / "ech_state.json"
app.SESSION_FILE = W / "data" / "session.bin"
app.DEVICE_FILE = W / "data" / "device.json"

results = []
procs = []


def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(("  OK   " if cond else "  BLAD ") + name + (f"  [{extra}]" if extra else ""), flush=True)


def wait_port(port, t=15):
    end = time.time() + t
    while time.time() < end:
        if app.port_open(port):
            return True
        time.sleep(0.2)
    return False


def spawn(args, log, stdin_cfg=None, **kw):
    f = open(W / log, "w")
    p = subprocess.Popen(args, stdout=f, stderr=subprocess.STDOUT, creationflags=NOWIN,
                         stdin=subprocess.PIPE if stdin_cfg else None, **kw)
    if stdin_cfg:
        p.stdin.write(json.dumps(stdin_cfg).encode())
        p.stdin.close()
    procs.append(p)
    return p


def xray_test(cfg) -> bool:
    r = subprocess.run([str(XRAY), "run", "-test", "-c", "stdin:"], input=json.dumps(cfg).encode(),
                       capture_output=True, creationflags=NOWIN)
    return b"Configuration OK" in r.stdout + r.stderr


def admin(*args):
    r = subprocess.run([VENV_PY, "-m", "akvpn.admin", *args], cwd=SERVER, capture_output=True,
                       text=True, env={**os.environ, "AKVPN_CONFIG": str(W / "akvpn.json"),
                                       "PYTHONIOENCODING": "utf-8"}, encoding="utf-8")
    return r.returncode, r.stdout + r.stderr


def mails():
    f = W / "mail.txt"
    if not f.exists():
        return []
    out = []
    for raw in f.read_bytes().split(b"\n=====\n"):
        if raw.strip():
            m = email.message_from_bytes(raw, policy=policy.default)
            out.append((m["To"], str(m["Subject"]), m.get_content()))
    return out


def last_code(to):
    import re
    for t, _s, body in reversed(mails()):
        if t == to:
            return re.search(r"\b(\d{6})\b", body).group(1)


def raw_client(port, prof):
    """Klient bez zadnych regul (socks -> vless) - do sprawdzania regul NA SERWERZE."""
    return {"log": {"loglevel": "warning"},
            "inbounds": [{"listen": "127.0.0.1", "port": port, "protocol": "socks",
                          "settings": {"auth": "noauth"}}],
            "outbounds": [app.vless_outbound(prof, "127.0.0.1", app.DEFAULTS)]}


def via_socks(port, host, dport, payload=b"GET / HTTP/1.0\r\n\r\n", t=6):
    """True, gdy przez proxy przyszla jakakolwiek odpowiedz."""
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


def test_settings():
    """Porty klienta inne niz domyslne - prawdziwa aplikacja moze dzialac obok testu."""
    s = dict(app.DEFAULTS)
    s.update(socks_port=10918, api_port=10960, guest_api_port=10961, ech_direct_port=10956,
             ech_router_port=10957, clash_port=10924)
    return s


def main():
    priv = json.loads((ROOT / "src" / "serwer_prywatny.json").read_text())
    s = test_settings()

    print("== dane wbudowane")
    emb = app.embedded()
    check("odszyfrowanie _wbudowane.py", emb["guest_uuid"] == priv["guest_uuid"]
          and "BEGIN CERTIFICATE" in emb["api_cert"] and emb["api_port"] == 10910)
    import _wbudowane
    blob = "".join(_wbudowane.BLOB)
    check("w exe brak jawnej domeny/sciezki/UUID", all(x not in blob for x in (
        priv["sni"], priv["guest_path"], priv["guest_uuid"])))

    print("== configi klienta (prawdziwy serwer: Cloudflare + TLS + ECH)")
    real_guest = app.guest_profile(emb)
    check("profil gościa: WS/TLS/ECH", real_guest.ech and real_guest.network == "ws"
          and real_guest.path == priv["guest_path"])
    check("xray -test: config gościa", xray_test(app.build_guest_xray(real_guest, priv["host"], s, 10910)))
    vp = urllib.parse.quote(priv["vpn_path"], safe="")
    tmpl = (f"vless://{{uuid}}@{priv['host']}:443?encryption=none&security=tls&sni={priv['sni']}"
            f"&alpn=http%2F1.1&type=ws&host={priv['sni']}&path={vp}&ech=1#{{name}}")
    real_vpn = app.Profile(tmpl.replace("{uuid}", "11111111-2222-4333-8444-555555555555")
                           .replace("{name}", "Obfuskator%20VPN"))
    xcfg = app.build_xray(real_vpn, real_vpn.host, s, "192.168.50.1", 10910)
    check("xray -test: config VPN", xray_test(xcfg))
    check("config VPN: wejscie api -> tunel (pierwsza regula)",
          xcfg["routing"]["rules"][0] == {"type": "field", "inboundTag": ["api"], "outboundTag": "proxy"})
    sb = app.build_singbox(real_vpn, real_vpn.host, s, "192.168.50.1", XRAY, SBOX)
    (W / "singbox.json").write_text(json.dumps(sb))
    r = subprocess.run([str(SBOX), "check", "-c", str(W / "singbox.json")], capture_output=True,
                       text=True, creationflags=NOWIN)
    check("sing-box check: config TUN", r.returncode == 0, (r.stdout + r.stderr).strip()[-200:])

    print("== DPAPI i urzadzenie")
    sess = {"email": "a@b.pl", "token": "t" * 40, "status": "active", "profile": None, "device": None}
    app.save_session(sess)
    raw = app.SESSION_FILE.read_bytes()
    check("sesja zaszyfrowana na dysku", b"a@b.pl" not in raw and b"tttt" not in raw)
    check("sesja odczytana z DPAPI", app.load_session() == sess)
    app.save_session(None)
    check("sesja usunieta", not app.SESSION_FILE.exists() and app.load_session() is None)
    d1 = app.device_id()
    check("staly identyfikator urzadzenia", d1 == app.device_id() and len(d1) >= 16)

    print("== start serwera (Xray + API)")
    old_uuid = "99999999-8888-4777-8666-555555555555"
    (W / "old.json").write_text(json.dumps({"inbounds": [{"port": 10900, "settings": {
        "clients": [{"id": old_uuid}]}}]}))
    out = subprocess.run([sys.executable, str(SERVER / "deploy" / "xray_config.py"), str(W / "old.json")],
                         capture_output=True, text=True).stdout
    out = (out.replace("@GUEST_UUID@", priv["guest_uuid"]).replace("@GUEST_PATH@", priv["guest_path"])
           .replace("@VPN_PATH@", priv["vpn_path"]))
    (W / "xray-server.json").write_text(out)
    srv_cfg = json.loads(out)
    check("xray -test: config serwera", xray_test(srv_cfg))
    xsrv = spawn([str(XRAY), "run", "-c", str(W / "xray-server.json")], "xray-server.log")
    (W / "akvpn.json").write_text(json.dumps({
        "db": str(W / "akvpn.db"), "xray_bin": str(XRAY),
        "vpn_link_template": "vless://{uuid}@127.0.0.1:10900?encryption=none&security=none&type=ws"
                             "&path=" + urllib.parse.quote(priv["vpn_path"], safe="") + "#{name}",
        "tls_cert": str(SERVER / "tajne" / "tls.crt"), "tls_key": str(SERVER / "tajne" / "tls.key"),
        "mail_cmd": [VENV_PY, str(SERVER / "tests" / "fake_msmtp.py"), str(W / "mail.txt")],
        "admin_email": "admin@test.pl", "sync_interval_s": 2, "stats_interval_s": 2}))
    spawn([VENV_PY, "-m", "akvpn.api"], "api.log", cwd=SERVER,
          env={**os.environ, "AKVPN_CONFIG": str(W / "akvpn.json")})
    check("serwer Xray slucha (10900/10901/10085)", all(wait_port(p) for p in (10900, 10901, 10085)))
    check("API slucha (10910)", wait_port(10910, 30))

    print("== wejscie gościa (Cores.start_guest)")
    ech = app.EchService(s)
    cores = app.Cores(s, ech)
    cores.remote_api_port = 10910
    local_guest = app.Profile(f"vless://{priv['guest_uuid']}@127.0.0.1:10901?encryption=none"
                              f"&security=none&type=ws&path={priv['guest_path']}#g")
    cores.start_guest(local_guest)
    check("xray gościa wystartowal", cores.guest_running() and app.port_open(s["guest_api_port"]))
    cert = emb["api_cert"]

    def g(method, path, body=None, token=None):
        return app.api_call(s["guest_api_port"], cert, method, path, body, token)

    check("ping przez gościa (TLS z przypietym cert.)", g("GET", "/v1/ping") == {"ok": True})
    # zly certyfikat = brak polaczenia (ochrona przed podsluchem w Cloudflare)
    other = subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt",
                            "ec_paramgen_curve:P-256", "-nodes", "-days", "1", "-subj", "/CN=x",
                            "-keyout", str(W / "k.pem"), "-out", "-"], capture_output=True, text=True,
                           env={**os.environ, "MSYS_NO_PATHCONV": "1"}).stdout
    try:
        app.api_call(s["guest_api_port"], other, "GET", "/v1/ping")
        check("obcy certyfikat odrzucony", False)
    except app.NetError as e:
        check("obcy certyfikat odrzucony", "certyfikat" in str(e), str(e)[:80])

    E = "ania@example.com"
    print("== rejestracja / logowanie przez gościa")
    check("rejestracja", g("POST", "/v1/register", {"email": E, "password": "haslo1234"})
          == {"status": "unverified"})
    code = last_code(E)
    check("mail z kodem (UTF-8)", code and any("kod potwierdzenia" in sj for _t, sj, _b in mails()))
    try:
        g("POST", "/v1/login", {"email": E, "password": "haslo1234", "device_id": d1,
                                "device_name": "TEST-PC"})
        check("logowanie przed potwierdzeniem odrzucone", False)
    except app.ApiError as e:
        check("logowanie przed potwierdzeniem odrzucone", e.code == "unverified")
    check("kod potwierdzony", g("POST", "/v1/verify", {"email": E, "code": code})["status"] == "pending")
    check("mail do admina", any(t == "admin@test.pl" and f"approve {E}" in b for t, _s, b in mails()))
    r = g("POST", "/v1/login", {"email": E, "password": "haslo1234", "device_id": d1,
                                "device_name": "TEST-PC"})
    tok = r["token"]
    check("logowanie: konto czeka, brak profilu", r["status"] == "pending" and r["profile"] is None)
    try:
        g("POST", "/v1/login", {"email": E, "password": "zle-haslo", "device_id": d1})
        check("zle haslo odrzucone", False)
    except app.ApiError as e:
        check("zle haslo odrzucone", e.status == 401 and "hasło" in str(e))

    print("== akceptacja (CLI + prawdziwe xray api adu)")
    rc, out = admin("pending")
    check("akvpn-admin pending", rc == 0 and E in out, out.strip().splitlines()[-1] if out else "")
    rc, out = admin("approve", E)
    check("akvpn-admin approve", rc == 0, out.strip())
    rc, out = admin("xray")
    check("urzadzenie w Xray", "dev1@akvpn" in out)
    me = g("GET", "/v1/me", token=tok)
    check("status aktywny + profil", me["status"] == "active" and me["profile"].startswith("vless://"))
    check("mail 'konto aktywne'", any("konto aktywne" in sj for _t, sj, _b in mails()))
    prof = app.Profile(me["profile"])
    cores.stop_guest()
    check("xray gościa zatrzymany", not cores.guest_running())

    print("== polaczenie VPN na UUID urzadzenia (config z build_xray, bez TUN)")
    main_cfg = app.build_xray(prof, "127.0.0.1", s, "192.168.50.1", 10910)
    xc = spawn([str(XRAY), "run", "-c", "stdin:"], "xray-client.log", main_cfg)
    check("klient VPN wystartowal (config przez stdin)", wait_port(s["socks_port"]) and xc.poll() is None)
    try:
        t, status, _ = app.http_via_socks(s["socks_port"], "www.gstatic.com", "/generate_204", False, 10)
        check("internet przez serwer (generate_204)", status == 204, f"{int(t * 1000)} ms")
    except Exception as e:
        check("internet przez serwer (generate_204)", False, str(e))
    me2 = app.api_call(s["api_port"], cert, "GET", "/v1/me", token=tok)
    check("API przez tunel VPN (wejscie api)", me2["status"] == "active")

    print("== reguly na serwerze")
    vpn_raw = spawn([str(XRAY), "run", "-c", "stdin:"], "raw-vpn.log", raw_client(10870, prof))
    guest_raw = spawn([str(XRAY), "run", "-c", "stdin:"], "raw-guest.log", raw_client(10871, local_guest))
    wait_port(10870)
    wait_port(10871)
    check("VPN: internet dziala", via_socks(10870, "www.gstatic.com", 80,
                                            b"GET /generate_204 HTTP/1.0\r\nHost: www.gstatic.com\r\n\r\n"))
    check("VPN: 127.0.0.1:10085 (API Xray) zablokowane", not via_socks(10870, "127.0.0.1", 10085))
    check("VPN: localhost:10085 zablokowane", not via_socks(10870, "localhost", 10085))
    check("VPN: 192.168.x zablokowane", not via_socks(10870, "192.168.1.1", 80, t=3))
    check("VPN: SMTP :25 zablokowane", not via_socks(10870, "smtp.gmail.com", 25, b"", t=4))
    check("gość: internet zablokowany", not via_socks(10871, "www.gstatic.com", 80,
                                                     b"GET /generate_204 HTTP/1.0\r\n\r\n"))
    check("gość: API Xray zablokowane", not via_socks(10871, "127.0.0.1", 10085))
    old_cfg = raw_client(10872, app.Profile(f"vless://{old_uuid}@127.0.0.1:10900?security=none"
                                            "&type=ws&path=" + urllib.parse.quote(priv["vpn_path"], safe="") + "#o"))
    spawn([str(XRAY), "run", "-c", "stdin:"], "raw-old.log", old_cfg)
    wait_port(10872)
    check("stary UUID (okres przejsciowy) dziala", via_socks(10872, "www.gstatic.com", 80,
                                                           b"GET /generate_204 HTTP/1.0\r\n\r\n"))
    for p in (vpn_raw, guest_raw):
        p.kill()

    print("== statystyki")
    for _ in range(3):
        app.http_via_socks(s["socks_port"], "www.gstatic.com", "/generate_204", False, 10)
    time.sleep(5)
    rc, out = admin("devices", E)
    check("ruch zapisany w bazie", rc == 0 and " 0 B" not in out.splitlines()[-1], out.splitlines()[-1])

    print("== restart Xray na serwerze -> synchronizacja przywraca urzadzenia")
    xsrv.kill()
    xsrv.wait()
    time.sleep(1)
    xsrv = spawn([str(XRAY), "run", "-c", str(W / "xray-server.json")], "xray-server2.log")
    wait_port(10085)
    # urzadzenie jest tylko w bazie, nie w config.json - po restarcie wraca dzieki petli
    check("config.json serwera nie zawiera urzadzen", "dev1@akvpn" not in (W / "xray-server.json").read_text())
    time.sleep(5)
    rc, out = admin("xray")
    check("po synchronizacji urzadzenie wrocilo (petla w tle)", "dev1@akvpn" in out)
    try:
        _t, status, _ = app.http_via_socks(s["socks_port"], "www.gstatic.com", "/generate_204", False, 10)
        check("VPN znow dziala", status == 204)
    except Exception as e:
        check("VPN znow dziala", False, str(e))

    print("== blokada / odblokowanie / wylogowanie")
    admin("block", E)

    def vpn_works():
        try:
            return app.http_via_socks(s["socks_port"], "www.gstatic.com", "/generate_204", False, 6)[1] == 204
        except Exception:
            return False
    check("zablokowany: VPN nie dziala", not vpn_works())
    cores.start_guest(local_guest)
    try:
        g("GET", "/v1/me", token=tok)
        check("zablokowany: /me przez gościa -> blocked", False)
    except app.ApiError as e:
        check("zablokowany: /me przez gościa -> blocked", e.code == "blocked")
    admin("unblock", E)
    check("odblokowany: VPN dziala", vpn_works())
    app.api_call(s["api_port"], cert, "POST", "/v1/logout", token=tok)
    rc, out = admin("xray")
    check("wylogowanie usuwa UUID z Xray", "dev1@akvpn" not in out)
    check("po wylogowaniu VPN nie dziala", not vpn_works())
    cores.stop_guest()

    print("== reset hasla")
    cores.start_guest(local_guest)
    g("POST", "/v1/password/forgot", {"email": E})
    g("POST", "/v1/password/reset", {"email": E, "code": last_code(E), "password": "nowehaslo1"})
    r = g("POST", "/v1/login", {"email": E, "password": "nowehaslo1", "device_id": d1,
                                "device_name": "TEST-PC"})
    check("logowanie nowym haslem", r["status"] == "active" and r["profile"])
    check("to samo urzadzenie = nowy UUID po resecie", app.Profile(r["profile"]).uuid != prof.uuid)
    cores.stop_guest()

    print("== przeplyw w aplikacji (App + okno, bez TUN)")
    gui_flow(local_guest)


def gui_flow(local_guest):
    import queue as _q
    a = app.App()
    a.s.update(test_settings())
    a._init_account()
    a.guest_prof = local_guest           # zamiast Cloudflare - lokalny serwer
    a.gui = app.Gui(a)
    connects = []
    a.connect_async = lambda: connects.append(time.time())   # bez TUN (brak admina)

    def pump(until, t=40):
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

    E2 = "bartek@example.com"
    check("start bez sesji -> brak profilu", a.session is None and not isinstance(a.cores.profile, app.Profile))
    a.gui.auth_view("login")
    a.auth_login(E2, "haslo1234", True)
    pump(lambda: not a.auth_busy)
    check("okno: zle dane -> komunikat", "hasło" in a.gui._auth_status.cget("text"),
          a.gui._auth_status.cget("text"))
    a.gui.auth_view("register")
    a.auth_register(E2, "haslo1234", "inne-haslo", True)
    check("okno: rozne hasla wykryte lokalnie", a.gui._auth_status.cget("text") == "Hasła się różnią.")
    a.auth_register(E2, "haslo1234", "haslo1234", True)
    check("okno: rejestracja -> ekran kodu", pump(lambda: not a.auth_busy and a.gui.auth_current == "verify"))
    a.auth_verify(E2, "000000" if last_code(E2) != "000000" else "111111")
    pump(lambda: not a.auth_busy)
    check("okno: zly kod -> komunikat", "kod" in a.gui._auth_status.cget("text").lower())
    a.auth_verify(E2, last_code(E2))
    check("okno: kod -> automatyczne logowanie -> czekanie",
          pump(lambda: not a.auth_busy and a.gui.auth_current == "pending"))
    check("sesja zapamietana (DPAPI)", app.load_session()["status"] == "pending")
    admin("approve", E2)
    a._last_check = 0
    a.account_tick()
    check("akceptacja wykryta -> polaczenie", pump(lambda: connects and a.gui.auth_current is None))
    check("profil VPN z serwera w rdzeniach", isinstance(a.cores.profile, app.Profile)
          and a.cores.profile.path == json.loads((ROOT / "src" / "serwer_prywatny.json").read_text())["vpn_path"])
    rc, out = admin("devices", E2)
    a.logout_async()
    check("wylogowanie -> wybór serwera", pump(lambda: a.gui.auth_current == "start" and a.session is None))
    check("sesja usunieta z dysku", app.load_session() is None)
    rc, out = admin("devices", E2)
    check("urzadzenie usuniete na serwerze", "(brak)" in out)
    a.cores.stop_guest()
    a.gui.root.destroy()


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
