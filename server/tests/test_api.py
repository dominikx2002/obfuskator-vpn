import re

import pytest
from fastapi.testclient import TestClient

from akvpn import config as config_mod
from akvpn.api import create_app
from akvpn.db import Database
from akvpn.mailer import MailError
from akvpn.service import Service

TEMPLATE = ("vless://{uuid}@104.21.91.212:443?encryption=none&security=tls&sni=x.example"
            "&type=ws&host=x.example&path=%2Fvpn&ech=1#{name}")


class FakeXray:
    def __init__(self):
        self.u = {"guest@other": "g"}   # obcy wpis - nie wolno go ruszyc
        self.stats = {}
        self.down = False

    def users(self):
        if self.down:
            raise RuntimeError("xray nie dziala")
        return dict(self.u)

    def add(self, users):
        self.u.update(users)
        return len(users)

    def remove(self, emails):
        for e in emails:
            self.u.pop(e, None)
        return len(emails)

    def take_stats(self):
        s, self.stats = self.stats, {}
        return s


class FakeMailer:
    def __init__(self):
        self.out = []
        self.fail = False

    def send(self, to, subject, body):
        if self.fail:
            raise MailError("smtp nie dziala")
        self.out.append((to, subject, body))

    def code_for(self, to):
        for t, _s, body in reversed(self.out):
            if t == to:
                m = re.search(r"\b(\d{6})\b", body)
                if m:
                    return m.group(1)
        raise AssertionError(f"brak kodu dla {to}")


@pytest.fixture
def env(tmp_path):
    cfg = dict(config_mod.DEFAULTS)
    cfg.update(db=str(tmp_path / "t.db"), vpn_link_template=TEMPLATE, admin_email="admin@x.pl")
    xr, ml = FakeXray(), FakeMailer()
    svc = Service(cfg, Database(cfg["db"]), xr, ml)
    client = TestClient(create_app(svc, run_background=False))
    return svc, client, xr, ml


def register_verified(client, ml, email="ania@x.pl", pw="haslo1234"):
    assert client.post("/v1/register", json={"email": email, "password": pw}).status_code == 200
    r = client.post("/v1/verify", json={"email": email, "code": ml.code_for(email)})
    assert r.json() == {"status": "pending"}


def login(client, email="ania@x.pl", pw="haslo1234", dev="device-0001", **kw):
    return client.post("/v1/login", json={"email": email, "password": pw, "device_id": dev,
                                          "device_name": "LAPTOP-ANI", **kw})


def test_full_flow(env):
    svc, client, xr, ml = env
    register_verified(client, ml)
    assert any(t == "admin@x.pl" and "approve ania@x.pl" in b for t, _s, b in ml.out)

    r = login(client)
    assert r.status_code == 200
    s = r.json()
    assert s["status"] == "pending" and s["profile"] is None and s["token"]
    assert "dev1@akvpn" not in xr.u          # pending -> nie ma go w Xray

    svc.approve("ania@x.pl")
    uid = xr.u["dev1@akvpn"]
    assert ml.out[-1][1].endswith("konto aktywne")

    me = client.get("/v1/me", headers={"Authorization": "Bearer " + s["token"]}).json()
    assert me["status"] == "active" and me["profile"].startswith(f"vless://{uid}@")
    assert "{name}" not in me["profile"] and "token" not in me

    # ponowne logowanie z tego samego komputera: ten sam UUID, nowy token
    s2 = login(client).json()
    assert s2["profile"] == me["profile"] and s2["token"] != s["token"]
    assert client.get("/v1/me", headers={"Authorization": "Bearer " + s["token"]}).status_code == 401

    svc.block("ania@x.pl")
    assert "dev1@akvpn" not in xr.u
    r = client.get("/v1/me", headers={"Authorization": "Bearer " + s2["token"]})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "blocked"
    assert login(client).json()["detail"]["code"] == "blocked"
    svc.unblock("ania@x.pl")
    assert xr.u["dev1@akvpn"] == uid

    assert client.post("/v1/logout", headers={"Authorization": "Bearer " + s2["token"]}).json()["ok"]
    assert "dev1@akvpn" not in xr.u
    assert xr.u["guest@other"] == "g"        # obcych wpisow nie rusza


