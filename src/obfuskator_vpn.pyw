#!/usr/bin/env python3
"""
Obfuskator VPN (dawniej Akademik VPN) - samodzielny klient VLESS (Xray + sing-box TUN) z ikona w zasobniku.

Co robi:
  * konto uzytkownika: rejestracja / logowanie przez API na serwerze. Przed
    zalogowaniem laczy sie "wejsciem gościa" (UUID wbudowany w exe), ktore na
    serwerze dochodzi tylko do API logowania. Po akceptacji konta serwer wydaje
    urzadzeniu wlasny UUID i link vless://, z ktorego aplikacja buduje configi
    dla xray.exe (proxy) i sing-box.exe (TUN) z folderu bin;
  * ma wbudowany lokalny serwer DNS (127.0.0.1:10853), ktory odpowiada na
    zapytanie Xray o rekord HTTPS z kluczem ECH. Klucz pobiera w tle (przez
    serwer, a awaryjnie bezposrednio) i trzyma ostatni dobry, wiec chwilowa
    awaria DNS ani rotacja klucza przez Cloudflare nie zrywaja polaczenia;
  * pokazuje ikone w zasobniku (kolor = stan) i okno ze statusem.

Dlaczego lokalny DNS: TUN ze StrictRoute przechwytuje i blokuje DNS poza
tunelem, wiec zapytanie Xray o ECH do routera ginelo, Xray podstawial
atrape klucza i konczylo sie bledem "tls: malformed ECHConfigList".
Petla zwrotna (127.0.0.1) nie przechodzi przez TUN.

Wymaga uprawnien administratora (TUN). Pierwsze uruchomienie pyta o UAC
i rejestruje zadanie harmonogramu "ObfuskatorVPN"; kolejne uruchomienia
(skrot, pasek zadan) ida przez to zadanie, juz bez UAC.

Budowanie: src\build.cmd (exe), src\pakiet.cmd (zip dla innych).
"""
import base64
import ctypes
import ctypes.wintypes
import hashlib
import http.client
import json
import math
import os
import queue
import random
import re
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

APP_NAME = "Obfuskator VPN"
FROZEN = getattr(sys, "frozen", False)   # zbudowane PyInstallerem (ObfuskatorVPN.exe)
APP_DIR = Path(sys.executable if FROZEN else __file__).resolve().parent
if not FROZEN and APP_DIR.name == "src":
    APP_DIR = APP_DIR.parent
DATA_DIR = APP_DIR / "data"
SETTINGS_FILE = APP_DIR / "settings.json"
STATE_FILE = DATA_DIR / "ech_state.json"
LOG_FILE = DATA_DIR / "app.log"
SESSION_FILE = DATA_DIR / "session.bin"    # sesja konta, zaszyfrowana DPAPI
DEVICE_FILE = DATA_DIR / "device.json"     # staly identyfikator tego komputera
ICON_FILE = APP_DIR / "app.ico"
BIN_DIR = APP_DIR / "bin"                 # xray.exe, sing-box.exe
TASK_NAME = "ObfuskatorVPN"
TUN_NAME = "ObfuskatorVPN"
OLD_NAMES = ("AkademikVPN", "Akademik VPN")   # poprzednia nazwa - sprzatanie po aktualizacji

DEFAULTS = {
    "socks_port": 10818,          # wejscie proxy Xray (tylko 127.0.0.1)
    "clash_port": 10824,          # API sing-box (statystyki ruchu)
    "tun": True,                  # caly ruch komputera przez tunel
    "ech_dns_port": 10853,        # lokalny serwer DNS z kluczem ECH
    "control_port": 10855,        # launcher -> aplikacja ("show")
    "ech_direct_port": 10856,     # wejscie Xray -> wyjscie direct (DoH bez tunelu)
    "ech_router_port": 10857,     # wejscie Xray -> UDP do DNS routera
    "api_port": 10860,            # wejscie Xray -> API kont na serwerze (przez tunel)
    "guest_api_port": 10861,      # to samo przez wejscie gościa (przed zalogowaniem)
    "debug_config": False,        # zapisuj config Xray do data\xray.json (diagnoza)
    "ech_refresh_minutes": 5,     # Cloudflare daje rekordowi TTL 300 s
    "ech_ttl": 60,                # TTL odpowiedzi dla Xray (s)
    "ping_interval": 10,          # s
    "auto_connect": True,
}

CREATE_NO_WINDOW = 0x08000000

COLORS = {
    "off": "#6b7280",
    "connecting": "#f5a524",
    "on": "#8b5cf6",
    "problem": "#f97316",
    "error": "#ef4444",
}
STATUS_TEXT = {
    "off": "Rozłączono",
    "connecting": "Łączenie…",
    "on": "Połączono",
    "problem": "Brak odpowiedzi",
    "error": "Błąd połączenia",
}
# ciemny fioletowy motyw okna
T = {
    "bg": "#0e0b16", "card": "#17121f", "card_hi": "#221a2e", "line": "#2e2440",
    "text": "#f1edf7", "muted": "#9a90ad", "dim": "#665c78", "off_ring": "#3b3150",
    "accent": "#8b5cf6", "accent_hi": "#a78bfa", "accent_lo": "#6d28d9",
    "down": "#c4b5fd", "up": "#22d3ee",
}


# czcionka programistyczna dolaczona do aplikacji (ladowana prywatnie, bez instalacji)
FONT_DIR = (Path(sys._MEIPASS) if FROZEN else APP_DIR / "src") / "fonts"
FONT = "Segoe UI"            # zmienia load_fonts(), gdy PT Root UI (jak w AmneziaVPN) sie zaladuje
FONT_B = "Segoe UI Semibold"


def load_fonts():
    global FONT, FONT_B
    loaded = 0
    for f in sorted(FONT_DIR.glob("*.ttf")):
        try:
            loaded += ctypes.windll.gdi32.AddFontResourceExW(str(f), 0x10, 0)  # FR_PRIVATE
        except Exception:
            pass
    if loaded:
        # czcionka zmienna - Windows udostepnia ja jako odmiany Regular/Bold i osobna Medium
        FONT = FONT_B = "PT Root UI VF"


def F(size, bold=False):
    if bold:
        return (FONT_B, size, "bold") if FONT_B == FONT else (FONT_B, size)
    return (FONT, size)


def rgb(h: str, a: int = 255):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a


def mix(c: str, other: str, t: float):
    """Kolor c przesuniety o t (0..1) w strone other, jako RGBA."""
    a, b = rgb(c), rgb(other)
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)) + (255,)


def vgradient(w: int, h: int, top, bottom) -> Image.Image:
    """Pionowy gradient RGBA (top/bottom - krotki RGBA)."""
    col = Image.new("RGBA", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        col.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(4)))
    return col.resize((w, h))


# ------------------------------------------------------------ logowanie ---

_events = deque(maxlen=200)
_log_lock = threading.Lock()


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    with _log_lock:
        _events.append(line)
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            if LOG_FILE.exists() and LOG_FILE.stat().st_size > 2_000_000:
                LOG_FILE.replace(LOG_FILE.with_suffix(".log.old"))
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


def load_settings() -> dict:
    s = dict(DEFAULTS)
    if SETTINGS_FILE.exists():
        try:
            s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            log(f"Nie moge odczytac settings.json ({e}) - uzywam domyslnych")
    else:
        SETTINGS_FILE.write_text(json.dumps(DEFAULTS, indent=2), encoding="utf-8")
    return s


def run_ps(script: str, timeout: float = 30) -> str:
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=timeout, creationflags=CREATE_NO_WINDOW,
    ).stdout


def fmt_bytes(n: float) -> str:
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def fmt_duration(sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


# ------------------------------------------------------------------ DNS ---

def dns_query_packet(domain: str, qtype: int = 65):
    tid = random.randint(0, 0xFFFF)
    qname = b"".join(bytes([len(p)]) + p.encode("idna")
                     for p in domain.rstrip(".").split(".")) + b"\0"
    return tid, struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0) + qname + struct.pack(">HH", qtype, 1)


def skip_name(msg: bytes, pos: int) -> int:
    while True:
        n = msg[pos]
        if n == 0:
            return pos + 1
        if n & 0xC0 == 0xC0:
            return pos + 2
        pos += n + 1


def read_name(msg: bytes, pos: int):
    """Nazwa bez kompresji (tak wyglada pytanie od klienta) -> (nazwa, pozycja)."""
    labels = []
    while True:
        n = msg[pos]
        if n == 0:
            return ".".join(labels).lower(), pos + 1
        if n & 0xC0:
            raise ValueError("kompresja w pytaniu")
        labels.append(msg[pos + 1:pos + 1 + n].decode("ascii", "replace"))
        pos += n + 1


def parse_ech_response(msg: bytes, tid: int):
    """Z odpowiedzi DNS na zapytanie HTTPS zwraca (ech_bytes, ttl) albo None."""
    rid, flags, qdcount, ancount = struct.unpack(">HHHH", msg[:8])
    if rid != tid:
        raise RuntimeError("odpowiedz z innym ID")
    if flags & 0x0200:
        raise RuntimeError("odpowiedz obcieta (TC)")
    if flags & 0x000F:
        raise RuntimeError(f"rcode {flags & 0x000F}")
    pos = 12
    for _ in range(qdcount):
        pos = skip_name(msg, pos) + 4
    for _ in range(ancount):
        pos = skip_name(msg, pos)
        rtype, _cls, ttl, rdlen = struct.unpack(">HHIH", msg[pos:pos + 10])
        pos += 10
        rdata = msg[pos:pos + rdlen]
        pos += rdlen
        if rtype != 65:
            continue  # np. CNAME
        p = skip_name(rdata, 2)  # SvcPriority + TargetName
        while p + 4 <= len(rdata):
            key, vlen = struct.unpack(">HH", rdata[p:p + 4])
            if key == 5:  # SvcParamKey "ech"
                return rdata[p + 4:p + 4 + vlen], ttl
            p += 4 + vlen
    return None


