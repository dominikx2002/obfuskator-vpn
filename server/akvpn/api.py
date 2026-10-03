"""API logowania. Slucha tylko na 127.0.0.1 - osiagalne wylacznie z tuneli Xray
(wejscie gościa i VPN). Uruchomienie: python -m akvpn.api"""
import logging
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import config
from .db import Database
from .mailer import Mailer
from .service import ApiError, Service
from .xray import Xray

log = logging.getLogger("akvpn")


class Register(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


class Email(BaseModel):
    email: str = Field(max_length=254)


class Verify(BaseModel):
    email: str = Field(max_length=254)
    code: str = Field(max_length=16)


class Login(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)
    device_id: str = Field(max_length=64)
    device_name: str = Field("", max_length=80)
    logout_others: bool = False


class Reset(BaseModel):
    email: str = Field(max_length=254)
    code: str = Field(max_length=16)
    password: str = Field(max_length=256)


def background(svc: Service, stop: threading.Event):
    """Co sync_interval_s: baza -> Xray (np. po restarcie Xray). Do tego ruch i sprzatanie."""
    cfg = svc.cfg
    last_stats = last_purge = 0.0
    failing = False
    while not stop.is_set():
        try:
            svc.sync()
            if failing:
                log.info("Xray znow odpowiada")
            failing = False
        except Exception as e:
            if not failing:
                log.error("Synchronizacja z Xray nieudana: %s", e)
            failing = True
        t = time.time()
        if not failing and t - last_stats >= cfg["stats_interval_s"]:
            last_stats = t
            try:
                svc.collect_stats()
            except Exception as e:
                log.warning("Statystyki: %s", e)
        if t - last_purge >= 3600:
            last_purge = t
            try:
                svc.purge()
            except Exception as e:
                log.warning("Sprzatanie: %s", e)
        stop.wait(cfg["sync_interval_s"])


def create_app(svc: Service, run_background=True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app):
        stop = threading.Event()
        if run_background:
            threading.Thread(target=background, args=(svc, stop), daemon=True,
                             name="xray-sync").start()
        yield
        stop.set()

    app = FastAPI(title="Obfuskator VPN", docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)

    @app.exception_handler(ApiError)
    async def api_error(_req: Request, e: ApiError):
        headers = {"Retry-After": str(e.extra["retry_after"])} if "retry_after" in e.extra else None
        return JSONResponse(status_code=e.status, content={"detail": e.as_dict()}, headers=headers)

    @app.exception_handler(Exception)
    async def crash(_req: Request, e: Exception):
        log.exception("Blad serwera")
        return JSONResponse(status_code=500, content={"detail": {
            "code": "server_error", "message": "Błąd serwera. Spróbuj za chwilę."}})

    def bearer(authorization: str | None) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise ApiError(401, "session", "Sesja wygasła. Zaloguj się ponownie.")
        return authorization[7:].strip()

    # endpointy synchroniczne - FastAPI uruchamia je w puli watkow (argon2, msmtp blokuja)
    @app.get("/v1/ping")
    def ping():
        return {"ok": True}

    @app.post("/v1/register")
    def register(b: Register):
        return svc.register(b.email, b.password)

    @app.post("/v1/resend")
    def resend(b: Email):
        return svc.resend(b.email)

    @app.post("/v1/verify")
    def verify(b: Verify):
        return svc.verify(b.email, b.code)

    @app.post("/v1/login")
    def login(b: Login):
        return svc.login(b.email, b.password, b.device_id, b.device_name, b.logout_others)

    @app.get("/v1/me")
    def me(authorization: str | None = Header(None)):
        return svc.me(bearer(authorization))

    @app.post("/v1/logout")
    def logout(authorization: str | None = Header(None)):
        return svc.logout(bearer(authorization))

    @app.post("/v1/password/forgot")
    def forgot(b: Email):
        return svc.forgot(b.email)

    @app.post("/v1/password/reset")
    def reset(b: Reset):
        return svc.reset(b.email, b.code, b.password)

    return app


def build_service(cfg=None) -> Service:
    cfg = cfg or config.load()
    return Service(cfg, Database(cfg["db"]), Xray(cfg), Mailer(cfg))


def main():
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    svc = build_service()
    cfg = svc.cfg
    tls = {"ssl_certfile": cfg["tls_cert"], "ssl_keyfile": cfg["tls_key"]} if cfg["tls_cert"] else {}
    log.info("API logowania na %s:%s (TLS: %s)", cfg["listen_host"], cfg["listen_port"],
             "tak" if tls else "NIE")
    uvicorn.run(create_app(svc), host=cfg["listen_host"], port=cfg["listen_port"],
                log_level="warning", access_log=False, proxy_headers=False, **tls)


if __name__ == "__main__":
    main()
