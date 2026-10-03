"""Logika kont - wspolna dla API (akvpn-api) i CLI admina (akvpn-admin)."""
import hashlib
import hmac
import logging
import re
import secrets
import threading
import uuid as uuidlib

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from .db import Database, now
from .mailer import MailError

log = logging.getLogger("akvpn")

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s.]{2,}$")
DEVICE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
DEV_EMAIL_RE = re.compile(r"^dev(\d+)@")


class ApiError(Exception):
    """Blad dla aplikacji: kod HTTP, kod maszynowy i komunikat po polsku."""

    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra

    def as_dict(self):
        return {"code": self.code, "message": self.message, **self.extra}


def _minutes(sec: float) -> str:
    m = max(1, int(sec // 60) + (1 if sec % 60 else 0))
    return f"{m} min"


class Service:
    def __init__(self, cfg: dict, db: Database, xray, mailer):
        self.cfg, self.db, self.xray, self.mailer = cfg, db, xray, mailer
        # parametry OWASP dla argon2id - ok. 19 MB RAM na jedno hashowanie
        self.ph = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
        self._dummy = self.ph.hash(secrets.token_hex(16))
        self._xlock = threading.Lock()
        self.domain = cfg["device_email_domain"]

    # ------------------------------------------------------------ pomocnicze
    @staticmethod
    def norm_email(email: str) -> str:
        email = (email or "").strip().lower()
        if len(email) > 254 or not EMAIL_RE.match(email):
            raise ApiError(400, "bad_email", "Podaj poprawny adres e-mail.")
        return email

    def check_password(self, pw: str):
        if len(pw or "") < self.cfg["password_min"]:
            raise ApiError(400, "weak_password",
                           f"Hasło musi mieć co najmniej {self.cfg['password_min']} znaków.")
        if len(pw) > 256:
            raise ApiError(400, "weak_password", "Hasło jest za długie.")

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _code_hash(email, purpose, code) -> str:
        return hashlib.sha256(f"{purpose}:{email}:{code}".encode()).hexdigest()

    def dev_email(self, dev_id: int) -> str:
        return f"dev{dev_id}@{self.domain}"

    def _limited(self, c, name: str, key: str, record=True):
        """Zwraca ApiError(429), gdy przekroczono limit z configu, inaczej None.
        Bledu nie rzucamy w srodku transakcji - wycofalby zapis proby."""
        mx, window = self.cfg[name]
        t = now()
        row = c.execute("SELECT COUNT(*), MIN(ts) FROM hits WHERE kind=? AND key=? AND ts>?",
                        (name, key, t - window)).fetchone()
        if row[0] >= mx:
            wait = row[1] + window - t
            return ApiError(429, "rate_limited",
                            f"Za dużo prób. Spróbuj ponownie za {_minutes(wait)}.",
                            retry_after=int(wait) + 1)
        if record:
            c.execute("INSERT INTO hits (kind, key, ts) VALUES (?, ?, ?)", (name, key, t))
        return None

    def _new_code(self, c, email: str, purpose: str):
        """Nowy 6-cyfrowy kod (zapisany jako hash). Zwraca (kod, blad)."""
        for name, key in (("limit_mail_per_email", email), ("limit_mail_global", "*")):
            err = self._limited(c, name, key)
            if err:
                err.message = "Wysłaliśmy już kilka maili. " + err.message
                return None, err
        code = f"{secrets.randbelow(10 ** 6):06d}"
        c.execute("INSERT OR REPLACE INTO codes (email, purpose, code_hash, expires_at, attempts) "
                  "VALUES (?, ?, ?, ?, 0)",
                  (email, purpose, self._code_hash(email, purpose, code),
                   now() + self.cfg["code_ttl_min"] * 60))
        return code, None

    def _use_code(self, c, email: str, purpose: str, code: str):
        """None, gdy kod sie zgadza (i zostaje zuzyty), inaczej ApiError."""
        err = self._limited(c, "limit_code_global", "*")
        if err:
            return err
        row = c.execute("SELECT * FROM codes WHERE email=? AND purpose=?",
                        (email, purpose)).fetchone()
        if not row or row["expires_at"] < now():
            return ApiError(400, "code_invalid", "Kod wygasł albo jest błędny. Wyślij nowy kod.")
        if row["attempts"] >= self.cfg["code_attempts"]:
            return ApiError(400, "code_invalid", "Za dużo błędnych prób. Wyślij nowy kod.")
        code = re.sub(r"\D", "", code or "")
        if not hmac.compare_digest(row["code_hash"], self._code_hash(email, purpose, code)):
            c.execute("UPDATE codes SET attempts=attempts+1 WHERE email=? AND purpose=?",
                      (email, purpose))
            left = self.cfg["code_attempts"] - row["attempts"] - 1
            return ApiError(400, "code_invalid", f"Zły kod. Pozostałe próby: {left}.")
        c.execute("DELETE FROM codes WHERE email=? AND purpose=?", (email, purpose))
        return None

    def _mail(self, to, subject, body, required=True):
        try:
            self.mailer.send(to, subject, body)
        except MailError as e:
            log.error("Mail do %s nie wyszedl: %s", to, e)
            if required:
                raise ApiError(503, "mail_failed",
                               "Nie udało się wysłać maila. Spróbuj za chwilę.")

    def _mail_code(self, email, code, purpose):
        ttl = self.cfg["code_ttl_min"]
        if purpose == "verify":
            subj, intro = "kod potwierdzenia", "Twój kod potwierdzenia adresu e-mail"
        else:
            subj, intro = "reset hasła", "Twój kod do ustawienia nowego hasła"
        self._mail(email, f"{subj} {code}",
                   f"Cześć!\n\n{intro} w aplikacji Obfuskator VPN:\n\n    {code}\n\n"
                   f"Kod jest ważny {ttl} minut. Jeśli to nie Ty, zignoruj tę wiadomość.\n")

    def _link(self, dev) -> str:
        return (self.cfg["vpn_link_template"].replace("{uuid}", dev["uuid"])
                .replace("{name}", "Obfuskator%20VPN"))

    def _session(self, acc, dev, token=None) -> dict:
        d = {"email": acc["email"], "status": acc["status"],
             "device": {"id": dev["id"], "name": dev["name"]},
             "profile": self._link(dev) if acc["status"] == "active" else None}
        if token:
            d["token"] = token
        return d

    # ------------------------------------------------------------- API
    def register(self, email: str, password: str) -> dict:
        email = self.norm_email(email)
        self.check_password(password)
        pw_hash = self.ph.hash(password)
        with self.db.tx() as c:
            err = self._limited(c, "limit_register_global", "*")
            acc = c.execute("SELECT * FROM accounts WHERE email=?", (email,)).fetchone()
            if not err and acc and acc["status"] != "unverified":
                err = ApiError(409, "exists", "Konto z tym adresem już istnieje. Zaloguj się.")
            code = None
            if not err:
                if acc:  # ponowna rejestracja niepotwierdzonego - nowe haslo i nowy kod
                    c.execute("UPDATE accounts SET pw_hash=?, created_at=? WHERE id=?",
                              (pw_hash, now(), acc["id"]))
                else:
                    c.execute("INSERT INTO accounts (email, pw_hash, created_at) VALUES (?, ?, ?)",
                              (email, pw_hash, now()))
                code, err = self._new_code(c, email, "verify")
        if err:
            raise err
        self._mail_code(email, code, "verify")
        log.info("Rejestracja: %s", email)
        return {"status": "unverified"}

    def resend(self, email: str) -> dict:
        email = self.norm_email(email)
        code = None
        with self.db.tx() as c:
            acc = c.execute("SELECT status FROM accounts WHERE email=?", (email,)).fetchone()
            err = None
            if acc and acc["status"] == "unverified":
                code, err = self._new_code(c, email, "verify")
        if err:
            raise err
        if code:
            self._mail_code(email, code, "verify")
        return {"ok": True}

    def verify(self, email: str, code: str) -> dict:
        email = self.norm_email(email)
        with self.db.tx() as c:
            acc = c.execute("SELECT * FROM accounts WHERE email=?", (email,)).fetchone()
            if acc and acc["status"] != "unverified":
                return {"status": acc["status"]}  # juz potwierdzone (np. drugie klikniecie)
            err = self._use_code(c, email, "verify", code)
            if not err:
                if not acc:
                    err = ApiError(400, "code_invalid", "Kod wygasł. Zarejestruj się ponownie.")
                else:
                    c.execute("UPDATE accounts SET status='pending' WHERE id=?", (acc["id"],))
        if err:
            raise err
        log.info("Potwierdzono e-mail: %s - czeka na akceptacje", email)
        if self.cfg.get("admin_email"):
            self._mail(self.cfg["admin_email"], f"nowe konto {email}",
                       f"Nowe konto czeka na akceptację: {email}\n\n"
                       f"Na serwerze:\n    akvpn-admin approve {email}\n"
                       f"albo:\n    akvpn-admin reject {email}\n", required=False)
        return {"status": "pending"}

    def login(self, email: str, password: str, device_key: str, device_name: str,
              logout_others: bool = False) -> dict:
        email = self.norm_email(email)
        if not DEVICE_KEY_RE.match(device_key or ""):
            raise ApiError(400, "bad_device", "Zły identyfikator urządzenia.")
        name = re.sub(r"[^\w .\-]", "", device_name or "")[:40].strip() or "Komputer"
        with self.db.tx() as c:
            err = (self._limited(c, "limit_login_global", "*")
                   or self._limited(c, "limit_login_fail", email, record=False))
            acc = c.execute("SELECT * FROM accounts WHERE email=?", (email,)).fetchone()
        if err:
            raise err
        try:
            ok = self.ph.verify(acc["pw_hash"] if acc else self._dummy, password or "")
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            ok = False
        if not ok or not acc:
            with self.db.tx() as c:
                self._limited(c, "limit_login_fail", email)
            raise ApiError(401, "bad_login", "Zły e-mail lub hasło.")
        if acc["status"] == "unverified":
            raise ApiError(403, "unverified", "Potwierdź adres e-mail kodem z wiadomości.")
        if acc["status"] == "blocked":
            raise ApiError(403, "blocked", "Konto jest zablokowane. Skontaktuj się z administratorem.")

        token = secrets.token_urlsafe(32)
        th = self._token_hash(token)
        removed_others = False
        with self.db.tx() as c:
            c.execute("DELETE FROM hits WHERE kind='limit_login_fail' AND key=?", (email,))
            if self.ph.check_needs_rehash(acc["pw_hash"]):
                c.execute("UPDATE accounts SET pw_hash=? WHERE id=?",
                          (self.ph.hash(password), acc["id"]))
            if logout_others:
                n = c.execute("DELETE FROM devices WHERE account_id=? AND device_key<>?",
                              (acc["id"], device_key)).rowcount
                removed_others = n > 0
            dev = c.execute("SELECT * FROM devices WHERE account_id=? AND device_key=?",
                            (acc["id"], device_key)).fetchone()
            err = None
            if dev:
                c.execute("UPDATE devices SET token_hash=?, name=?, last_seen=? WHERE id=?",
                          (th, name, now(), dev["id"]))
            else:
                others = c.execute("SELECT name FROM devices WHERE account_id=? ORDER BY last_seen",
                                   (acc["id"],)).fetchall()
                if len(others) >= self.cfg["device_limit"]:
                    err = ApiError(409, "device_limit",
                                   f"Masz już {len(others)} urządzenia (limit "
                                   f"{self.cfg['device_limit']}). Wyloguj jedno z nich albo "
                                   "wyloguj wszystkie pozostałe.",
                                   devices=[o["name"] for o in others])
                else:
                    c.execute("INSERT INTO devices (account_id, device_key, name, uuid, token_hash,"
                              " created_at, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?)",
                              (acc["id"], device_key, name, str(uuidlib.uuid4()), th, now(), now()))
            if not err:
                c.execute("UPDATE accounts SET last_login=? WHERE id=?", (now(), acc["id"]))
                dev = c.execute("SELECT * FROM devices WHERE account_id=? AND device_key=?",
                                (acc["id"], device_key)).fetchone()
        if err:
            raise err
        if acc["status"] == "active" or removed_others:
            self.sync_safe()
        log.info("Logowanie: %s (%s, urzadzenie %s)", email, acc["status"], dev["id"])
        return self._session(acc, dev, token)

    def _by_token(self, c, token: str):
        row = c.execute("SELECT d.*, a.email, a.status FROM devices d JOIN accounts a "
                        "ON a.id=d.account_id WHERE d.token_hash=?",
                        (self._token_hash(token or ""),)).fetchone()
        if not row:
            raise ApiError(401, "session", "Sesja wygasła. Zaloguj się ponownie.")
        return row

    def me(self, token: str) -> dict:
        with self.db.tx() as c:
            row = self._by_token(c, token)
            c.execute("UPDATE devices SET last_seen=? WHERE id=?", (now(), row["id"]))
        if row["status"] == "blocked":
            raise ApiError(403, "blocked", "Konto jest zablokowane. Skontaktuj się z administratorem.")
        return self._session(row, row)

    def logout(self, token: str) -> dict:
        with self.db.tx() as c:
            row = self._by_token(c, token)
            c.execute("DELETE FROM devices WHERE id=?", (row["id"],))
        self.sync_safe()
        log.info("Wylogowanie: %s (urzadzenie %s)", row["email"], row["id"])
        return {"ok": True}

    def forgot(self, email: str) -> dict:
        email = self.norm_email(email)
        code = None
        with self.db.tx() as c:
            acc = c.execute("SELECT status FROM accounts WHERE email=?", (email,)).fetchone()
            err = None
            if acc and acc["status"] in ("pending", "active"):
                code, err = self._new_code(c, email, "reset")
        if err:
            raise err
        if code:
            self._mail_code(email, code, "reset")
        return {"ok": True}  # tak samo dla nieistniejacego konta

    def reset(self, email: str, code: str, password: str) -> dict:
        email = self.norm_email(email)
        self.check_password(password)
        pw_hash = self.ph.hash(password)
        with self.db.tx() as c:
            err = self._use_code(c, email, "reset", code)
            acc = c.execute("SELECT * FROM accounts WHERE email=?", (email,)).fetchone()
            if not err and acc:
                c.execute("UPDATE accounts SET pw_hash=? WHERE id=?", (pw_hash, acc["id"]))
                # nowe haslo = wylogowanie wszedzie (ktos mogl znac stare)
                c.execute("DELETE FROM devices WHERE account_id=?", (acc["id"],))
                c.execute("DELETE FROM hits WHERE kind='limit_login_fail' AND key=?", (email,))
        if err:
            raise err
        self.sync_safe()
        log.info("Reset hasla: %s", email)
        return {"ok": True}

    # ------------------------------------------------------- Xray i sprzatanie
    def sync(self) -> tuple:
        """Doprowadza uzytkownikow Xray do stanu z bazy. Zwraca (dodani, usunieci)."""
        with self._xlock:
            with self.db.tx() as c:
                rows = c.execute("SELECT d.id, d.uuid FROM devices d JOIN accounts a "
                                 "ON a.id=d.account_id WHERE a.status='active'").fetchall()
            want = {self.dev_email(r["id"]): r["uuid"] for r in rows}
            have = {e: u for e, u in self.xray.users().items()
                    if e.endswith("@" + self.domain) and DEV_EMAIL_RE.match(e)}
            stale = [e for e, u in have.items() if want.get(e) != u]
            missing = {e: u for e, u in want.items() if have.get(e) != u}
            removed = self.xray.remove(stale) if stale else 0
            added = self.xray.add(missing) if missing else 0
            if added or removed:
                log.info("Xray: dodano %d, usunieto %d (urzadzen aktywnych: %d)",
                         added, removed, len(want))
            return added, removed

    def sync_safe(self):
        try:
            self.sync()
        except Exception as e:  # petla w tle sprobuje ponownie
            log.error("Synchronizacja z Xray nieudana: %s", e)

    def collect_stats(self):
        stats = self.xray.take_stats()
        with self.db.tx() as c:
            for email, (up, down) in stats.items():
                m = DEV_EMAIL_RE.match(email)
                if m and email.endswith("@" + self.domain) and (up or down):
                    c.execute("UPDATE devices SET up_bytes=up_bytes+?, down_bytes=down_bytes+? "
                              "WHERE id=?", (up, down, int(m.group(1))))

    def purge(self):
        t = now()
        with self.db.tx() as c:
            n = c.execute("DELETE FROM accounts WHERE status='unverified' AND created_at<?",
                          (t - self.cfg["unverified_ttl_h"] * 3600,)).rowcount
            c.execute("DELETE FROM codes WHERE expires_at<?", (t,))
            c.execute("DELETE FROM hits WHERE ts<?", (t - 2 * 86400,))
        if n:
            log.info("Usunieto niepotwierdzone konta: %d", n)

    # ------------------------------------------------------------- admin
    def account(self, email: str):
        email = (email or "").strip().lower()
        with self.db.tx() as c:
            acc = c.execute("SELECT * FROM accounts WHERE email=?", (email,)).fetchone()
        if not acc:
            raise ApiError(404, "not_found", f"Nie ma konta {email}")
        return acc

    def _set_status(self, email, status, allowed_from):
        acc = self.account(email)
        if acc["status"] not in allowed_from:
            raise ApiError(409, "bad_status", f"Konto {acc['email']} ma status {acc['status']}")
        with self.db.tx() as c:
            c.execute("UPDATE accounts SET status=?, approved_at=COALESCE(?, approved_at) "
                      "WHERE id=?", (status, now() if status == "active" else None, acc["id"]))
        self.sync()
        return acc

    def approve(self, email):
        acc = self._set_status(email, "active", ("pending", "unverified", "blocked"))
        self._mail(acc["email"], "konto aktywne",
                   "Cześć!\n\nTwoje konto w Obfuskator VPN zostało zaakceptowane. "
                   "Aplikacja połączy się sama w ciągu minuty (albo zaloguj się ponownie).\n",
                   required=False)

    def block(self, email):
        self._set_status(email, "blocked", ("pending", "active"))

    def unblock(self, email):
        self._set_status(email, "active", ("blocked",))

    def reject(self, email):
        acc = self.account(email)
        if acc["status"] not in ("pending", "unverified"):
            raise ApiError(409, "bad_status", f"Konto ma status {acc['status']} - użyj delete")
        with self.db.tx() as c:
            c.execute("DELETE FROM accounts WHERE id=?", (acc["id"],))
        self.sync()
        self._mail(acc["email"], "konto odrzucone",
                   "Cześć!\n\nTwoja prośba o konto w Obfuskator VPN została odrzucona.\n",
                   required=False)

    def delete(self, email):
        acc = self.account(email)
        with self.db.tx() as c:
            c.execute("DELETE FROM accounts WHERE id=?", (acc["id"],))
        self.sync()

    def revoke(self, email, dev_id=None) -> int:
        acc = self.account(email)
        with self.db.tx() as c:
            if dev_id is None:
                n = c.execute("DELETE FROM devices WHERE account_id=?", (acc["id"],)).rowcount
            else:
                n = c.execute("DELETE FROM devices WHERE account_id=? AND id=?",
                              (acc["id"], dev_id)).rowcount
        self.sync()
        return n

    def set_password(self, email, password):
        acc = self.account(email)
        self.check_password(password)
        with self.db.tx() as c:
            c.execute("UPDATE accounts SET pw_hash=? WHERE id=?", (self.ph.hash(password), acc["id"]))
            c.execute("DELETE FROM hits WHERE kind='limit_login_fail' AND key=?", (acc["email"],))

    def accounts(self, status=None):
        q = ("SELECT a.*, COUNT(d.id) AS devices, COALESCE(SUM(d.up_bytes), 0) AS up, "
             "COALESCE(SUM(d.down_bytes), 0) AS down, MAX(d.last_seen) AS seen "
             "FROM accounts a LEFT JOIN devices d ON d.account_id=a.id ")
        args = ()
        if status:
            q += "WHERE a.status=? "
            args = (status,)
        with self.db.tx() as c:
            return c.execute(q + "GROUP BY a.id ORDER BY a.created_at", args).fetchall()

    def devices(self, email):
        acc = self.account(email)
        with self.db.tx() as c:
            return c.execute("SELECT * FROM devices WHERE account_id=? ORDER BY id",
                             (acc["id"],)).fetchall()