class _HTTPSConn(http.client.HTTPSConnection):
    """HTTPS na adres IP, opcjonalnie przez SOCKS (wejscie Xray)."""

    def __init__(self, host, socks_port, timeout):
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self._socks = socks_port

    def connect(self):
        if self._socks:
            s = socks_connect(self._socks, self.host, self.port, self.timeout)
        else:
            s = socket.create_connection((self.host, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(s, server_hostname=self.host)
        except Exception:
            s.close()
            raise


def doh_ech(domain, server_ip, socks_port, timeout=6):
    """DoH (RFC 8484, format binarny) na adres IP - bez potrzeby rozwiazywania nazwy."""
    tid, packet = dns_query_packet(domain)
    q = base64.urlsafe_b64encode(packet).rstrip(b"=").decode()
    conn = _HTTPSConn(server_ip, socks_port, timeout)
    try:
        conn.request("GET", f"/dns-query?dns={q}", headers={"Accept": "application/dns-message"})
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
    finally:
        conn.close()
    return parse_ech_response(body, tid)


def udp_ech(domain, server, port, timeout=3):
    tid, packet = dns_query_packet(domain)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(packet, (server, port))
        msg, _ = s.recvfrom(4096)
    return parse_ech_response(msg, tid)


def fetch_ech(domain, attempts):
    """attempts: [(opis, funkcja, serwer, port/socks)] - wszystkie naraz,
    pierwsza udana wygrywa (zablokowane drogi tylko czekaja na timeout).
    Zwraca (ech_bytes, opis)."""
    if not attempts:
        raise RuntimeError("brak drog pobrania")
    errors = []
    pool = ThreadPoolExecutor(max_workers=len(attempts))
    futures = {pool.submit(fn, domain, server, port): label
               for label, fn, server, port in attempts}
    try:
        for fut in as_completed(futures):
            label = futures[fut]
            try:
                res = fut.result()
            except Exception as e:
                errors.append(f"{label}: {e or type(e).__name__}")
                continue
            if res:
                return res[0], label
            errors.append(f"{label}: brak 'ech' w rekordzie HTTPS")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    raise RuntimeError("; ".join(errors))


class EchService:
    """Trzyma klucz ECH, odswieza go w tle i serwuje Xray przez lokalny DNS."""

    def __init__(self, settings):
        self.s = settings
        self.domains = set()
        self.key = None
        self.source = ""
        self.fetched_at = None
        self.next_at = None
        self.last_error = ""
        self.queries = 0
        self.attempts_fn = lambda: []   # ustawia Cores - drogi zaleza od stanu rdzeni
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._load()

    # stan na dysku - po starcie od razu serwujemy ostatni dobry klucz
    def _load(self):
        try:
            st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            self.key = base64.b64decode(st["key"])
            self.source = st.get("source", "") + " (zapisany)"
            self.fetched_at = st.get("fetched_at")
            self.domains = set(st.get("domains", []))
        except (OSError, ValueError, KeyError):
            pass

    def _save(self):
        try:
            STATE_FILE.write_text(json.dumps({
                "key": base64.b64encode(self.key).decode(),
                "source": self.source,
                "fetched_at": self.fetched_at,
                "domains": sorted(self.domains),
            }), encoding="utf-8")
        except OSError as e:
            log(f"Nie zapisalem stanu ECH: {e}")

    @property
    def key_id(self):
        return hashlib.sha256(self.key).hexdigest()[:10] if self.key else "-"

    def set_domains(self, domains):
        new = set(d.lower().rstrip(".") for d in domains if d)
        if new and new != self.domains:
            self.domains = new
            self.refresh_now()

    def refresh_now(self):
        self._wake.set()

    def start(self):
        threading.Thread(target=self._serve, daemon=True, name="ech-dns").start()
        threading.Thread(target=self._refresh_loop, daemon=True, name="ech-refresh").start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def _refresh_loop(self):
        while not self._stop.is_set():
            ok = self._refresh()
            if ok:
                wait = self.s["ech_refresh_minutes"] * 60
            else:
                wait = 60 if self.key else 15  # bez klucza nie da sie polaczyc - czesciej
            self.next_at = time.time() + wait
            self._wake.wait(wait)
            self._wake.clear()

    def _refresh(self) -> bool:
        if not self.domains:
            return False
        domain = sorted(self.domains)[0]
        try:
            key, source = fetch_ech(domain, self.attempts_fn())
        except Exception as e:
            self.last_error = str(e)
            log(f"ECH: nie udalo sie pobrac klucza ({e})"
                + (" - serwuje ostatni dobry" if self.key else ""))
            return False
        changed = key != self.key
        self.key, self.source, self.last_error = key, source, ""
        self.fetched_at = time.time()
        self._save()
        if changed:
            log(f"ECH: nowy klucz {self.key_id} ze zrodla {source}")
        return True

    def _serve(self):
        port = self.s["ech_dns_port"]
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as e:
            log(f"BLAD: nie moge otworzyc lokalnego DNS 127.0.0.1:{port} ({e})")
            return
        sock.settimeout(1)
        log(f"Lokalny DNS z kluczem ECH dziala na 127.0.0.1:{port}")
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                continue  # WSAECONNRESET po ICMP - ignorujemy
            try:
                resp = self._answer(data)
                if resp:
                    sock.sendto(resp, addr)
            except Exception as e:
                log(f"ECH DNS: zle zapytanie od {addr}: {e}")
        sock.close()

    def _answer(self, q: bytes):
        if len(q) < 12:
            return None
        tid, flags, qdcount = struct.unpack(">HHH", q[:6])
        if flags & 0x8000 or qdcount != 1:
            return None
        name, pos = read_name(q, 12)
        qtype, _qclass = struct.unpack(">HH", q[pos:pos + 4])
        question = q[12:pos + 4]
        self.queries += 1

        answer = b""
        if name not in self.domains:
            rcode = 5  # REFUSED
        elif qtype != 65:
            rcode = 0  # nazwa istnieje, ale nie mamy innych rekordow
        elif not self.key:
            rcode = 2  # SERVFAIL - jeszcze nie ma klucza
        else:
            rcode = 0
            rdata = struct.pack(">H", 1) + b"\0" + struct.pack(">HH", 5, len(self.key)) + self.key
            answer = struct.pack(">HHHIH", 0xC00C, 65, 1, self.s["ech_ttl"], len(rdata)) + rdata
        out_flags = 0x8000 | 0x0400 | (flags & 0x0100) | 0x0080 | rcode
        header = struct.pack(">HHHHHH", tid, out_flags, 1, 1 if answer else 0, 0, 0)
        return header + question + answer


# --------------------------------------------------------------- SOCKS ---

def recv_exact(s, n):
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("polaczenie zamkniete")
        buf += chunk
    return buf


def socks_connect(socks_port, host, port, timeout):
    s = socket.create_connection(("127.0.0.1", socks_port), timeout=timeout)
    try:
        s.sendall(b"\x05\x01\x00")
        if recv_exact(s, 2) != b"\x05\x00":
            raise ConnectionError("SOCKS: odmowa")
        h = host.encode()
        s.sendall(b"\x05\x01\x00\x03" + bytes([len(h)]) + h + struct.pack(">H", port))
        r = recv_exact(s, 4)
        if r[1] != 0:
            raise ConnectionError(f"SOCKS: kod {r[1]}")
        alen = {1: 4, 4: 16}.get(r[3]) or recv_exact(s, 1)[0]
        recv_exact(s, alen + 2)
        return s
    except Exception:
        s.close()
        raise


def http_via_socks(socks_port, host, path, tls, timeout=8):
    """Proste zapytanie GET przez proxy; zwraca (czas_s, status, cialo)."""
    t0 = time.perf_counter()
    s = socks_connect(socks_port, host, 443 if tls else 80, timeout)
    try:
        if tls:
            s = ssl.create_default_context().wrap_socket(s, server_hostname=host)
        s.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: ObfuskatorVPN\r\n"
                  f"Connection: close\r\n\r\n".encode())
        data = b""
        while len(data) < 65536:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
            if not tls and b"\r\n\r\n" in data:
                break  # do pomiaru pingu wystarczy naglowek
        elapsed = time.perf_counter() - t0
    finally:
        s.close()
    head, _, body = data.partition(b"\r\n\r\n")
    status = int(head.split(b" ", 2)[1]) if head.startswith(b"HTTP/") else 0
    return elapsed, status, body.decode("utf-8", "replace")


# ----------------------------------------------------------- procesy ---

def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def kill_from_dir(folder: Path, names=None) -> int:
    """Zabija procesy uruchomione z folderu (opcjonalnie tylko o danych nazwach)."""
    d = str(folder.resolve()).replace("'", "''")
    flt = ""
    if names:
        flt = " -and (@(" + ",".join(f"'{n}'" for n in names) + ") -contains $_.ProcessName)"
    out = run_ps(
        f"$d='{d}'; $p=@(Get-Process | Where-Object {{ $_.Path -and "
        f"$_.Path.StartsWith($d,[StringComparison]::OrdinalIgnoreCase){flt} }}); "
        "$p | Stop-Process -Force -ErrorAction SilentlyContinue; $p.Count")
    try:
        return int(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0


def port_open(port, timeout=0.3) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


PRIVATE_V4 = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
              "169.254.0.0/16", "100.64.0.0/10"]
PRIVATE_V6 = ["::1/128", "fc00::/7", "fe80::/10"]
NETWORKS = {"tcp": "raw", "raw": "raw", "ws": "ws", "grpc": "grpc", "xhttp": "xhttp",
            "httpupgrade": "httpupgrade"}


def is_ip(host: str) -> bool:
    try:
        socket.inet_pton(socket.AF_INET6 if ":" in host else socket.AF_INET, host)
        return True
    except OSError:
        return False


class Profile:
    """Serwer z linku vless:// (taki, jaki eksportuje v2rayN / 3x-ui)."""

    def __init__(self, link: str):
        link = link.strip()
        u = urllib.parse.urlsplit(link)
        if u.scheme.lower() != "vless":
            raise ValueError("To nie jest link vless://")
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query, keep_blank_values=True).items()}
        self.link = link
        self.uuid = urllib.parse.unquote(u.username or "")
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", self.uuid):
            raise ValueError("Link nie zawiera poprawnego identyfikatora (UUID)")
        try:
            self.host, self.port = u.hostname, u.port or 443
        except ValueError:
            raise ValueError("Zły port w linku")
        if not self.host:
            raise ValueError("Link nie zawiera adresu serwera")
        self.name = urllib.parse.unquote(u.fragment) or self.host
        net = q.get("type", "tcp").lower()
        if net not in NETWORKS:
            raise ValueError(f"Nieobsługiwany transport: {net}")
        self.network = NETWORKS[net]
        self.security = q.get("security", "none").lower()
        if self.security not in ("none", "tls", "reality"):
            raise ValueError(f"Nieobsługiwane zabezpieczenie: {self.security}")
        self.ws_host = q.get("host", "")
        self.sni = q.get("sni") or q.get("peer") or self.ws_host or (
            "" if is_ip(self.host) else self.host)
        self.alpn = [a for a in q.get("alpn", "").split(",") if a]
        self.fp = q.get("fp", "")
        self.path = q.get("path", "/") or "/"
        self.service = q.get("serviceName", "")
        self.mode = q.get("mode", "")
        self.flow = q.get("flow", "")
        self.pbk, self.sid, self.spx = q.get("pbk", ""), q.get("sid", ""), q.get("spx", "")
        self.insecure = q.get("allowInsecure", "") in ("1", "true")
        self.ech = bool(q.get("ech") or q.get("echConfigList")) and self.security == "tls"
        if self.ech and not self.sni:
            raise ValueError("ECH wymaga parametru sni")

    # opis do okna
    @property
    def address(self):
        return f"{self.host}:{self.port}"

    @property
    def protocol(self):
        return "VLESS"

    @property
    def transport(self):
        sec = self.security.upper() + (" + ECH" if self.ech else "")
        return f"{self.network.upper()} / {sec}"

    @property
    def remarks(self):
        return self.name


class NoProfile:
    remarks = "Nie zalogowano"
    address = protocol = transport = sni = "–"


# ---------------------------------------------------------------- konto ---

def _keystream(seed: bytes, n: int) -> bytes:
    out, i = b"", 0
    while len(out) < n:
        out += hashlib.sha256(seed + i.to_bytes(4, "big")).digest()
        i += 1
    return out[:n]


def embedded() -> dict:
    """Dane wejscia gościa i certyfikat API (zaciemnione przez osadz.py przy budowaniu)."""
    try:
        from _wbudowane import BLOB
    except ImportError:  # uruchomienie ze zrodel bez build.cmd
        return json.loads((APP_DIR / "src" / "serwer_prywatny.json").read_text(encoding="utf-8")) | {
            "api_port": 10910,
            "api_cert": (APP_DIR / "server" / "tajne" / "tls.crt").read_text(encoding="ascii")}
    import zlib
    b = base64.b85decode(BLOB)
    seed, body = b[:16], b[16:]
    return json.loads(zlib.decompress(bytes(x ^ y for x, y in zip(body, _keystream(seed, len(body))))))


def guest_profile(e: dict) -> Profile:
    q = urllib.parse.urlencode({"encryption": "none", "security": "tls", "sni": e["sni"],
                                "alpn": e.get("alpn", ""), "type": "ws", "host": e["sni"],
                                "path": e["guest_path"], "ech": "1"})
    return Profile(f"vless://{e['guest_uuid']}@{e['host']}:{e['port']}?{q}#{urllib.parse.quote(APP_NAME)}")


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    """Szyfrowanie Windows DPAPI - odczyta tylko to samo konto Windows."""
    buf = ctypes.create_string_buffer(data, len(data))
    src = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    dst = _Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(dst)):  # UI_FORBIDDEN
        raise OSError("DPAPI: " + ctypes.FormatError())
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(dst.pbData)


def load_session():
    try:
        s = json.loads(_dpapi(SESSION_FILE.read_bytes(), False))
        return s if s.get("token") and s.get("email") else None
    except (OSError, ValueError):
        return None


def save_session(s):
    """s=None usuwa zapamietana sesje."""
    try:
        if s is None:
            SESSION_FILE.unlink(missing_ok=True)
        else:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            SESSION_FILE.write_bytes(_dpapi(json.dumps(s).encode(), True))
    except OSError as e:
        log(f"Nie zapisalem sesji: {e}")


def device_id() -> str:
    try:
        d = json.loads(DEVICE_FILE.read_text(encoding="utf-8"))["id"]
        if re.fullmatch(r"[A-Za-z0-9_-]{8,64}", d):
            return d
    except (OSError, ValueError, KeyError, TypeError):
        pass
    import secrets
    d = secrets.token_urlsafe(18)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DEVICE_FILE.write_text(json.dumps({"id": d}), encoding="utf-8")
    return d


def open_privacy():
    for f in (APP_DIR / "PRYWATNOSC.txt", APP_DIR / "src" / "paczka" / "PRYWATNOSC.txt"):
        if f.exists():
            os.startfile(str(f))
            return


def device_name() -> str:
    return (os.environ.get("COMPUTERNAME") or socket.gethostname() or "Komputer")[:40]


class ApiError(Exception):
    """Odpowiedz API z bledem (code - kod maszynowy, komunikat po polsku)."""

    def __init__(self, status, code, message, detail=None):
        super().__init__(message)
        self.status, self.code, self.detail = status, code, detail or {}


class NetError(Exception):
    """Serwer nieosiagalny (tunel nie dziala, brak klucza ECH, brak internetu)."""


