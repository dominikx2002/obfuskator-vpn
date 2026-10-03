"""akvpn-admin - zarzadzanie kontami z linii polecen (SSH).

  akvpn-admin pending                 konta czekajace na akceptacje
  akvpn-admin approve EMAIL           akceptacja (+ mail "konto aktywne")
  akvpn-admin reject EMAIL            odrzucenie i usuniecie (+ mail)
  akvpn-admin list [STATUS]           konta, urzadzenia, ruch
  akvpn-admin devices EMAIL           urzadzenia konta
  akvpn-admin block|unblock EMAIL
  akvpn-admin revoke EMAIL [ID]       wylogowanie urzadzenia (albo wszystkich)
  akvpn-admin passwd EMAIL            nowe haslo (zapyta)
  akvpn-admin delete EMAIL
  akvpn-admin sync                    wymuszenie synchronizacji z Xray
  akvpn-admin xray                    uzytkownicy widoczni teraz w Xray
"""
import argparse
import getpass
import sys
from datetime import datetime

from .api import build_service
from .service import ApiError

STATUS_PL = {"unverified": "niepotwierdzone", "pending": "czeka", "active": "aktywne",
             "blocked": "zablokowane"}


def fmt_bytes(n):
    n = float(n or 0)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def fmt_time(t):
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M") if t else "-"


def table(rows, headers):
    rows = [[str(x) for x in r] for r in rows]
    w = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h)
         for i, h in enumerate(headers)]
    print("  ".join(h.ljust(w[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * x for x in w))
    for r in rows:
        print("  ".join(c.ljust(w[i]) for i, c in enumerate(r)))
    if not rows:
        print("(brak)")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="akvpn-admin", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pending")
    p = sub.add_parser("list")
    p.add_argument("status", nargs="?", choices=list(STATUS_PL))
    for name in ("approve", "reject", "devices", "block", "unblock", "passwd", "delete"):
        sub.add_parser(name).add_argument("email")
    p = sub.add_parser("revoke")
    p.add_argument("email")
    p.add_argument("device_id", nargs="?", type=int)
    sub.add_parser("sync")
    sub.add_parser("xray")
    a = ap.parse_args(argv)

    svc = build_service()
    try:
        if a.cmd in ("pending", "list"):
            status = "pending" if a.cmd == "pending" else a.status
            table([(r["email"], STATUS_PL.get(r["status"], r["status"]), fmt_time(r["created_at"]),
                    r["devices"], fmt_time(r["seen"]), fmt_bytes(r["down"]), fmt_bytes(r["up"]))
                   for r in svc.accounts(status)],
                  ["E-MAIL", "STATUS", "ZAŁOŻONE", "URZ.", "OSTATNIO", "POBRANE", "WYSŁANE"])
        elif a.cmd == "approve":
            svc.approve(a.email)
            print(f"Zaakceptowano {a.email} - urządzenia dodane do Xray, wysłano maila.")
        elif a.cmd == "reject":
            svc.reject(a.email)
            print(f"Odrzucono i usunięto {a.email}.")
        elif a.cmd == "devices":
            table([(d["id"], d["name"], svc.dev_email(d["id"]), fmt_time(d["created_at"]),
                    fmt_time(d["last_seen"]), fmt_bytes(d["down_bytes"]), fmt_bytes(d["up_bytes"]))
                   for d in svc.devices(a.email)],
                  ["ID", "NAZWA", "W XRAY", "DODANE", "OSTATNIO", "POBRANE", "WYSŁANE"])
        elif a.cmd == "block":
            svc.block(a.email)
            print(f"Zablokowano {a.email} - UUID usunięte z Xray.")
        elif a.cmd == "unblock":
            svc.unblock(a.email)
            print(f"Odblokowano {a.email}.")
        elif a.cmd == "revoke":
            n = svc.revoke(a.email, a.device_id)
            print(f"Wylogowano urządzeń: {n}.")
        elif a.cmd == "passwd":
            pw = getpass.getpass("Nowe hasło: ")
            if pw != getpass.getpass("Powtórz: "):
                print("Hasła się różnią.")
                return 1
            svc.set_password(a.email, pw)
            print("Hasło zmienione.")
        elif a.cmd == "delete":
            svc.delete(a.email)
            print(f"Usunięto {a.email}.")
        elif a.cmd == "sync":
            added, removed = svc.sync()
            print(f"Xray: dodano {added}, usunięto {removed}.")
        elif a.cmd == "xray":
            users = svc.xray.users()
            table(sorted(users.items()), ["E-MAIL W XRAY", "UUID"])
    except ApiError as e:
        print(f"Błąd: {e.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
