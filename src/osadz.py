"""
Zaciemnia dane wejscia gościa i certyfikat API do src\\_wbudowane.py (wola to build.cmd).

Zrodla (tylko na Twoim komputerze, nie ida do paczki):
  src\\serwer_prywatny.json   - adres Cloudflare, SNI, sciezka i UUID gościa
  server\\tajne\\tls.crt      - certyfikat API logowania (klucz prywatny jest tylko na serwerze)

To zaciemnienie, nie szyfrowanie: chroni przed podejrzeniem danych w exe
zwyklym podgladem, a nie przed kims, kto zdekompiluje aplikacje. Prawdziwa
ochrona jest na serwerze - gosc dochodzi tylko do API logowania, a sciezke
VPN i UUID dostaje dopiero zaakceptowane konto.
"""
import base64
import hashlib
import json
import secrets
import sys
import zlib
from pathlib import Path

SRC = Path(__file__).resolve().parent
ROOT = SRC.parent


def keystream(seed: bytes, n: int) -> bytes:
    out = b""
    i = 0
    while len(out) < n:
        out += hashlib.sha256(seed + i.to_bytes(4, "big")).digest()
        i += 1
    return out[:n]


def pack(data: dict) -> str:
    raw = zlib.compress(json.dumps(data, separators=(",", ":")).encode(), 9)
    seed = secrets.token_bytes(16)
    return base64.b85encode(seed + bytes(a ^ b for a, b in zip(raw, keystream(seed, len(raw))))).decode()


def unpack(blob: str) -> dict:
    b = base64.b85decode(blob)
    seed, body = b[:16], b[16:]
    return json.loads(zlib.decompress(bytes(a ^ b for a, b in zip(body, keystream(seed, len(body))))))


def main():
    priv = json.loads((SRC / "serwer_prywatny.json").read_text(encoding="utf-8"))
    # tylko wejscie gościa - sciezka VPN i e-mail admina nie trafiaja do exe
    data = {k: priv[k] for k in ("host", "port", "sni", "alpn", "guest_path", "guest_uuid")}
    data["api_port"] = priv.get("api_port", 10910)
    data["api_cert"] = (ROOT / "server" / "tajne" / "tls.crt").read_text(encoding="ascii")
    blob = pack(data)
    assert unpack(blob) == data
    lines = [blob[i:i + 96] for i in range(0, len(blob), 96)]
    (SRC / "_wbudowane.py").write_text(
        "# Wygenerowane przez osadz.py - nie edytuj.\nBLOB = (\n"
        + "".join(f'    "{x}"\n' for x in lines) + ")\n", encoding="utf-8")
    print(f"Zapisano _wbudowane.py ({len(blob)} znakow)")


if __name__ == "__main__":
    sys.exit(main())