def api_call(port: int, cert: str, method: str, path: str, body=None, token=None, timeout=25):
    """Zapytanie do API kont przez wejscie Xray na 127.0.0.1:port. Laczy sie z API
    na serwerze tunelem; TLS z przypietym certyfikatem, bo Cloudflare widzi
    zawartosc WebSocketu."""
    ctx = ssl.create_default_context(cadata=cert)
    ctx.check_hostname = False  # jedyny zaufany certyfikat to nasz - nazwa bez znaczenia
    conn = http.client.HTTPSConnection("127.0.0.1", port, timeout=timeout, context=ctx)
    headers = {"Content-Type": "application/json", "User-Agent": "ObfuskatorVPN"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        conn.request(method, path, json.dumps(body).encode() if body is not None else None, headers)
        resp = conn.getresponse()
        raw = resp.read()
    except ssl.SSLCertVerificationError as e:
        raise NetError(f"zły certyfikat serwera ({e.verify_message})")
    except (OSError, http.client.HTTPException) as e:
        raise NetError(str(e) or type(e).__name__)
    finally:
        conn.close()
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        raise NetError(f"dziwna odpowiedź serwera (HTTP {resp.status})")
    if resp.status >= 400:
        d = data.get("detail") if isinstance(data.get("detail"), dict) else {}
        raise ApiError(resp.status, d.get("code", "error"),
                       d.get("message") or f"Błąd serwera (HTTP {resp.status})", d)
    return data


def system_dns():
    """Serwery DNS (IPv4) karty z brama domyslna - z pominieciem naszego TUN."""
    ps = (
        "Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and "
        f"$_.NetAdapter.Status -eq 'Up' -and $_.InterfaceAlias -notmatch '{TUN_NAME}|AkademikVPN|singbox|wintun' }} | "
        "ForEach-Object { ($_.DNSServer | Where-Object AddressFamily -eq 2).ServerAddresses; "
        "'gw=' + $_.IPv4DefaultGateway.NextHop }"
    )
    try:
        lines = [x.strip() for x in run_ps(ps).splitlines() if x.strip()]
    except Exception:
        lines = []
    dns = [x for x in lines if not x.startswith("gw=") and is_ip(x) and not x.startswith("127.")]
    gws = [x[3:] for x in lines if x.startswith("gw=")]
    return (dns or gws or ["1.1.1.1"])[0]


def api_inbound(port: int, api_port: int) -> dict:
    """Wejscie, ktore kieruje polaczenie na API kont: 127.0.0.1:api_port PO STRONIE SERWERA."""
    return {"tag": "api", "listen": "127.0.0.1", "port": port, "protocol": "dokodemo-door",
            "settings": {"address": "127.0.0.1", "port": api_port, "network": "tcp"}}


def build_stream(p: Profile, s: dict) -> dict:
    stream = {"network": p.network, "security": p.security}
    if p.network == "ws":
        stream["wsSettings"] = {"path": p.path, "host": p.ws_host}
    elif p.network == "httpupgrade":
        stream["httpupgradeSettings"] = {"path": p.path, "host": p.ws_host}
    elif p.network == "grpc":
        stream["grpcSettings"] = {"serviceName": p.service}
    elif p.network == "xhttp":
        stream["xhttpSettings"] = {"path": p.path, "host": p.ws_host, "mode": p.mode or "auto"}
    if p.security == "tls":
        tls = {"serverName": p.sni, "allowInsecure": p.insecure}
        if p.alpn:
            tls["alpn"] = p.alpn
        if p.fp:
            tls["fingerprint"] = p.fp
        if p.ech:
            # klucz ECH z naszego lokalnego DNS (patrz EchService)
            tls["echConfigList"] = f"{p.sni}+udp://127.0.0.1:{s['ech_dns_port']}"
            tls["echForceQuery"] = "full"
        stream["tlsSettings"] = tls
    elif p.security == "reality":
        stream["realitySettings"] = {"serverName": p.sni, "fingerprint": p.fp or "chrome",
                                     "publicKey": p.pbk, "shortId": p.sid, "spiderX": p.spx}
    return stream


def vless_outbound(p: Profile, server_ip: str, s: dict) -> dict:
    user = {"address": server_ip, "port": p.port, "id": p.uuid, "encryption": "none"}
    if p.flow:
        user["flow"] = p.flow
    return {"tag": "proxy", "protocol": "vless", "settings": user,
            "streamSettings": build_stream(p, s)}


def build_guest_xray(p: Profile, server_ip: str, s: dict, api_port: int) -> dict:
    """Wejscie gościa: tylko droga do API kont (rejestracja, logowanie, status konta)."""
    return {
        "log": {"loglevel": "warning", "access": "none"},
        "inbounds": [api_inbound(s["guest_api_port"], api_port)],
        "outbounds": [vless_outbound(p, server_ip, s)],
    }


def build_xray(p: Profile, server_ip: str, s: dict, sys_dns: str, api_port: int) -> dict:
    return {
        "log": {"loglevel": "warning", "access": "none"},
        "inbounds": [
            api_inbound(s["api_port"], api_port),
            {"tag": "socks", "listen": "127.0.0.1", "port": s["socks_port"], "protocol": "mixed",
             "sniffing": {"enabled": True, "destOverride": ["http", "tls"], "routeOnly": False},
             "settings": {"auth": "noauth", "udp": True}},
            # awaryjne drogi po klucz ECH: xray.exe jest wylaczony z TUN,
            # wiec jego wyjscie "direct" naprawde omija tunel
            {"tag": "ech-direct", "listen": "127.0.0.1", "port": s["ech_direct_port"],
             "protocol": "socks", "settings": {"auth": "noauth", "udp": False}},
            {"tag": "ech-router", "listen": "127.0.0.1", "port": s["ech_router_port"],
             "protocol": "dokodemo-door",
             "settings": {"address": sys_dns, "port": 53, "network": "udp"}},
        ],
        "outbounds": [
            vless_outbound(p, server_ip, s),
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ],
        "routing": {"domainStrategy": "AsIs", "rules": [
            # 127.0.0.1 z wejscia "api" to adres na serwerze - musi isc tunelem
            {"type": "field", "inboundTag": ["api"], "outboundTag": "proxy"},
            {"type": "field", "inboundTag": ["ech-direct", "ech-router"], "outboundTag": "direct"},
            {"type": "field", "ip": [f"{server_ip}/32" if ":" not in server_ip else server_ip],
             "outboundTag": "direct"},
            # QUIC blokujemy - aplikacje wracaja do TCP, ktore idzie przez tunel
            {"type": "field", "network": "udp", "port": "443", "outboundTag": "block"},
            {"type": "field", "ip": PRIVATE_V4 + PRIVATE_V6, "outboundTag": "direct"},
            {"type": "field", "port": "0-65535", "outboundTag": "proxy"},
        ]},
    }


def build_singbox(p: Profile, server_ip: str, s: dict, sys_dns: str, xray_exe: Path,
                  sbox_exe: Path) -> dict:
    cores = [str(xray_exe), str(sbox_exe)]
    dns_rules = [{"action": "predefined", "rcode": "NOERROR", "query_type": [64, 65]}]
    if not is_ip(p.host):
        dns_rules.insert(0, {"server": "local", "domain": [p.host]})
    return {
        "log": {"level": "warn", "timestamp": True},
        "dns": {
            "servers": [
                {"type": "udp", "tag": "local", "server": sys_dns},
                {"type": "https", "tag": "remote", "server": "1.1.1.1", "detour": "proxy"},
            ],
            "rules": dns_rules,
            "final": "remote",
        },
        "inbounds": [{
            "type": "tun", "tag": "tun", "interface_name": TUN_NAME,
            "address": ["172.18.0.1/30"], "mtu": 9000,
            "auto_route": True, "strict_route": True, "stack": "gvisor",
        }],
        "outbounds": [
            {"type": "socks", "tag": "proxy", "server": "127.0.0.1",
             "server_port": s["socks_port"], "version": "5"},
            {"type": "direct", "tag": "direct"},
        ],
        "route": {
            "default_domain_resolver": {"server": "local"},
            "auto_detect_interface": True,
            "rules": [
                # zapytania xray.exe do DNS sieci (droga awaryjna po klucz ECH) wprost
                {"process_path": [str(xray_exe)], "ip_cidr": [f"{sys_dns}/32"], "port": [53],
                 "outbound": "direct"},
                {"network": ["udp"], "port": [135, 137, 138, 139, 5353], "action": "reject"},
                {"ip_cidr": ["224.0.0.0/3", "ff00::/8"], "action": "reject"},
                {"ip_cidr": ["172.18.0.1/32"], "action": "reject", "method": "drop"},
                {"port": [53], "process_path": cores, "action": "hijack-dns"},
                {"process_path": cores, "outbound": "direct"},
                {"action": "sniff"},
                {"type": "logical", "mode": "or",
                 "rules": [{"port": [53]}, {"protocol": ["dns"]}], "action": "hijack-dns"},
                {"ip_cidr": [f"{server_ip}/32" if ":" not in server_ip else server_ip],
                 "outbound": "direct"},
                {"network": ["udp"], "port": [443], "action": "reject"},
                {"ip_is_private": True, "outbound": "direct"},
            ],
            "final": "proxy",
        },
        "experimental": {"clash_api": {"external_controller": f"127.0.0.1:{s['clash_port']}"}},
    }


class Cores:
    """Uruchamia xray (proxy) i sing-box (TUN) z wbudowanego folderu bin."""

    def __init__(self, settings, ech: EchService):
        self.s = settings
        self.ech = ech
        self.xray_exe = BIN_DIR / "xray.exe"
        self.sbox_exe = BIN_DIR / "sing-box.exe"
        self.xray = None
        self.sbox = None
        self.socks_port = settings["socks_port"]
        self.clash_port = settings["clash_port"]
        self.router_dns = "192.168.50.1"
        self.profile = NoProfile()
        self.remote_api_port = 10910    # port API kont na serwerze (z danych wbudowanych)
        self.started_at = None
        self._logs = []
        self.guest = None
        self._guest_log = None
        self._guest_lock = threading.Lock()
        ech.attempts_fn = self.ech_attempts

    def ech_attempts(self):
        """Drogi pobrania klucza ECH. Przy TUN ze StrictRoute zwykly proces nie
        wyjdzie poza tunel, a siec moze blokowac publiczne DoH - dlatego glownie przez Xray."""
        if self.running():
            return [
                ("DoH 1.1.1.1 przez serwer", doh_ech, "1.1.1.1", self.socks_port),
                ("DoH 8.8.8.8 przez serwer", doh_ech, "8.8.8.8", self.socks_port),
                ("DoH 1.1.1.1 bezpośrednio", doh_ech, "1.1.1.1", self.s["ech_direct_port"]),
                (f"DNS {self.router_dns} (sieć)", udp_ech, "127.0.0.1", self.s["ech_router_port"]),
            ]
        return [  # rdzenie wylaczone - zwykla siec
            ("DoH 1.1.1.1", doh_ech, "1.1.1.1", None),
            ("DoH 8.8.8.8", doh_ech, "8.8.8.8", None),
            (f"DNS {self.router_dns} (sieć)", udp_ech, self.router_dns, 53),
        ]

    def set_profile(self, p):
        """Profil z sesji konta (Profile) albo NoProfile po wylogowaniu."""
        self.profile = p if isinstance(p, Profile) else NoProfile()
        if isinstance(p, Profile) and p.ech:
            self.ech.set_domains([p.sni])

    @staticmethod
    def server_ip(p: Profile) -> str:
        if is_ip(p.host):
            return p.host
        try:
            return socket.getaddrinfo(p.host, p.port, socket.AF_INET)[0][4][0]
        except OSError as e:
            raise RuntimeError(f"Nie mogę znaleźć adresu serwera {p.host}: {e}")

    def _write_configs(self, p: Profile) -> dict:
        """Config sing-box na dysk; config Xray zwraca (idzie przez stdin, bez pliku)."""
        server_ip = self.server_ip(p)
        self.router_dns = system_dns()
        xcfg = build_xray(p, server_ip, self.s, self.router_dns, self.remote_api_port)
        scfg = build_singbox(p, server_ip, self.s, self.router_dns, self.xray_exe, self.sbox_exe)
        self._debug_dump("xray.json", xcfg)
        (DATA_DIR / "singbox.json").write_text(json.dumps(scfg, indent=2), encoding="utf-8")
        return xcfg

    def _debug_dump(self, name, cfg):
        f = DATA_DIR / name
        try:
            if self.s.get("debug_config"):
                f.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            else:
                f.unlink(missing_ok=True)  # nie zostawiamy danych serwera na dysku
        except OSError:
            pass

    # -- procesy --
    def _spawn(self, exe: Path, args, logname, stdin_cfg=None):
        f = open(DATA_DIR / logname, "w", encoding="utf-8", errors="replace")
        self._logs.append(f)
        proc = subprocess.Popen([str(exe)] + args, cwd=str(DATA_DIR), stdout=f,
                                stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW,
                                stdin=subprocess.PIPE if stdin_cfg is not None else None,
                                env={**os.environ, "XRAY_LOCATION_ASSET": str(BIN_DIR)})
        if stdin_cfg is not None:
            try:
                proc.stdin.write(json.dumps(stdin_cfg).encode())
                proc.stdin.close()
            except OSError:
                pass  # xray padl od razu - wyjdzie przy sprawdzaniu poll()
        return proc

    def running(self) -> bool:
        return bool(self.xray and self.xray.poll() is None)

    def dead_core(self):
        if self.xray and self.xray.poll() is not None:
            return f"xray (kod {self.xray.returncode})"
        if self.sbox and self.sbox.poll() is not None:
            return f"sing-box (kod {self.sbox.returncode})"
        return None

    def _wait_port(self, proc, port, what):
        for _ in range(50):
            if port_open(port):
                return
            if proc.poll() is not None:
                raise RuntimeError(f"{what} zakończył się od razu (kod {proc.returncode}) – "
                                   f"zobacz logi")
            time.sleep(0.1)

    def start(self):
        self.stop()
        self.stop_guest()
        p = self.profile
        if not isinstance(p, Profile):
            raise RuntimeError("Zaloguj się, aby połączyć")
        for exe in (self.xray_exe, self.sbox_exe):
            if not exe.exists():
                raise RuntimeError(f"Brak pliku {exe} – rozpakuj całą paczkę jeszcze raz")
        # v2rayN z wlaczonym TUN gryzlby sie z naszym
        v2 = run_ps("(Get-Process v2rayN -ErrorAction SilentlyContinue | "
                    "Select-Object -First 1).Path").strip()
        if v2:
            log("v2rayN jest uruchomiony - zamykam go, zeby nie dublowac TUN")
            kill_from_dir(Path(v2).parent)
            time.sleep(1.5)
        if kill_from_dir(BIN_DIR, ["xray", "sing-box"]):  # pozostalosci po awarii
            time.sleep(1.5)
        xcfg = self._write_configs(p)
        self.xray = self._spawn(self.xray_exe, ["run", "-c", "stdin:"], "xray.log", xcfg)
        self._wait_port(self.xray, self.socks_port, "xray")
        if p.ech and not self.ech.key:
            # pierwsze uruchomienie: teraz dostepne sa tez drogi przez Xray
            self.ech.refresh_now()
        if self.s.get("tun", True):
            self.sbox = self._spawn(self.sbox_exe, ["run", "-c", str(DATA_DIR / "singbox.json"),
                                                    "--disable-color"], "singbox.log")
            time.sleep(1.0)
            if self.sbox.poll() is not None:
                raise RuntimeError(f"sing-box zakończył się od razu (kod {self.sbox.returncode}) – "
                                   f"zobacz logi")
        self.started_at = time.time()
        log(f"Uruchomiono rdzenie: {p.address}, DNS sieci {self.router_dns}")

    # -- wejscie gościa: osobny xray tylko z droga do API kont --
    def guest_running(self) -> bool:
        return bool(self.guest and self.guest.poll() is None)

    def start_guest(self, p: Profile):
        """Uruchamia (jesli trzeba) xray z wejsciem gościa. Dziala tez obok TUN -
        xray.exe jest wylaczony z tunelu regula process_path."""
        with self._guest_lock:
            if self.guest_running():
                return
            if not self.xray_exe.exists():
                raise RuntimeError(f"Brak pliku {self.xray_exe} – rozpakuj całą paczkę jeszcze raz")
            if p.ech:
                self.ech.set_domains([p.sni])
            cfg = build_guest_xray(p, self.server_ip(p), self.s, self.remote_api_port)
            self._debug_dump("xray-guest.json", cfg)
            f = open(DATA_DIR / "xray-guest.log", "w", encoding="utf-8", errors="replace")
            self._guest_log = f
            self.guest = subprocess.Popen([str(self.xray_exe), "run", "-c", "stdin:"],
                                          cwd=str(DATA_DIR), stdout=f, stderr=subprocess.STDOUT,
                                          stdin=subprocess.PIPE, creationflags=CREATE_NO_WINDOW,
                                          env={**os.environ, "XRAY_LOCATION_ASSET": str(BIN_DIR)})
            try:
                self.guest.stdin.write(json.dumps(cfg).encode())
                self.guest.stdin.close()
            except OSError:
                pass
            self._wait_port(self.guest, self.s["guest_api_port"], "xray (gość)")

    def stop_guest(self):
        with self._guest_lock:
            if self.guest and self.guest.poll() is None:
                self.guest.terminate()
                try:
                    self.guest.wait(5)
                except subprocess.TimeoutExpired:
                    self.guest.kill()
            self.guest = None
            if self._guest_log:
                try:
                    self._guest_log.close()
                except OSError:
                    pass
                self._guest_log = None

    def stop(self):
        for proc in (self.sbox, self.xray):  # najpierw TUN, potem proxy
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        self.xray = self.sbox = None
        self.started_at = None
        for f in self._logs:
            try:
                f.close()
            except OSError:
                pass
        self._logs = []


# ------------------------------------------------------------- monitor ---

class Monitor:
    def __init__(self, app):
        self.app = app
        self.status = "off"
        self.ping_ms = None
        self.fails = 0
        self.public = None       # (ip, loc, colo)
        self.down = self.up = 0.0
        self.down_total = self.up_total = 0
        self.conns = 0
        self._last_tot = None
        self._last_ping = 0
        self._last_public = 0
        self._last_ech_kick = 0
        self._restarts = []
        self._ping_busy = False
        self.history = deque(maxlen=60)   # (pobieranie, wysylanie) B/s co sekunde

    def loop(self):
        while not self.app.quitting:
            try:
                self.tick()
            except Exception:
                log("Monitor: " + traceback.format_exc().strip().splitlines()[-1])
            time.sleep(1)

    def tick(self):
        app = self.app
        cores = app.cores
        app.account_tick()
        if not app.want_connected:
            if self.status != "error":  # blad zostaje widoczny do nastepnej proby
                self.status = "off"
            return
        if app.busy:
            self.status = "connecting"
            return

        dead = cores.dead_core()
        if dead:
            now = time.time()
            self._restarts = [t for t in self._restarts if now - t < 300]
            if len(self._restarts) >= 5:
                log(f"{dead} pada w kolko - zatrzymuje. Zobacz logi w folderze data.")
                app.want_connected = False
                cores.stop()
                self.status = "error"
                app.notify("Rdzeń ciągle się wyłącza – rozłączono. Sprawdź logi.")
                return
            self._restarts.append(now)
            log(f"{dead} sie wylaczyl - uruchamiam ponownie")
            app.connect_async()
            return

        now = time.time()
        if not self._ping_busy and (now - self._last_ping >= self.app.s["ping_interval"] or (
                self.status == "connecting" and now - self._last_ping >= 2)):
            self._last_ping = now
            self._ping_busy = True
            threading.Thread(target=self._ping, daemon=True).start()
        self._traffic()
        self.history.append((self.down, self.up))
        if self.status == "on" and (self.public is None or now - self._last_public > 600):
            self._last_public = now
            threading.Thread(target=self._public_ip, daemon=True).start()

    def _ping(self):
        try:
            self._check()
        finally:
            self._ping_busy = False

    def _check(self):
        if not self.app.want_connected or self.app.busy:
            return
        try:
            t, status, _ = http_via_socks(self.app.cores.socks_port, "www.gstatic.com",
                                          "/generate_204", tls=False, timeout=8)
            if status not in (204, 200):
                raise RuntimeError(f"HTTP {status}")
            self.ping_ms = int(t * 1000)
            if self.status != "on":
                log(f"Polaczono (ping {self.ping_ms} ms)")
                if self.status in ("problem",):
                    self.app.notify("Połączenie przywrócone.")
            self.fails = 0
            self.status = "on"
        except Exception as e:
            if not self.app.want_connected or self.app.busy:
                return  # rozlaczanie w trakcie testu
            self.fails += 1
            self.ping_ms = None
            if self.fails == 1:
                log(f"Test polaczenia nieudany: {e}")
            if self.fails >= 3:
                if self.status == "on":
                    self.app.notify("Serwer nie odpowiada – próbuję odświeżyć klucz ECH.")
                if self.status != "problem":
                    log("Brak polaczenia przez serwer")
                self.status = "problem"
                if time.time() - self._last_ech_kick > 120:
                    self._last_ech_kick = time.time()
                    self.app.ech.refresh_now()
            elif self.status != "on":
                self.status = "connecting"

    def _traffic(self):
        port = self.app.cores.clash_port
        if not port:
            return
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request("GET", "/connections")
            data = json.loads(conn.getresponse().read())
            conn.close()
        except Exception:
            return
        down, up = data.get("downloadTotal", 0), data.get("uploadTotal", 0)
        now = time.time()
        if self._last_tot:
            t0, d0, u0 = self._last_tot
            dt = max(now - t0, 0.2)
            self.down, self.up = max(down - d0, 0) / dt, max(up - u0, 0) / dt
        self._last_tot = (now, down, up)
        self.down_total, self.up_total = down, up
        self.conns = len(data.get("connections") or [])

    def _public_ip(self):
        try:
            _, status, body = http_via_socks(self.app.cores.socks_port, "www.cloudflare.com",
                                             "/cdn-cgi/trace", tls=True, timeout=10)
            kv = dict(line.split("=", 1) for line in body.splitlines() if "=" in line)
            if kv.get("ip"):
                self.public = (kv["ip"], kv.get("loc", "?"), kv.get("colo", "?"))
        except Exception as e:
            log(f"Nie sprawdzilem publicznego IP: {e}")

    def reset(self):
        self.ping_ms = None
        self.fails = 0
        self.public = None
        self.down = self.up = 0.0
        self.conns = 0
        self.history.clear()
        self._last_tot = None
        self._last_ping = 0
        self._last_public = 0


# ---------------------------------------------------------------- ikony ---

SHIELD = [(32, 2.5), (57.5, 10.5), (56, 35), (47, 50.5), (32, 61.5), (17, 50.5), (8, 35), (6.5, 10.5)]
# lewe nogi pajaka: biodro -> kolano -> stopa (prawe to lustro)
SPIDER_LEGS = [
    [(29.6, 25.0), (22.5, 16.0), (18.5, 20.5)],
    [(28.8, 27.6), (19.0, 23.0), (14.0, 28.5)],
    [(28.8, 31.0), (19.5, 32.5), (15.5, 39.5)],
    [(29.4, 34.0), (22.0, 40.5), (19.5, 47.0)],
]


def draw_icon(color: str, size: int = 64):
    """Pajak na nitce w tarczy, z dziurka od klucza na odwloku: pajeczyna
    i ukrywanie sie w niej (obfuskacja) + zamek (VPN). Kolor tarczy = stan.
    Rysowana w 4x i zmniejszana, zeby krawedzie byly gladkie."""
    k = 4
    S = size * k
    s = S / 64

    def P(pts):
        return [(x * s, y * s) for x, y in pts]
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).polygon(P(SHIELD), fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(vgradient(S, S, mix(color, "#ffffff", 0.22), mix(color, "#000000", 0.22)), (0, 0), mask)
    if size >= 40:  # pajeczyna w tle - tylko tam, gdzie ja widac
        web = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        wd = ImageDraw.Draw(web)
        spokes = [math.radians(a) for a in (25, 50, 75, 105, 130, 155)]
        lw_web = max(1, int(0.8 * s))
        for a in spokes:
            wd.line(P([(32, 4), (32 + 70 * math.cos(a), 4 + 70 * math.sin(a))]),
                    fill=(255, 255, 255, 46), width=lw_web)
        for r in (12, 22, 33, 45):
            wd.line(P([(32 + r * math.cos(a), 4 + r * math.sin(a)) for a in spokes]),
                    fill=(255, 255, 255, 46), width=lw_web)
        clip = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        clip.paste(web, (0, 0), mask)
        img.alpha_composite(clip)
    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    hole = mix(color, "#000000", 0.3)
    d.line(P([(32, 3.5), (32, 22)]), fill=white, width=max(1, int(1.4 * s)))  # nitka
    lw = (3.0 if size >= 32 else 3.8) * s
    for leg in SPIDER_LEGS:
        for side in (1, -1):
            pts = [(32 + side * (x - 32), y) for x, y in leg]
            d.line(P(pts), fill=white, width=int(lw), joint="curve")
            for x, y in pts[1:]:
                d.ellipse([x * s - lw / 2, y * s - lw / 2, x * s + lw / 2, y * s + lw / 2], fill=white)
    d.ellipse(P([(27, 21.5), (37, 30.5)]), fill=white)          # glowotulow
    d.ellipse(P([(24, 29), (40, 48.5)]), fill=white)            # odwlok
    d.ellipse(P([(29.4, 33.2), (34.6, 38.4)]), fill=hole)       # dziurka od klucza
    d.polygon(P([(31.0, 37), (33.0, 37), (33.9, 44.2), (30.1, 44.2)]), fill=hole)
    if size >= 40:
        for ex in (30.2, 33.8):                                  # oczy
            d.ellipse(P([(ex - 0.9, 23.6), (ex + 0.9, 25.4)]), fill=hole)
    return img.resize((size, size), Image.LANCZOS)


def make_ico():
    imgs = [draw_icon(COLORS["on"], n) for n in (256, 128, 64, 48, 32, 24, 16)]
    imgs[0].save(ICON_FILE, format="ICO", sizes=[(i.width, i.height) for i in imgs],
                 append_images=imgs[1:])


def dark_titlebar(win):
    """Ciemny pasek tytulu (Windows 10/11) w kolorze tla okna."""
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        on = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), 4)
        r, g, b, _ = rgb(T["bg"])
        col = ctypes.c_int(r | (g << 8) | (b << 16))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(col), 4)
    except Exception:
        pass