def test_register_errors(env):
    _svc, client, _xr, ml = env
    r = client.post("/v1/register", json={"email": "zly", "password": "haslo1234"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "bad_email"
    r = client.post("/v1/register", json={"email": "a@x.pl", "password": "krotkie"})
    assert r.json()["detail"]["code"] == "weak_password"
    register_verified(client, ml, "a@x.pl")
    r = client.post("/v1/register", json={"email": "A@X.pl ", "password": "haslo1234"})
    assert r.status_code == 409


def test_unverified_login_and_resend(env):
    _svc, client, _xr, ml = env
    client.post("/v1/register", json={"email": "b@x.pl", "password": "haslo1234"})
    r = login(client, "b@x.pl")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "unverified"
    old = ml.code_for("b@x.pl")
    client.post("/v1/resend", json={"email": "b@x.pl"})
    new = ml.code_for("b@x.pl")
    if old != new:
        r = client.post("/v1/verify", json={"email": "b@x.pl", "code": old})
        assert r.json()["detail"]["code"] == "code_invalid"
    assert client.post("/v1/verify", json={"email": "b@x.pl", "code": new}).json()["status"] == "pending"


def test_code_attempts(env):
    svc, client, _xr, ml = env
    client.post("/v1/register", json={"email": "c@x.pl", "password": "haslo1234"})
    good = ml.code_for("c@x.pl")
    bad = "000000" if good != "000000" else "111111"
    for i in range(svc.cfg["code_attempts"]):
        r = client.post("/v1/verify", json={"email": "c@x.pl", "code": bad})
        assert r.json()["detail"]["code"] == "code_invalid"
    # po wyczerpaniu prob nawet dobry kod nie dziala
    r = client.post("/v1/verify", json={"email": "c@x.pl", "code": good})
    assert "Za dużo" in r.json()["detail"]["message"]


def test_login_rate_limit(env):
    svc, client, _xr, ml = env
    register_verified(client, ml)
    for _ in range(5):
        assert login(client, pw="zlehaslo99").status_code == 401
    r = login(client)  # dobre haslo, ale konto chwilowo zablokowane
    assert r.status_code == 429 and "Retry-After" in r.headers
    # inne konto dziala normalnie
    register_verified(client, ml, "d@x.pl")
    assert login(client, "d@x.pl").status_code == 200


def test_unknown_email_same_error(env):
    _svc, client, _xr, _ml = env
    r = login(client, "nikt@x.pl")
    assert r.status_code == 401 and r.json()["detail"]["code"] == "bad_login"


def test_device_limit(env):
    svc, client, xr, ml = env
    register_verified(client, ml)
    svc.approve("ania@x.pl")
    for i in range(3):
        assert login(client, dev=f"device-000{i}").status_code == 200
    r = login(client, dev="device-0009")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "device_limit"
    assert len(r.json()["detail"]["devices"]) == 3
    r = login(client, dev="device-0009", logout_others=True)
    assert r.status_code == 200
    assert [e for e in xr.u if e.startswith("dev")] == [f"dev{r.json()['device']['id']}@akvpn"]


def test_password_reset(env):
    svc, client, xr, ml = env
    register_verified(client, ml)
    svc.approve("ania@x.pl")
    tok = login(client).json()["token"]
    assert client.post("/v1/password/forgot", json={"email": "ania@x.pl"}).json()["ok"]
    assert client.post("/v1/password/forgot", json={"email": "nikt@x.pl"}).json()["ok"]
    code = ml.code_for("ania@x.pl")
    r = client.post("/v1/password/reset", json={"email": "ania@x.pl", "code": code,
                                                "password": "nowehaslo1"})
    assert r.json()["ok"]
    assert client.get("/v1/me", headers={"Authorization": "Bearer " + tok}).status_code == 401
    assert not [e for e in xr.u if e.startswith("dev")]     # wylogowany wszedzie
    assert login(client).status_code == 401
    assert login(client, pw="nowehaslo1").status_code == 200


def test_mail_limits(env):
    svc, client, _xr, ml = env
    client.post("/v1/register", json={"email": "e@x.pl", "password": "haslo1234"})
    client.post("/v1/resend", json={"email": "e@x.pl"})
    client.post("/v1/resend", json={"email": "e@x.pl"})
    r = client.post("/v1/resend", json={"email": "e@x.pl"})   # 4. mail w godzine
    assert r.status_code == 429


def test_register_global_limit(env):
    _svc, client, _xr, _ml = env
    for i in range(10):
        assert client.post("/v1/register", json={"email": f"u{i}@x.pl",
                                                 "password": "haslo1234"}).status_code == 200
    r = client.post("/v1/register", json={"email": "u99@x.pl", "password": "haslo1234"})
    assert r.status_code == 429


def test_mail_failure(env):
    _svc, client, _xr, ml = env
    ml.fail = True
    r = client.post("/v1/register", json={"email": "f@x.pl", "password": "haslo1234"})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "mail_failed"
    ml.fail = False
    assert client.post("/v1/resend", json={"email": "f@x.pl"}).status_code == 200
    assert ml.code_for("f@x.pl")


def test_sync_restores_after_xray_restart(env):
    svc, client, xr, ml = env
    register_verified(client, ml)
    svc.approve("ania@x.pl")
    login(client)
    xr.u = {}                       # restart Xray - pusty config
    assert svc.sync() == (1, 0)
    assert "dev1@akvpn" in xr.u
    xr.u["dev77@akvpn"] = "obcy"     # nieznane urzadzenie z naszej domeny - usuwane
    xr.u["dev1@akvpn"] = "zmieniony"  # zly UUID - poprawiany
    svc.sync()
    assert "dev77@akvpn" not in xr.u and xr.u["dev1@akvpn"] != "zmieniony"


def test_xray_down_does_not_break_login(env):
    svc, client, xr, ml = env
    register_verified(client, ml)
    svc.approve("ania@x.pl")
    xr.down = True
    assert login(client).status_code == 200   # dojdzie przy nastepnej synchronizacji
    xr.down = False
    svc.sync()
    assert "dev1@akvpn" in xr.u


def test_stats_and_purge(env):
    svc, client, xr, ml = env
    register_verified(client, ml)
    svc.approve("ania@x.pl")
    login(client)
    xr.stats = {"dev1@akvpn": [100, 2000], "guest@akvpn": [5, 5]}
    svc.collect_stats()
    xr.stats = {"dev1@akvpn": [1, 1]}
    svc.collect_stats()
    d = svc.devices("ania@x.pl")[0]
    assert (d["up_bytes"], d["down_bytes"]) == (101, 2001)
    client.post("/v1/register", json={"email": "stary@x.pl", "password": "haslo1234"})
    with svc.db.tx() as c:
        c.execute("UPDATE accounts SET created_at=0 WHERE email='stary@x.pl'")
    svc.purge()
    assert [r["email"] for r in svc.accounts()] == ["ania@x.pl"]


def test_reject_and_admin_errors(env):
    svc, client, _xr, ml = env
    register_verified(client, ml)
    svc.reject("ania@x.pl")
    assert svc.accounts() == []
    from akvpn.service import ApiError
    with pytest.raises(ApiError):
        svc.approve("nikt@x.pl")


def test_bad_token_and_device(env):
    _svc, client, _xr, ml = env
    assert client.get("/v1/me").status_code == 401
    assert client.get("/v1/me", headers={"Authorization": "Bearer xyz"}).status_code == 401
    register_verified(client, ml)
    r = login(client, dev="x")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "bad_device"
