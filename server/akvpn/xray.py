"""Uzytkownicy Xray bez restartu - przez `xray api` (HandlerService / StatsService).

`xray api` zwraca kod 0 takze przy bledach pojedynczych uzytkownikow,
wiec wynik czytamy z tekstu ("Added N user(s)", "already exists")."""
import json
import os
import re
import subprocess
import tempfile


class XrayError(RuntimeError):
    pass


class Xray:
    def __init__(self, cfg: dict):
        self.bin = cfg["xray_bin"]
        self.server = cfg["xray_api"]
        self.tag = cfg["vpn_inbound_tag"]
        self.port = int(cfg.get("vpn_inbound_port", 10900))

    def _run(self, cmd, *args) -> str:
        # flagi przed argumentami pozycyjnymi - po pierwszym pliku/e-mailu xray ich nie czyta
        args = (cmd,) + args
        try:
            r = subprocess.run([self.bin, "api", cmd, f"-s={self.server}", *args[1:]],
                               capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise XrayError(f"xray api {args[0]}: {e}")
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 0 or "failed to dial" in out:
            raise XrayError(f"xray api {args[0]}: {out.strip()[-300:]}")
        return out

    def users(self) -> dict:
        """{email: uuid} uzytkownikow wejscia VPN (bez starych klientow bez e-maila)."""
        out = self._run("inbounduser", f"-tag={self.tag}")
        start = out.find("{")
        data = json.loads(out[start:]) if start >= 0 else {}
        return {u["email"]: u.get("account", {}).get("id", "")
                for u in data.get("users", []) if u.get("email")}

    def add(self, users: dict) -> int:
        """users: {email: uuid}. Zwraca liczbe dodanych."""
        if not users:
            return 0
        conf = {"inbounds": [{"tag": self.tag, "port": self.port, "protocol": "vless",
                              "settings": {"decryption": "none", "clients": [
                                  {"id": u, "email": e} for e, u in users.items()]}}]}
        fd, path = tempfile.mkstemp(suffix=".json", prefix="akvpn-adu-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(conf, f)
            out = self._run("adu", path)
        finally:
            os.unlink(path)
        if "failed to build config" in out:
            raise XrayError(out.strip()[-300:])
        m = re.search(r"Added (\d+) user", out)
        return int(m.group(1)) if m else 0

    def remove(self, emails) -> int:
        emails = list(emails)
        if not emails:
            return 0
        out = self._run("rmu", f"-tag={self.tag}", *emails)
        m = re.search(r"Removed (\d+) user", out)
        return int(m.group(1)) if m else 0

    def take_stats(self) -> dict:
        """{email: [up, down]} od ostatniego odczytu (liczniki zerowane)."""
        out = self._run("statsquery", "-pattern", "user>>>", "-reset")
        start = out.find("{")
        data = json.loads(out[start:]) if start >= 0 else {}
        res = {}
        for st in data.get("stat", []):
            parts = st.get("name", "").split(">>>")
            if len(parts) == 4 and parts[0] == "user" and parts[2] == "traffic":
                v = int(st.get("value", 0) or 0)
                pair = res.setdefault(parts[1], [0, 0])
                pair[0 if parts[3] == "uplink" else 1] += v
        return res