# ------------------------------------------------------------------ okno ---

class Gui:
    """Kompaktowe okno w stylu klientow VPN (jak AmneziaVPN): duzy przycisk i status,
    pod spodem zakladki Polaczenie / Statystyki / Konto."""
    W, H = 360, 600
    TABS = ("home", "stats", "acct")

    def __init__(self, app):
        import tkinter as tk
        from PIL import ImageTk
        self.app, self.tk, self.ImageTk = app, tk, ImageTk
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        load_fonts()
        self.root = root = tk.Tk()
        import tkinter.font
        global FONT, FONT_B
        fams = tkinter.font.families(root)
        if FONT not in fams:
            FONT, FONT_B = "Segoe UI", "Segoe UI Semibold"
        self.s = root.winfo_fpixels("1i") / 96
        root.title(APP_NAME)
        root.resizable(False, False)
        root.configure(bg=T["bg"])
        try:
            root.iconbitmap(default=str(ICON_FILE))
        except tk.TclError:
            pass
        root.protocol("WM_DELETE_WINDOW", self.hide)
        root.withdraw()
        self.visible = False
        self._placed = False
        self._dark = False
        self._hint = False
        self.c = tk.Canvas(root, width=self.px(self.W), height=self.px(self.H), bg=T["bg"],
                           highlightthickness=0, bd=0)
        self.c.pack()
        self._refs = {}
        self._btn_cache = {}
        self._btn_key = None
        self._tog_key = None
        self._pulse = 0.0
        self._logwin = None
        self._auth = None          # nakladka logowania (auth_view)
        self.auth_current = None
        self._build()
        self._animate()

    # -- pomocnicze --
    def px(self, v):
        return int(round(v * self.s))

    def text(self, x, y, txt="", size=10, color=None, bold=False, anchor="w", tags=None):
        return self.c.create_text(self.px(x), self.px(y), text=txt, fill=color or T["text"],
                                  font=F(size, bold),
                                  anchor=anchor, tags=tags)

    def photo(self, key, pil):
        ph = self.ImageTk.PhotoImage(pil)
        self._refs[key] = ph
        return ph

    def render(self, w, h, fn, k=3):
        """Rysuje w k-krotnej rozdzielczosci i zmniejsza (wygladzone krawedzie)."""
        W, H = self.px(w), self.px(h)
        img = Image.new("RGBA", (W * k, H * k), (0, 0, 0, 0))
        fn(ImageDraw.Draw(img), W * k, H * k, img)
        return img.resize((W, H), Image.LANCZOS)

    def card(self, x, y, w, h, key, tags=None, radius=14):
        r = self.px(radius) * 3
        img = self.render(w, h, lambda d, W, H, _: d.rounded_rectangle(
            [0, 0, W - 1, H - 1], radius=r, fill=rgb(T["card"]), outline=rgb(T["line"]), width=3))
        return self.c.create_image(self.px(x), self.px(y), image=self.photo(key, img), anchor="nw",
                                   tags=tags)

    def backdrop(self, w, h, glow_y, key):
        """Tlo okna z miekka fioletowa poswiata (za przyciskiem / ikona)."""
        W, H = self.px(w), self.px(h)
        img = Image.new("RGBA", (W, H), rgb(T["bg"]))
        g = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        r = self.px(150)
        cy = self.px(glow_y)
        ImageDraw.Draw(g).ellipse([W / 2 - r, cy - r * 0.8, W / 2 + r, cy + r * 0.8],
                                  fill=rgb(T["accent"], 42))
        img.alpha_composite(g.filter(ImageFilter.GaussianBlur(self.px(60))))
        return self.photo(key, img)

    def pill(self, parent, w, h, label, cmd, kind="primary", bg=None):
        """Zaokraglony przycisk (Canvas) w stylu okna: primary / secondary / danger.
        Ma .invoke(), .set_label(tekst), .set_enabled(bool)."""
        tk = self.tk
        fills = {
            "primary": (T["accent"], T["accent_hi"], "#ffffff"),
            "danger": (COLORS["error"], "#f87171", "#ffffff"),
            "secondary": (T["card_hi"], T["line"], T["text"]),
        }[kind]
        c = tk.Canvas(parent, width=self.px(w), height=self.px(h), bg=bg or T["card"],
                      highlightthickness=0, bd=0, cursor="hand2")
        imgs = {}
        for st, col in (("n", fills[0]), ("h", fills[1]), ("d", T["off_ring"])):
            top, bot = mix(col, "#ffffff", 0.08), mix(col, "#000000", 0.1)

            def draw(d, W, H, img, top=top, bot=bot):
                m = Image.new("L", (W, H), 0)
                ImageDraw.Draw(m).rounded_rectangle([0, 0, W - 1, H - 1], radius=H // 2, fill=255)
                img.paste(vgradient(W, H, top, bot), (0, 0), m)
            imgs[st] = self.photo(f"pill_{id(c)}_{st}", self.render(w, h, draw))
        item = c.create_image(0, 0, image=imgs["n"], anchor="nw")
        txt = c.create_text(self.px(w) // 2, self.px(h) // 2, text=label, fill=fills[2],
                            font=F(10, True))
        state = {"on": True, "label": label}

        def invoke():
            if state["on"]:
                cmd()

        def set_enabled(on):
            state["on"] = on
            c.itemconfigure(item, image=imgs["n" if on else "d"])
            c.itemconfigure(txt, fill=fills[2] if on else T["muted"])
            c.configure(cursor="hand2" if on else "")

        def set_label(t):
            c.itemconfigure(txt, text=t)
        c.bind("<Enter>", lambda e: state["on"] and c.itemconfigure(item, image=imgs["h"]))
        c.bind("<Leave>", lambda e: state["on"] and c.itemconfigure(item, image=imgs["n"]))
        c.bind("<Button-1>", lambda e: invoke())
        c.invoke, c.set_enabled, c.set_label = invoke, set_enabled, set_label
        c.label = label
        return c

    def switch(self, parent, var, label, bg):
        """Przelacznik z opisem (zamiast checkboxa). Ma .set_enabled(bool)."""
        tk = self.tk
        row = tk.Frame(parent, bg=bg)
        c = tk.Canvas(row, width=self.px(34), height=self.px(20), bg=bg, highlightthickness=0,
                      bd=0, cursor="hand2")
        c.pack(side="left")
        lb = tk.Label(row, text=label, bg=bg, fg=T["text"], font=F(9), cursor="hand2")
        lb.pack(side="left", padx=(self.px(8), 0))
        state = {"on": True}
        item = c.create_image(0, 0, anchor="nw")

        def redraw():
            c.itemconfigure(item, image=self.photo(f"sw_{id(c)}", self.toggle_img(
                var.get(), T["accent"] if state["on"] else T["dim"], 34, 20)))

        def flip(_e=None):
            if state["on"]:
                var.set(not var.get())
                redraw()

        def set_enabled(on):
            state["on"] = on
            lb.configure(fg=T["text"] if on else T["dim"])
            redraw()
        for w in (c, lb):
            w.bind("<Button-1>", flip)
        row.set_enabled = set_enabled
        redraw()
        return row

    def entry_box(self, parent, var, secret=False):
        """Pole tekstowe z wewnetrznym marginesem; ramka swieci na fioletowo przy pisaniu."""
        tk = self.tk
        box = tk.Frame(parent, bg=T["card_hi"], highlightthickness=1,
                       highlightbackground=T["line"], highlightcolor=T["line"])
        e = tk.Entry(box, textvariable=var, show="•" if secret else "", bg=T["card_hi"],
                     fg=T["text"], insertbackground=T["accent_hi"], relief="flat", bd=0,
                     font=F(11), disabledbackground=T["card_hi"],
                     disabledforeground=T["muted"], selectbackground=T["accent_lo"],
                     highlightthickness=0)
        e.pack(fill="x", padx=self.px(10), pady=self.px(6))
        e.bind("<FocusIn>", lambda _e: box.configure(highlightbackground=T["accent"]))
        e.bind("<FocusOut>", lambda _e: box.configure(highlightbackground=T["line"]))
        box.bind("<Button-1>", lambda _e: e.focus_set())
        return box, e

    def hover(self, tag, on_enter=None, on_leave=None, click=None):
        def enter(_):
            self.c.config(cursor="hand2")
            if on_enter:
                on_enter()

        def leave(_):
            self.c.config(cursor="")
            if on_leave:
                on_leave()
        self.c.tag_bind(tag, "<Enter>", enter)
        self.c.tag_bind(tag, "<Leave>", leave)
        if click:
            self.c.tag_bind(tag, "<Button-1>", lambda e: click(e))

    def button(self, x, y, w, h, label, cmd, tab=None, danger=False):
        tag = f"btn_{label}"
        tags = (tag, tab) if tab else tag
        imgs = {}
        fills = (("n", "#3a1d2a"), ("h", "#52202f")) if danger else (("n", T["card_hi"]), ("h", T["line"]))
        for st, fill in fills:
            img = self.render(w, h, lambda d, W, H, _, f=fill: d.rounded_rectangle(
                [0, 0, W - 1, H - 1], radius=H // 2, fill=rgb(f)))
            imgs[st] = self.photo(f"{tag}_{st}", img)
        item = self.c.create_image(self.px(x), self.px(y), image=imgs["n"], anchor="nw", tags=tags)
        self.text(x + w / 2, y + h / 2, label, 9, "#fca5a5" if danger else None, anchor="center",
                  tags=tags)
        self.hover(tag, lambda: self.c.itemconfigure(item, image=imgs["h"]),
                   lambda: self.c.itemconfigure(item, image=imgs["n"]), lambda e: cmd())

    # -- grafika --
    def power_img(self, color, glow, active):
        def draw(d, S, _H, img):
            if glow > 0:
                g = Image.new("RGBA", (S, S), (0, 0, 0, 0))
                m = S * 0.12
                ImageDraw.Draw(g).ellipse([m, m, S - m, S - m], fill=rgb(color, int(130 * glow)))
                img.alpha_composite(g.filter(ImageFilter.GaussianBlur(S * 0.05)))
            m = S * 0.14
            d.ellipse([m, m, S - m, S - m], fill=rgb(T["card"]),
                      outline=rgb(color if active else T["off_ring"]), width=int(S * 0.018))
            m = S * 0.205
            if active:  # wypelnienie z gradientem
                mk = Image.new("L", (S, S), 0)
                ImageDraw.Draw(mk).ellipse([m, m, S - m, S - m], fill=255)
                img.paste(vgradient(S, S, mix(color, "#ffffff", 0.2), mix(color, "#000000", 0.25)),
                          (0, 0), mk)
            else:
                d.ellipse([m, m, S - m, S - m], fill=rgb(T["card_hi"]))
            sym = rgb("#ffffff" if active else T["muted"])
            cx, r, w = S / 2, S * 0.105, int(S * 0.03)
            d.arc([cx - r - w / 2, cx - r - w / 2, cx + r + w / 2, cx + r + w / 2],
                  start=-55, end=235, fill=sym, width=w)
            for a in (-55, 235):
                ax, ay = cx + r * math.cos(math.radians(a)), cx + r * math.sin(math.radians(a))
                d.ellipse([ax - w / 2, ay - w / 2, ax + w / 2, ay + w / 2], fill=sym)
            y1, y2 = cx - r * 1.35, cx - r * 0.2
            d.line([(cx, y1), (cx, y2)], fill=sym, width=w)
            for yy in (y1, y2):
                d.ellipse([cx - w / 2, yy - w / 2, cx + w / 2, yy + w / 2], fill=sym)
        return self.render(150, 150, draw)

    def toggle_img(self, on, color, w=46, h=26):
        def draw(d, W, H, _):
            d.rounded_rectangle([0, 0, W - 1, H - 1], radius=H // 2,
                                fill=rgb(color if on else T["off_ring"]))
            m = H * 0.13
            kx = W - H + m if on else m
            d.ellipse([kx, m, kx + H - 2 * m, H - m], fill=rgb("#ffffff"))
        return self.render(w, h, draw)

    def avatar_img(self, initial, size=32):
        """Kolko z inicjalem e-maila na karcie konta."""
        def draw(d, W, H, img):
            mk = Image.new("L", (W, H), 0)
            ImageDraw.Draw(mk).ellipse([0, 0, W - 1, H - 1], fill=255)
            img.paste(vgradient(W, H, rgb(T["accent_hi"]), rgb(T["accent_lo"])), (0, 0), mk)
        img = self.render(size, size, draw)
        d = ImageDraw.Draw(img)
        from PIL import ImageFont
        try:
            font = ImageFont.truetype(str(FONT_DIR / "pt-root-ui_vf.ttf"), self.px(size * 0.5))
            font.set_variation_by_name("Bold")
        except (OSError, ValueError):
            font = ImageFont.load_default()
        d.text((img.width / 2, img.height / 2), initial, fill="white", font=font, anchor="mm")
        return img

    def graph_img(self, hist, w, h):
        n = 60
        data = [(0.0, 0.0)] * (n - len(hist)) + list(hist)[-n:]
        mx = max(max(max(a, b) for a, b in data) * 1.25, 16 * 1024)

        def draw(d, W, H, img):
            for i in (1, 2, 3):
                y = H * i / 4
                d.line([(0, y), (W, y)], fill=rgb(T["line"]), width=2)

            def pts(idx):
                return [(W * i / (n - 1), H - 3 - (H - 8) * v[idx] / mx) for i, v in enumerate(data)]
            down = pts(0)
            area = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(area).polygon(down + [(W, H), (0, H)], fill=rgb(T["down"], 45))
            img.alpha_composite(area)
            d.line(down, fill=rgb(T["down"]), width=5, joint="curve")
            d.line(pts(1), fill=rgb(T["up"]), width=4, joint="curve")
        return self.render(w, h, draw, k=2), mx

    # -- uklad --
    def nav_icon(self, kind, color):
        """Ikony dolnego paska: zasilanie, wykres, osoba."""
        def draw(d, W, H, _):
            c = rgb(color)
            w = max(2, int(W * 0.09))
            if kind == "home":
                r = W * 0.32
                d.arc([W / 2 - r, H / 2 - r + W * 0.04, W / 2 + r, H / 2 + r + W * 0.04], -55, 235,
                      fill=c, width=w)
                d.line([(W / 2, H * 0.1), (W / 2, H * 0.48)], fill=c, width=w)
            elif kind == "stats":
                for i, hh in enumerate((0.35, 0.65, 0.5, 0.85)):
                    x = W * (0.14 + i * 0.22)
                    d.rounded_rectangle([x, H * (0.92 - hh), x + W * 0.14, H * 0.92], radius=w // 2,
                                        fill=c)
            else:
                d.ellipse([W * 0.32, H * 0.08, W * 0.68, H * 0.44], outline=c, width=w)
                d.arc([W * 0.14, H * 0.52, W * 0.86, H * 1.25], 180, 360, fill=c, width=w)
        return self.render(20, 20, draw)

    def _build(self):
        c, P, W = self.c, self.px, self.W
        c.create_image(0, 0, image=self.backdrop(W, self.H, 175, "bg_main"), anchor="nw")
        # naglowek
        c.create_image(P(26), P(28), image=self.photo("logo", draw_icon(COLORS["on"], P(22))))
        self.text(44, 28, APP_NAME, 11, bold=True)
        dots = self.text(W - 26, 26, "⋯", 16, T["muted"], anchor="center", tags="menu")
        self.hover("menu", lambda: c.itemconfigure(dots, fill=T["text"]),
                   lambda: c.itemconfigure(dots, fill=T["muted"]), self._menu)

        # --- zakladka Polaczenie ---
        self.power = c.create_image(P(W / 2), P(165), tags=("power", "home"))
        self.hover("power", click=lambda e: self.app.toggle())
        self.status_t = self.text(W / 2, 262, "", 17, bold=True, anchor="center", tags="home")
        self.sub_t = self.text(W / 2, 288, "", 9, T["muted"], anchor="center", tags="home")
        # chipy: pobieranie, wysylanie, ping
        self.chips = {}
        cw = (W - 32 - 16) / 3
        for i, (key, color) in enumerate((("down", T["down"]), ("up", T["up"]), ("ping", T["text"]))):
            x = 16 + i * (cw + 8)
            img = self.render(cw, 34, lambda d, w_, h_, _: d.rounded_rectangle(
                [0, 0, w_ - 1, h_ - 1], radius=h_ // 2, fill=rgb(T["card"]), outline=rgb(T["line"]),
                width=3))
            c.create_image(P(x), P(318), image=self.photo(f"chip_{key}", img), anchor="nw", tags="home")
            self.chips[key] = self.text(x + cw / 2, 335, "–", 9, color, bold=True, anchor="center",
                                        tags="home")
        # konto - jak wybor serwera w AmneziaVPN
        self.card(16, 368, W - 32, 62, "card_prof", tags=("profile", "home"), radius=31)
        self.avatar = c.create_image(P(48), P(399), tags=("profile", "home"))
        self._avatar_key = None
        self.prof_name = self.text(76, 389, "", 10, bold=True, tags=("profile", "home"))
        self.prof_sub = self.text(76, 410, "", 8, T["muted"], tags=("profile", "home"))
        self.text(W - 36, 398, "›", 16, T["muted"], anchor="center", tags=("profile", "home"))
        self.hover("profile", click=lambda e: self.account_dialog())
        self.since_t = self.text(W / 2, 452, "", 8, T["dim"], anchor="center", tags="home")

        # --- zakladka Statystyki ---
        self.text(16, 70, "Statystyki", 14, bold=True, tags="stats")
        self.card(16, 92, W - 32, 128, "card_traffic", tags="stats")
        self.down_l = self.text(30, 110, "", 7, T["muted"], bold=True, tags="stats")
        self.up_l = self.text(W / 2 + 6, 110, "", 7, T["muted"], bold=True, tags="stats")
        self.down_v = self.text(30, 132, "", 13, T["down"], bold=True, tags="stats")
        self.up_v = self.text(W / 2 + 6, 132, "", 13, T["up"], bold=True, tags="stats")
        self.graph = c.create_image(P(30), P(152), anchor="nw", tags="stats")
        self.card(16, 230, W - 32, 196, "card_det", tags="stats")
        rows = [[("ip", "PUBLICZNE IP"), ("ping", "PING")],
                [("loc", "LOKALIZACJA"), ("time", "CZAS POŁĄCZENIA")],
                [("ech", "KLUCZ ECH"), ("fetched", "KLUCZ ODŚWIEŻONY")],
                [("src", "ŹRÓDŁO KLUCZA"), ("conns", "POŁĄCZENIA")]]
        self.det = {}
        for r, row in enumerate(rows):
            y = 248 + r * 44
            for col, (key, label) in enumerate(row):
                x = 30 + col * (W / 2 - 22)
                self.text(x, y, label, 7, T["dim"], bold=True, tags="stats")
                self.det[key] = self.text(x, y + 18, "–", 9, tags="stats")
        bw = (W - 32 - 8) / 2
        self.button(16, 438, bw, 34, "Odśwież klucz ECH", self.app.ech.refresh_now, "stats")
        self.button(16 + bw + 8, 438, bw, 34, "Logi", self.show_logs, "stats")

        # --- zakladka Konto ---
        self.text(16, 70, "Konto", 14, bold=True, tags="acct")
        self.card(16, 92, W - 32, 84, "card_acct", tags="acct")
        self.acc_avatar = c.create_image(P(52), P(134), tags="acct")
        self.acc_email = self.text(84, 120, "", 10, bold=True, tags="acct")
        self.acc_dev = self.text(84, 141, "", 8, T["muted"], tags="acct")
        self.acc_note = self.text(84, 158, "", 8, T["dim"], tags="acct")
        self.card(16, 186, W - 32, 96, "card_opts", tags="acct")
        self.opts = {}
        for i, (key, label) in enumerate((("autostart", "Uruchamiaj z Windowsem"),
                                          ("auto_connect", "Łącz automatycznie"))):
            y = 210 + i * 48
            self.text(30, y, label, 9, tags=("acct", f"opt_{key}"))
            self.opts[key] = c.create_image(P(W - 30), P(y), anchor="e", tags=("acct", f"opt_{key}"))
            self.hover(f"opt_{key}", click=lambda e, k=key: self.app.toggle_option(k))
        self._opts_key = None
        c.create_line(P(30), P(234), P(W - 30), P(234), fill=T["line"], tags="acct")
        self.button(16, 296, W - 32, 34, "Folder z danymi", self.app.open_logs, "acct")
        self.button(16, 338, W - 32, 34, "Prywatność", open_privacy, "acct")
        self.button(16, 380, W - 32, 34, "Wyloguj", self.app.logout_async, "acct", danger=True)
        self.button(16, 438, W - 32, 34, "Zakończ aplikację", lambda: self.app.ui.put("quit"), "acct")

        # --- dolny pasek zakladek ---
        nav_y = self.H - 58
        img = self.render(W, 58, lambda d, w_, h_, _: (
            d.rectangle([0, 0, w_, h_], fill=rgb(T["card"])),
            d.line([(0, 1), (w_, 1)], fill=rgb(T["line"]), width=3)))
        c.create_image(0, P(nav_y), image=self.photo("nav_bg", img), anchor="nw")
        self.nav = {}
        for i, (tab, label) in enumerate((("home", "Połączenie"), ("stats", "Statystyki"),
                                          ("acct", "Konto"))):
            x = W * (i * 2 + 1) / 6
            ic = c.create_image(P(x), P(nav_y + 21), tags=f"nav_{tab}")
            lb = self.text(x, nav_y + 43, label, 8, T["muted"], anchor="center", tags=f"nav_{tab}")
            # niewidoczny prostokat - caly obszar zakladki klikalny
            c.create_rectangle(P(x - W / 6), P(nav_y), P(x + W / 6), P(self.H), outline="", fill="",
                               tags=f"nav_{tab}")
            self.nav[tab] = (ic, lb)
            self.hover(f"nav_{tab}", click=lambda e, t=tab: self.set_tab(t))
        self.tab = None
        self.set_tab("home")

    def set_tab(self, tab):
        if tab == self.tab:
            return
        self.tab = tab
        for t in self.TABS:
            self.c.itemconfigure(t, state="normal" if t == tab else "hidden")
        for t, (ic, lb) in self.nav.items():
            col = T["accent_hi"] if t == tab else T["muted"]
            self.c.itemconfigure(ic, image=self.photo(f"nav_{t}_{t == tab}", self.nav_icon(t, col)))
            self.c.itemconfigure(lb, fill=col)
        self.refresh()

    def _menu(self, e):
        tk = self.tk
        m = tk.Menu(self.root, tearoff=0, bg=T["card_hi"], fg=T["text"], bd=0, relief="flat",
                    activebackground=T["line"], activeforeground=T["text"], font=F(10))
        m.add_command(label="  Połącz / rozłącz", command=self.app.toggle)
        m.add_command(label="  Odśwież klucz ECH", command=self.app.ech.refresh_now)
        m.add_command(label="  Logi", command=self.show_logs)
        m.add_separator()
        m.add_command(label="  Zakończ", command=lambda: self.app.ui.put("quit"))
        m.tk_popup(e.x_root, e.y_root)

    # -- odswiezanie --
    def _draw_power(self):
        st = self.app.monitor.status
        color = COLORS[st]
        active = st != "off"
        if st == "connecting":
            glow = round((0.3 + 0.7 * (0.5 + 0.5 * math.sin(self._pulse))) * 7) / 7
        else:
            glow = {"on": 0.9, "off": 0}.get(st, 0.6)
        key = (st, glow)
        if key == self._btn_key:
            return
        if key not in self._btn_cache:
            self._btn_cache[key] = self.ImageTk.PhotoImage(self.power_img(color, glow, active))
        self.c.itemconfigure(self.power, image=self._btn_cache[key])
        self._btn_key = key

    def _animate(self):
        if self.visible and self.app.monitor.status == "connecting":
            self._pulse += 0.35
            self._draw_power()
        self.root.after(70, self._animate)

    def refresh(self):
        app, m, cores, e = self.app, self.app.monitor, self.app.cores, self.app.ech
        c = self.c
        st = m.status
        on = st in ("on", "problem")
        p = cores.profile
        has_profile = isinstance(p, Profile)
        sess = app.session or {}
        em = sess.get("email") or p.remarks
        em_short = em if len(em) <= 26 else em[:25] + "…"
        dev = (sess.get("device") or {}).get("name") or device_name()
        initial = (sess.get("email") or "?")[0].upper()
        if initial != self._avatar_key:
            self._avatar_key = initial
            c.itemconfigure(self.avatar, image=self.photo("avatar", self.avatar_img(initial)))
            c.itemconfigure(self.acc_avatar, image=self.photo("avatar_big", self.avatar_img(initial, 44)))

        if self.tab == "home":
            self._draw_power()
            c.itemconfigure(self.status_t, text=STATUS_TEXT[st],
                            fill=T["text"] if st == "off" else COLORS[st])
            sub = {
                "off": "Kliknij przycisk, aby połączyć" if has_profile
                       else "Zaloguj się, aby połączyć",
                "connecting": "Zestawianie zaszyfrowanego tunelu…",
                "on": "Ruch jest ukryty",
                "problem": "Serwer nie odpowiada – odświeżam klucz",
                "error": (app.last_error or "Sprawdź logi")[:44],
            }[st]
            c.itemconfigure(self.sub_t, text=sub)
            c.itemconfigure(self.chips["down"], text=f"↓ {fmt_bytes(m.down)}/s" if on else "↓ –")
            c.itemconfigure(self.chips["up"], text=f"↑ {fmt_bytes(m.up)}/s" if on else "↑ –")
            c.itemconfigure(self.chips["ping"], text=f"{m.ping_ms} ms" if m.ping_ms is not None
                            else "– ms")
            c.itemconfigure(self.prof_name, text=em_short)
            c.itemconfigure(self.prof_sub, text=f"{dev[:16]} · {p.transport.replace(' ', '')}"
                            if has_profile else "Kliknij, aby się zalogować")
            since = ""
            if cores.started_at and on:
                since = f"Połączono od {fmt_duration(time.time() - cores.started_at)}"
                if m.public:
                    since += f" · {m.public[1]} {m.public[2]}"
            c.itemconfigure(self.since_t, text=since)
        elif self.tab == "stats":
            c.itemconfigure(self.down_l, text=f"↓ POBIERANIE  {fmt_bytes(m.down_total)}")
            c.itemconfigure(self.up_l, text=f"↑ WYSYŁANIE  {fmt_bytes(m.up_total)}")
            c.itemconfigure(self.down_v, text=f"{fmt_bytes(m.down)}/s")
            c.itemconfigure(self.up_v, text=f"{fmt_bytes(m.up)}/s")
            img, _ = self.graph_img(m.history, self.W - 60, 52)
            c.itemconfigure(self.graph, image=self.photo("graph", img))
            d = self.det
            c.itemconfigure(d["ip"], text=m.public[0] if m.public and on else "–")
            c.itemconfigure(d["loc"], text=f"{m.public[1]} · {m.public[2]}" if m.public and on else "–")
            c.itemconfigure(d["ping"], text=f"{m.ping_ms} ms" if m.ping_ms is not None else "–")
            c.itemconfigure(d["time"], text=fmt_duration(time.time() - cores.started_at)
                            if cores.started_at and on else "–")
            c.itemconfigure(d["ech"], text=e.key_id if e.key else "brak klucza",
                            fill=T["text"] if e.key else COLORS["error"])
            if e.fetched_at:
                t = datetime.fromtimestamp(e.fetched_at).strftime("%H:%M:%S")
                c.itemconfigure(d["fetched"], text=t + (" ⚠" if e.last_error else ""),
                                fill=COLORS["problem"] if e.last_error else T["text"])
            c.itemconfigure(d["src"], text=(e.source or "–").replace(" (sieć)", "")[:16])
            c.itemconfigure(d["conns"], text=str(m.conns) if on else "–")
        else:
            c.itemconfigure(self.acc_email, text=em_short)
            status = {"active": "konto aktywne", "pending": "czeka na akceptację"}.get(
                sess.get("status"), "nie zalogowano")
            c.itemconfigure(self.acc_dev, text=f"{dev[:18]} · {status}")
            c.itemconfigure(self.acc_note, text="Zapamiętane na tym komputerze" if app.remember
                            else "Sesja tylko do zamknięcia aplikacji")
            key = (bool(app._autostart), bool(app.s.get("auto_connect")))
            if key != self._opts_key:
                self._opts_key = key
                for k, val in zip(("autostart", "auto_connect"), key):
                    c.itemconfigure(self.opts[k], image=self.photo(
                        f"opt_{k}", self.toggle_img(val, T["accent"], 40, 22)))
        self._refresh_logs()

    # -- konto --
    def account_dialog(self):
        """Zakladka Konto (albo ekran logowania, gdy nikt nie jest zalogowany)."""
        self.show()
        if self.app.session and self.auth_current is None:
            self.set_tab("acct")

    # -- ekran logowania / rejestracji (nakladka na glowne okno) --
    def auth_view(self, view, msg="", info="", **kw):
        """Widoki: login, register, verify, pending, forgot, reset, device_limit.
        Wolane tylko z watku okna."""
        tk = self.tk
        if self._auth is None:
            self._auth = tk.Frame(self.root, bg=T["bg"])
            self._auth_email = tk.StringVar()
            self._auth_remember = tk.BooleanVar(value=True)
        a = self._auth
        a.place(x=0, y=0, relwidth=1, relheight=1)
        a.lift()
        for ch in a.winfo_children():
            ch.destroy()
        self.auth_current = view
        self._auth_widgets = []
        self._auth_primary = None
        P = self.px
        if kw.get("email"):
            self._auth_email.set(kw["email"])

        # naglowek jak w glownym oknie
        head = tk.Canvas(a, width=P(self.W), height=P(150), bg=T["bg"], highlightthickness=0, bd=0)
        head.pack(fill="x")
        head.create_image(0, 0, image=self.backdrop(self.W, 150, 82, "bg_auth"), anchor="nw")
        head.create_image(P(26), P(28), image=self.photo("auth_logo", draw_icon(COLORS["on"], P(22))))
        head.create_text(P(44), P(28), text=APP_NAME, fill=T["text"], anchor="w",
                         font=F(11, True))
        dots = head.create_text(P(self.W - 26), P(26), text="⋯", fill=T["muted"], anchor="center",
                                font=("Segoe UI", 16), tags="menu")
        head.tag_bind("menu", "<Button-1>", self._menu)
        head.tag_bind("menu", "<Enter>", lambda e: head.itemconfigure(dots, fill=T["text"]))
        head.tag_bind("menu", "<Leave>", lambda e: head.itemconfigure(dots, fill=T["muted"]))
        color = COLORS["connecting"] if view == "pending" else COLORS["on"]
        head.create_image(P(self.W / 2), P(80), image=self.photo("auth_big", draw_icon(color, P(56))))
        titles = {"login": "Zaloguj się", "register": "Załóż konto", "verify": "Potwierdź e-mail",
                  "pending": "Czekasz na akceptację", "forgot": "Reset hasła",
                  "reset": "Nowe hasło", "device_limit": "Limit urządzeń"}
        t1 = titles[view]
        head.create_text(P(self.W / 2), P(132), text=t1, fill=T["text"], anchor="center",
                         font=F(14, True))

        # karta: ramka z polami na zaokraglonym tle (tlo dorysowane po ulozeniu pol)
        holder = tk.Canvas(a, width=P(self.W), height=P(100), bg=T["bg"], highlightthickness=0, bd=0)
        holder.pack(fill="x", pady=(P(4), 0))
        card = tk.Frame(holder, bg=T["card"], padx=P(12), pady=P(10))
        holder.create_window(P(22), P(6), window=card, anchor="nw", width=P(self.W - 44))

        def text(txt, color=T["muted"], size=9, pady=(0, P(10))):
            lb = tk.Label(card, text=txt, bg=T["card"], fg=color, font=F(size),
                          anchor="w", justify="left", wraplength=P(self.W - 84))
            lb.pack(fill="x", pady=pady)
            return lb
        if msg:
            text(msg, COLORS["error"])
        self._auth_status = text(info, T["muted"], pady=(0, P(6)))

        entries = []

        def field(label, var=None, secret=False):
            tk.Label(card, text=label.upper(), bg=T["card"], fg=T["dim"], anchor="w",
                     font=F(7, True)).pack(fill="x")
            var = var or tk.StringVar()
            box, e = self.entry_box(card, var, secret)
            box.pack(fill="x", pady=(P(3), P(9)))
            entries.append(e)
            self._auth_widgets.append(e)
            return var

        def remember_box():
            sw = self.switch(card, self._auth_remember, "Zapamiętaj mnie na tym komputerze",
                             T["card"])
            sw.pack(fill="x", pady=(0, P(9)))
            self._auth_widgets.append(sw)

        def primary(label, cmd):
            b = self.pill(card, self.W - 68, 42, label, cmd)
            b.pack(pady=(P(4), P(2)))
            self._auth_widgets.append(b)
            self._auth_primary = (b, label)
            for e in entries:
                e.bind("<Return>", lambda _e: b.invoke())

        links = tk.Frame(a, bg=T["bg"])
        links.pack(fill="x", padx=P(20), pady=(P(14), 0))

        def link(label, cmd, side="left"):
            lb = tk.Label(links, text=label, bg=T["bg"], fg=T["muted"], cursor="hand2",
                          font=F(9))
            lb.pack(side=side)
            lb.bind("<Enter>", lambda e: lb.configure(fg=T["accent_hi"]))
            lb.bind("<Leave>", lambda e: lb.configure(fg=T["muted"]))
            lb.bind("<Button-1>", lambda e: None if self.app.auth_busy else cmd())

        app = self.app
        email = self._auth_email
        remember = self._auth_remember
        if view == "login":
            field("E-mail", email)
            pw = field("Hasło", secret=True)
            remember_box()
            primary("Zaloguj się", lambda: app.auth_login(email.get(), pw.get(), remember.get()))
            link("Załóż konto", lambda: self.auth_view("register"))
            link("Nie pamiętam hasła", lambda: self.auth_view("forgot"), side="right")
        elif view == "register":
            field("E-mail", email)
            pw = field("Hasło", secret=True)
            pw2 = field("Powtórz hasło", secret=True)
            remember_box()
            primary("Załóż konto", lambda: app.auth_register(email.get(), pw.get(), pw2.get(),
                                                             remember.get()))
            link("Masz już konto? Zaloguj się", lambda: self.auth_view("login"))
            link("Prywatność", open_privacy, side="right")
        elif view == "verify":
            text(f"Kod wysłaliśmy na {email.get()}")
            code = field("Kod z maila")
            primary("Potwierdź", lambda: app.auth_verify(email.get(), code.get()))
            link("Wyślij kod ponownie", lambda: app.auth_resend(email.get()))
            link("Wróć", lambda: self.auth_view("login"), side="right")
        elif view == "pending":
            text("Po akceptacji połączymy się automatycznie.")
            primary("Sprawdź teraz", lambda: app.check_account_async("guest", manual=True))
            link("Wyloguj", app.logout_async)
        elif view == "forgot":
            field("E-mail", email)
            primary("Wyślij kod", lambda: app.auth_forgot(email.get()))
            link("Wróć", lambda: self.auth_view("login"))
        elif view == "reset":
            text(f"Kod wysłaliśmy na {email.get()}")
            code = field("Kod z maila")
            pw = field("Nowe hasło", secret=True)
            pw2 = field("Powtórz hasło", secret=True)
            primary("Ustaw hasło i zaloguj", lambda: app.auth_reset(
                email.get(), code.get(), pw.get(), pw2.get(), remember.get()))
            link("Wyślij kod ponownie", lambda: app.auth_forgot(email.get(), stay=True))
            link("Wróć", lambda: self.auth_view("login"), side="right")
        elif view == "device_limit":
            names = ", ".join(kw.get("devices") or []) or "–"
            text(f"Zalogowane: {names}", T["text"])
            pw = kw.get("password", "")
            primary("Wyloguj je i zaloguj tutaj", lambda: app.auth_login(
                email.get(), pw, remember.get(), logout_others=True))
            link("Wróć", lambda: self.auth_view("login"))
        # pusty wiersz statusu chowamy (wraca w auth_info na swoje miejsce)
        slaves = card.pack_slaves()
        i = slaves.index(self._auth_status)
        self._auth_anchor = slaves[i + 1] if i + 1 < len(slaves) else None
        if not info:
            self._auth_status.pack_forget()
        self._auth_holder, self._auth_card = holder, card
        self._auth_fit()
        self.auth_set_busy(app.auth_busy)
        empty = [e for e in entries if not e.get()]
        if empty or entries:
            (empty or entries)[0].focus_set()

    def auth_set_busy(self, busy, info=None):
        """Blokuje formularz na czas zapytania; info - tekst statusu nad przyciskiem."""
        if self._auth is None or self.auth_current is None:
            return
        for w in self._auth_widgets:
            if not w.winfo_exists():
                continue
            if hasattr(w, "set_enabled"):
                w.set_enabled(not busy)
            else:
                w.configure(state="disabled" if busy else "normal")
        if self._auth_primary:
            b, label = self._auth_primary
            if b.winfo_exists():
                b.set_label("Proszę czekać…" if busy else label)
        if info is not None:
            self.auth_info(info)

    def auth_info(self, text, error=False):
        st = self._auth_status
        if self.auth_current is None or not st.winfo_exists():
            return
        st.configure(text=text, fg=COLORS["error"] if error else T["muted"])
        if not text:
            st.pack_forget()
        elif not st.winfo_ismapped():
            kw = {"before": self._auth_anchor} if self._auth_anchor is not None else {}
            st.pack(fill="x", pady=(0, self.px(6)), **kw)
        self._auth_fit()

    def _auth_fit(self):
        """Zaokraglone tlo karty dopasowane do aktualnej zawartosci."""
        holder, card = self._auth_holder, self._auth_card
        card.update_idletasks()
        hh = card.winfo_reqheight() + self.px(12)
        if getattr(holder, "_fit_h", None) == hh:
            return
        holder._fit_h = hh
        holder.configure(height=hh)
        holder.delete("cardbg")
        r = self.px(14) * 3
        bgimg = self.render(self.W - 32, hh / self.s, lambda d, W, H, _: d.rounded_rectangle(
            [0, 0, W - 1, H - 1], radius=r, fill=rgb(T["card"]), outline=rgb(T["line"]), width=3))
        holder.tag_lower(holder.create_image(self.px(16), 0, image=self.photo("auth_card", bgimg),
                                             anchor="nw", tags="cardbg"))

    def hide_auth(self):
        if self._auth is not None:
            self._auth.place_forget()
        self.auth_current = None

    # -- logi --
    def show_logs(self):
        tk = self.tk
        if self._logwin is not None and self._logwin.winfo_exists():
            self._logwin.deiconify()
            self._logwin.lift()
            return
        w = tk.Toplevel(self.root)
        w.title(f"{APP_NAME} – logi")
        w.configure(bg=T["bg"])
        w.geometry(f"{self.px(680)}x{self.px(400)}")
        txt = tk.Text(w, bg=T["card"], fg=T["text"], relief="flat", font=F(9),
                      wrap="none", padx=10, pady=8, highlightthickness=0, bd=0,
                      insertbackground=T["text"], selectbackground=T["line"])
        txt.pack(fill="both", expand=True, padx=self.px(10), pady=self.px(10))
        txt.configure(state="disabled")
        self._logwin, self._logtxt, self._logsig = w, txt, None
        w.protocol("WM_DELETE_WINDOW", self._close_logs)
        w.after(30, lambda: dark_titlebar(w))  # po wyswietleniu okna
        self._refresh_logs()

    def _close_logs(self):
        self._logwin.destroy()
        self._logwin = None

    def _refresh_logs(self):
        if self._logwin is None:
            return
        evs = list(_events)
        sig = (len(evs), evs[-1] if evs else "")
        if sig == self._logsig:
            return
        self._logsig = sig
        t = self._logtxt
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.insert("end", "\n".join(evs))
        t.see("end")
        t.configure(state="disabled")

    # -- pokazywanie --
    def show(self):
        root = self.root
        if not self._placed:
            # jak w klientach VPN: w prawym dolnym rogu, nad zegarem
            rect = ctypes.wintypes.RECT()
            ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0)
            w, h = self.px(self.W), self.px(self.H)
            x = rect.right - w - self.px(24)
            y = max(rect.top, rect.bottom - h - self.px(56))
            root.geometry(f"+{x}+{y}")
            self._placed = True
        root.deiconify()
        if not self._dark:
            dark_titlebar(root)
            self._dark = True
        root.lift()
        root.attributes("-topmost", True)
        root.after(250, lambda: root.attributes("-topmost", False))
        root.focus_force()
        self.visible = True
        self.refresh()

    def hide(self):
        self.root.withdraw()
        self.visible = False
        if not self._hint:
            self._hint = True
            self.app.notify("Aplikacja działa dalej – ikona jest w zasobniku obok zegara.")


# ----------------------------------------------------------------- app ---

class App:
    def __init__(self):
        self.s = load_settings()
        self.quitting = False
        self.want_connected = False
        self.busy = False
        self.last_error = ""
        self.ui = queue.Queue()
        self.ech = EchService(self.s)
        self.cores = Cores(self.s, self.ech)
        self.monitor = Monitor(self)
        self.icon = None
        self.gui = None
        self._icon_cache = {}
        self._shown_status = None
        self._autostart = None
        # konto
        self.emb = None              # dane wbudowane (wejscie gościa, certyfikat API)
        self.guest_prof = None
        self.session = None          # {email, token, status, profile, device}
        self.remember = False
        self.auth_busy = False       # zapytanie z ekranu logowania w toku
        self._checking = False       # sprawdzanie statusu konta w toku
        self._last_check = 0.0
        self._last_guest_check = 0.0
        self._pending_login = None   # (email, haslo, zapamietaj) - po kodzie z maila

    # -- sterowanie --
    def connect_async(self):
        self.want_connected = True
        threading.Thread(target=self._connect, daemon=True).start()

    def _connect(self):
        if self.busy:
            return
        self.busy = True
        self.last_error = ""
        self.monitor.reset()
        self.monitor.status = "connecting"
        try:
            self.cores.start()
        except Exception as e:
            log(f"BLAD przy uruchamianiu: {e}")
            self.last_error = str(e)
            self.cores.stop()
            self.want_connected = False
            self.monitor.status = "error"
            self.notify(f"Nie udało się połączyć: {e}")
        finally:
            self.busy = False

    def disconnect(self):
        self.want_connected = False
        threading.Thread(target=self._disconnect, daemon=True).start()

    def _disconnect(self):
        self.busy = True
        try:
            self.cores.stop()
            log("Rozlaczono")
        finally:
            self.busy = False
            self.monitor.reset()
            self.monitor.status = "off"

    def toggle(self):
        if self.busy:
            return
        if self.want_connected:
            self.disconnect()
        elif not isinstance(self.cores.profile, Profile):
            self.ui.put("show")  # ekran logowania / oczekiwania
        else:
            self.connect_async()

    # -- konto --
    def _init_account(self):
        try:
            self.emb = embedded()
            self.guest_prof = guest_profile(self.emb)
            self.cores.remote_api_port = int(self.emb.get("api_port", 10910))
            self.ech.set_domains([self.guest_prof.sni])
        except Exception as e:
            log(f"BLAD: brak danych serwera w aplikacji ({e})")
            self.emb = self.guest_prof = None
        self.session = load_session()
        self.remember = self.session is not None
        self._apply_profile()

    def _apply_profile(self) -> bool:
        """Profil z sesji -> rdzenie. Zwraca True, gdy link polaczenia sie zmienil."""
        sess = self.session or {}
        p = None
        if sess.get("status") == "active" and sess.get("profile"):
            try:
                p = Profile(sess["profile"])
            except ValueError as e:
                log(f"Zly profil od serwera: {e}")
        changed = getattr(self.cores.profile, "link", None) != getattr(p, "link", None)
        self.cores.set_profile(p)
        return changed

    def _set_session(self, sess, remember=None) -> bool:
        if remember is not None:
            self.remember = remember
        self.session = sess
        save_session(sess if sess and self.remember else None)
        return self._apply_profile()

    def api(self, method, path, body=None, token=None, via="auto", timeout=25):
        """Zapytanie do API kont. via: 'main' (tunel VPN), 'guest' (wejscie gościa),
        'auto' (tunel, jesli dziala). Rzuca NetError albo ApiError."""
        if not self.emb:
            raise NetError("brak danych serwera w aplikacji – pobierz nową wersję")
        if via == "auto":
            via = "main" if self.cores.running() else "guest"
        if via == "main":
            return api_call(self.s["api_port"], self.emb["api_cert"], method, path, body, token,
                            timeout)
        if self.guest_prof.ech and not self.ech.key:
            self.ech.refresh_now()
            for _ in range(60):
                if self.ech.key:
                    break
                time.sleep(0.25)
            else:
                raise NetError("brak klucza ECH – sprawdź połączenie z internetem")
        fresh = not self.cores.guest_running()
        try:
            self.cores.start_guest(self.guest_prof)
        except RuntimeError as e:
            raise NetError(str(e))
        try:
            return api_call(self.s["guest_api_port"], self.emb["api_cert"], method, path, body,
                            token, timeout)
        except NetError:
            if not fresh:
                raise
            time.sleep(1.5)  # swiezo uruchomiony xray - jedna powtorka
            return api_call(self.s["guest_api_port"], self.emb["api_cert"], method, path, body,
                            token, timeout)

    def _auth_task(self, fn, info):
        """Akcja z ekranu logowania w tle; bledy trafiaja na ekran."""
        if self.auth_busy:
            return
        self.auth_busy = True
        self.gui.auth_set_busy(True, info)

        def run():
            try:
                fn()
            except NetError as e:
                log(f"Konto: brak polaczenia z serwerem ({e})")
                self.ech.refresh_now()
                self._auth_info(f"Nie mogę połączyć się z serwerem ({e}). Spróbuj za chwilę.", True)
            except ApiError as e:
                self._auth_info(str(e), True)
            except Exception as e:
                log("Konto: " + traceback.format_exc().strip().splitlines()[-1])
                self._auth_info(f"Błąd: {e}", True)
            finally:
                self.auth_busy = False
                self.ui.put(lambda: self.gui.auth_set_busy(False))
        threading.Thread(target=run, daemon=True, name="auth").start()

    def _auth_info(self, text, error=False):
        self.ui.put(lambda: self.gui.auth_info(text, error))

    def _auth_show(self, view, **kw):
        self.ui.put(lambda: self.gui.auth_view(view, **kw))

    def _check_new_password(self, pw, pw2) -> bool:
        if len(pw) < 8:
            self.gui.auth_info("Hasło musi mieć co najmniej 8 znaków.", True)
        elif pw != pw2:
            self.gui.auth_info("Hasła się różnią.", True)
        else:
            return True
        return False

    def _do_login(self, email, password, remember, logout_others=False):
        """W watku tla: logowanie i przejscie dalej (VPN albo oczekiwanie na akceptacje)."""
        try:
            r = self.api("POST", "/v1/login", {
                "email": email, "password": password, "device_id": device_id(),
                "device_name": device_name(), "logout_others": logout_others})
        except ApiError as e:
            if e.code == "unverified":
                self._pending_login = (email, password, remember)
                self._auth_show("verify", email=email,
                                info="Adres nie jest jeszcze potwierdzony. Wpisz kod z maila "
                                     "albo wyślij nowy.")
                return
            if e.code == "device_limit":
                self._auth_show("device_limit", email=email, password=password,
                                devices=e.detail.get("devices"))
                return
            raise
        self._pending_login = None
        sess = {k: r.get(k) for k in ("email", "token", "status", "profile", "device")}
        self._set_session(sess, remember)
        log(f"Zalogowano: {sess['email']} (konto {sess['status']}, urzadzenie {device_name()})")
        self._last_check = time.time()
        if sess["status"] == "active":
            self.ui.put(self._enter_main)
        else:
            self._auth_show("pending", email=sess["email"])

    def _enter_main(self):  # watek okna
        self.gui.hide_auth()
        self.gui.refresh()
        self.connect_async()

    def auth_login(self, email, password, remember, logout_others=False):
        email = email.strip()
        if not email or not password:
            self.gui.auth_info("Podaj e-mail i hasło.", True)
            return
        self._auth_task(lambda: self._do_login(email, password, remember, logout_others),
                        "Łączenie z serwerem…")

    def auth_register(self, email, pw, pw2, remember):
        email = email.strip()
        if not email:
            self.gui.auth_info("Podaj adres e-mail.", True)
            return
        if not self._check_new_password(pw, pw2):
            return

        def fn():
            self.api("POST", "/v1/register", {"email": email, "password": pw})
            self._pending_login = (email, pw, remember)
            log(f"Rejestracja: {email} - czekam na kod z maila")
            self._auth_show("verify", email=email)
        self._auth_task(fn, "Zakładanie konta…")

    def auth_verify(self, email, code):
        email = email.strip()
        if not code.strip():
            self.gui.auth_info("Wpisz kod z maila.", True)
            return

        def fn():
            self.api("POST", "/v1/verify", {"email": email, "code": code.strip()})
            pl = self._pending_login
            if pl and pl[0].lower() == email.lower():
                self._do_login(*pl)
            else:
                self._auth_show("login", email=email, info="Adres potwierdzony – zaloguj się.")
        self._auth_task(fn, "Sprawdzanie kodu…")

    def auth_resend(self, email):
        def fn():
            self.api("POST", "/v1/resend", {"email": email.strip()})
            self._auth_info("Wysłaliśmy nowy kod. Sprawdź też folder Spam.")
        self._auth_task(fn, "Wysyłanie kodu…")

    def auth_forgot(self, email, stay=False):
        email = email.strip()
        if not email:
            self.gui.auth_info("Podaj adres e-mail.", True)
            return

        def fn():
            self.api("POST", "/v1/password/forgot", {"email": email})
            if stay:
                self._auth_info("Wysłaliśmy nowy kod.")
            else:
                self._auth_show("reset", email=email)
        self._auth_task(fn, "Wysyłanie kodu…")

    def auth_reset(self, email, code, pw, pw2, remember):
        email = email.strip()
        if not code.strip():
            self.gui.auth_info("Wpisz kod z maila.", True)
            return
        if not self._check_new_password(pw, pw2):
            return

        def fn():
            self.api("POST", "/v1/password/reset", {"email": email, "code": code.strip(),
                                                    "password": pw})
            log(f"Ustawiono nowe haslo dla {email}")
            self._do_login(email, pw, remember)
        self._auth_task(fn, "Ustawianie hasła…")

    def logout_async(self):
        sess = self.session
        if not sess:
            return

        def run():
            try:
                self.api("POST", "/v1/logout", token=sess["token"], timeout=8)
            except (NetError, ApiError) as e:
                log(f"Wylogowanie na serwerze nieudane ({e}) - wylogowuje tylko tutaj")
            self._drop_session("Wylogowano.", error=False)
        self.want_connected = False
        threading.Thread(target=run, daemon=True).start()

    def _drop_session(self, msg, error=True):
        """Koniec sesji (wylogowanie, blokada, odwolane urzadzenie): rozlacz i pokaz logowanie."""
        self.want_connected = False
        if self.cores.running():
            self._disconnect()
        email = (self.session or {}).get("email", "")
        self._set_session(None)
        log(f"Koniec sesji: {msg}")

        def show():
            self.gui.show()
            self.gui.auth_view("login", email=email)
            self.gui.auth_info(msg, error)
        self.ui.put(show)

    def check_account_async(self, via, manual=False):
        """Status konta z serwera (akceptacja, blokada, zmiana profilu)."""
        sess = self.session
        if not sess or self._checking:
            return
        self._checking = True
        if manual:
            self.auth_busy = True
            self.gui.auth_set_busy(True, "Sprawdzanie…")

        def run():
            r = None
            try:
                r = self.api("GET", "/v1/me", token=sess["token"], via=via, timeout=20)
            except ApiError as e:
                if e.code in ("session", "blocked"):
                    self._drop_session(str(e))
                elif manual:
                    self._auth_info(str(e), True)
            except NetError as e:
                log(f"Konto: nie sprawdzilem statusu ({e})")
                if manual:
                    self._auth_info("Nie mogę połączyć się z serwerem. Spróbuj za chwilę.", True)
            finally:
                self._checking = False
                if manual:
                    self.auth_busy = False
                    self.ui.put(lambda: self.gui.auth_set_busy(False))
            if r is not None:
                self._account_update(sess, r, manual)
        threading.Thread(target=run, daemon=True, name="account").start()

    def _account_update(self, sess, r, manual):
        if self.session is not sess:
            return  # w miedzyczasie wylogowano / zalogowano ponownie
        was = sess.get("status")
        new = dict(sess, email=r["email"], status=r["status"], profile=r.get("profile"),
                   device=r.get("device"))
        changed = self._set_session(new)
        if r["status"] == "active" and was != "active":
            log("Konto zaakceptowane przez administratora")
            self.notify("Konto zaakceptowane – łączę z VPN-em.")
            self.ui.put(self._enter_main)
        elif r["status"] == "active" and changed and self.want_connected:
            log("Serwer zmienil dane polaczenia - lacze ponownie")
            self.connect_async()
        elif r["status"] != "active" and manual:
            self._auth_info("Konto nadal czeka na akceptację.")

    def account_tick(self):
        """Co sekunde (z Monitor): kiedy pytac serwer o status konta."""
        sess = self.session
        if not sess or self._checking or self.auth_busy:
            return
        t = time.time()
        if sess.get("status") != "active":
            if t - self._last_check >= 60:      # czeka na akceptacje
                self._last_check = t
                self.check_account_async("guest")
            return
        st = self.monitor.status
        if st == "on":
            if self.cores.guest_running():
                self.cores.stop_guest()         # gosc juz niepotrzebny
            if t - self._last_check >= 1800:    # po polaczeniu i co 30 min - przez tunel
                self._last_check = t
                self.check_account_async("main")
        elif st == "problem" and self.ech.key and t - self._last_guest_check >= 120:
            # tunel nie dziala mimo klucza ECH - moze urzadzenie odwolano albo konto zablokowano
            self._last_guest_check = t
            self.check_account_async("guest")

    def open_logs(self):
        subprocess.Popen(["explorer", str(DATA_DIR)])

    def notify(self, text):
        try:
            if self.icon and self.icon.HAS_NOTIFICATION:
                self.icon.notify(text, APP_NAME)
        except Exception:
            pass

    # -- autostart (wyzwalacz zadania harmonogramu) --
    def autostart_enabled(self):
        if self._autostart is None:
            out = run_ps(f"(Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue)"
                         ".Triggers.Count")
            self._autostart = out.strip() not in ("", "0")
        return self._autostart

    def toggle_autostart(self):
        want = not self.autostart_enabled()
        trig = "@(New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME)" if want else "@()"
        run_ps(f"$t=Get-ScheduledTask -TaskName '{TASK_NAME}'; $t.Triggers={trig}; "
               "Set-ScheduledTask -InputObject $t | Out-Null")
        self._autostart = None
        ok = self.autostart_enabled() == want
        log(("Autostart wlaczony" if want else "Autostart wylaczony") if ok
            else "Nie udalo sie zmienic autostartu")

    def toggle_option(self, key):
        """Przelaczniki z zakladki Konto."""
        if key == "autostart":
            threading.Thread(target=self.toggle_autostart, daemon=True).start()
            return
        self.s[key] = not self.s.get(key)
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        saved[key] = self.s[key]
        try:
            SETTINGS_FILE.write_text(json.dumps(saved, indent=2), encoding="utf-8")
        except OSError as e:
            log(f"Nie zapisalem ustawien: {e}")

    def quit(self):
        if self.quitting:
            return
        self.quitting = True
        log("Zamykanie")
        self.cores.stop()
        self.cores.stop_guest()
        self.ech.stop()
        if self.icon:
            self.icon.stop()
        if self.gui:
            self.gui.root.after(0, self.gui.root.destroy)

    # -- serwer sterowania (drugie uruchomienie -> "show") --
    def control_server(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", self.s["control_port"]))
        srv.listen(5)
        while not self.quitting:
            try:
                c, _ = srv.accept()
                with c:
                    c.settimeout(2)
                    cmd = c.recv(64).decode(errors="ignore").strip()
                    if cmd in ("show", "quit", "connect", "disconnect"):
                        self.ui.put(cmd)
            except OSError:
                time.sleep(0.5)

    # -- zasobnik --
    def _icon_img(self, status):
        if status not in self._icon_cache:
            self._icon_cache[status] = draw_icon(COLORS[status])
        return self._icon_cache[status]

    def build_tray(self):
        import pystray
        M = pystray.MenuItem
        put = self.ui.put
        menu = pystray.Menu(
            M(f"Otwórz {APP_NAME}", lambda: put("show"), default=True),
            M(lambda _: "Rozłącz" if self.want_connected else "Połącz", lambda: self.toggle()),
            M("Odśwież klucz ECH", lambda: self.ech.refresh_now()),
            pystray.Menu.SEPARATOR,
            M("Konto…", lambda: put("account")),
            M("Folder z danymi", lambda: self.open_logs()),
            M("Uruchamiaj przy logowaniu", lambda: self.toggle_autostart(),
              checked=lambda _: bool(self._autostart)),
            pystray.Menu.SEPARATOR,
            M("Zakończ", lambda: put("quit")),
        )
        self.icon = pystray.Icon(TASK_NAME, self._icon_img("off"), APP_NAME, menu)
        self.icon.run_detached()

    def tooltip(self):
        st = self.monitor.status
        t = f"{APP_NAME} – {STATUS_TEXT[st]}"
        if st == "on" and self.monitor.ping_ms is not None:
            t += f", {self.monitor.ping_ms} ms"
        return t[:127]

    def pump(self):
        try:
            while True:
                cmd = self.ui.get_nowait()
                if callable(cmd):  # zadanie z watku tla dla okna
                    cmd()
                elif cmd == "show":
                    self.gui.show()
                elif cmd == "account":
                    self.gui.account_dialog()
                elif cmd == "quit":
                    self.quit()
                    return
                elif cmd == "connect":
                    self.connect_async()
                elif cmd == "disconnect":
                    self.disconnect()
        except queue.Empty:
            pass
        st = self.monitor.status
        if self.icon and st != self._shown_status:
            self._shown_status = st
            self.icon.icon = self._icon_img(st)
        if self.icon:
            tip = self.tooltip()
            if self.icon.title != tip:
                self.icon.title = tip
        if self.gui.visible:
            self.gui.refresh()
        self.gui.root.after(500, self.pump)

    def run(self, show=False):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        log(f"Start {APP_NAME}")
        if not ICON_FILE.exists():
            make_ico()
        threading.Thread(target=self.control_server, daemon=True).start()
        threading.Thread(target=ensure_task, daemon=True).start()
        threading.Thread(target=self.autostart_enabled, daemon=True).start()
        threading.Thread(target=ensure_shortcuts, daemon=True).start()
        self._init_account()
        self.ech.start()
        self.gui = Gui(self)
        self.build_tray()
        threading.Thread(target=self.monitor.loop, daemon=True).start()
        sess = self.session
        if not sess:
            self.gui.auth_view("login")
            show = True  # bez konta nic nie zadziala - okno zawsze
        elif sess.get("status") != "active":
            self.gui.auth_view("pending", email=sess["email"])
        elif self.s.get("auto_connect"):
            self.connect_async()
        if show:
            self.ui.put("show")
        self.gui.root.after(200, self.pump)
        self.gui.root.mainloop()


# ---------------------------------------------------- uruchamianie / UAC ---

def task_command():
    """(program, argumenty) do zadania harmonogramu."""
    if FROZEN:
        return sys.executable, "--task"
    pyw = Path(sys.executable).with_name("pythonw.exe")
    return str(pyw), f'"{Path(__file__).resolve()}" --task'


def ensure_task():
    """Rejestruje (albo poprawia) zadanie, ktore uruchamia aplikacje jako
    administrator bez okna UAC. Wywolywane z uprawnieniami administratora."""
    exe, args = task_command()

    def q(v):
        return str(v).replace("'", "''")
    ps = f"""
$exe='{q(exe)}'; $arg='{q(args)}'; $dir='{q(APP_DIR)}'; $name='{TASK_NAME}'
$old = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($old -and $old.Actions[0].Execute -eq $exe -and $old.Actions[0].Arguments -eq $arg) {{ 'ok'; return }}
$a = New-ScheduledTaskAction -Execute $exe -Argument $arg -WorkingDirectory $dir
$p = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Highest
$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$t = New-ScheduledTask -Action $a -Principal $p -Settings $s
if ($old) {{ if ($old.Triggers) {{ $t.Triggers = $old.Triggers }}; Unregister-ScheduledTask -TaskName $name -Confirm:$false }}
Register-ScheduledTask -TaskName $name -InputObject $t | Out-Null
'registered'
"""
    try:
        out = run_ps(ps, 60).strip()
    except Exception as e:
        out = str(e)
    if "registered" in out:
        log("Zarejestrowano zadanie harmonogramu - kolejne uruchomienia bez pytania UAC")
        # po zmianie nazwy: stare zadanie wskazuje na nieistniejacy AkademikVPN.exe
        run_ps(f"Unregister-ScheduledTask -TaskName '{OLD_NAMES[0]}' -Confirm:$false "
               "-ErrorAction SilentlyContinue")
    elif not out.endswith("ok"):
        log(f"Nie zarejestrowalem zadania harmonogramu: {out[-200:]}")


def ensure_shortcuts():
    """Przy pierwszym uruchomieniu .exe: skrot na pulpicie i w menu Start."""
    marker = DATA_DIR / ".skroty-obfuskator"
    if not FROZEN or marker.exists():
        return

    def q(v):
        return str(v).replace("'", "''")
    ps = f"""
$sh = New-Object -ComObject WScript.Shell
foreach ($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {{
  Remove-Item -LiteralPath (Join-Path $d '{OLD_NAMES[1]}.lnk') -ErrorAction SilentlyContinue
  $l = $sh.CreateShortcut((Join-Path $d '{APP_NAME}.lnk'))
  $l.TargetPath = '{q(sys.executable)}'; $l.WorkingDirectory = '{q(APP_DIR)}'
  $l.IconLocation = '{q(sys.executable)},0'; $l.Description = '{APP_NAME}'; $l.Save()
}}
'ok'"""
    try:
        if run_ps(ps).strip().endswith("ok"):
            marker.write_text("1", encoding="utf-8")
            log("Utworzono skroty na pulpicie i w menu Start")
    except Exception as e:
        log(f"Nie utworzylem skrotow: {e}")


def already_running(port, cmd="show") -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1) as c:
            c.sendall(cmd.encode() + b"\n")
        return True
    except OSError:
        return False


def main():
    if "--make-icon" in sys.argv:
        make_ico()
        return
    if "--selftest" in sys.argv:  # sprawdzenie spakowanego exe bez uruchamiania VPN
        import tkinter
        import pystray
        from PIL import ImageTk
        root = tkinter.Tk()
        ImageTk.PhotoImage(draw_icon(COLORS["on"]))
        root.destroy()
        load_fonts()
        if FONT != "PT Root UI VF":
            raise SystemExit(f"brak czcionki w {FONT_DIR}")
        emb = embedded()
        guest_profile(emb)
        ssl.create_default_context(cadata=emb["api_cert"])
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "selftest.txt").write_text(f"ok {pystray.Icon.__module__} wbudowane {FONT}",
                                               encoding="utf-8")
        return
    settings = load_settings()
    port = settings["control_port"]
    if already_running(port):
        return  # juz dziala - pokazalismy okno
    if not is_admin():
        # zwykle klikniecie (skrot, pasek zadan): start przez zadanie harmonogramu
        r = subprocess.run(["schtasks", "/run", "/tn", TASK_NAME], capture_output=True,
                           creationflags=CREATE_NO_WINDOW)
        if r.returncode == 0:
            for _ in range(60):
                time.sleep(0.5)
                if already_running(port):
                    return
            return
        # pierwsze uruchomienie - jednorazowo UAC, potem aplikacja zarejestruje zadanie
        exe, args = task_command()
        args = args.replace("--task", "--show")
        ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, args, str(APP_DIR), 1)
        return
    app = App()
    try:
        app.run(show="--task" not in sys.argv or "--show" in sys.argv)
    except Exception:
        log("BLAD krytyczny:\n" + traceback.format_exc())
        app.cores.stop()
        raise


if __name__ == "__main__":
    main()
