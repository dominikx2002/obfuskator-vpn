# Obfuskator VPN

Klient VPN dla Windows z kontami użytkowników. Ruch idzie przez
**VLESS + WebSocket + TLS** za Cloudflare. Nazwa serwera jest ukryta dzięki
**ECH** (Encrypted Client Hello), więc sieć widzi tylko połączenie z Cloudflare.
Aplikacja sama uruchamia [Xray-core](https://github.com/XTLS/Xray-core) (proxy)
i [sing-box](https://github.com/SagerNet/sing-box) (TUN – cały ruch komputera).
Ma też własny lokalny DNS, który podaje Xray aktualny klucz ECH.

<p align="center"><img src="docs/ikona.png" width="128" alt="Ikona: pająk na nitce w tarczy"></p>

<p align="center">
  <img src="docs/logowanie.png" width="260" alt="Ekran logowania">
  <img src="docs/okno.png" width="260" alt="Okno po połączeniu">
  <img src="docs/statystyki.png" width="260" alt="Zakładka Statystyki">
</p>

## Funkcje

- Rejestracja i logowanie (e-mail i hasło). Adres potwierdzasz kodem z maila,
  a dostęp przyznaje administrator.
- Każde urządzenie dostaje własny UUID. Na jednym koncie mogą działać do 3 komputerów.
- „Zapamiętaj mnie”: sesja jest zaszyfrowana Windows DPAPI.
- Cały ruch idzie przez TUN (StrictRoute), a QUIC jest blokowany, żeby ruch wracał do TCP w tunelu.
- Klucz ECH jest pobierany w tle kilkoma drogami. Ostatni dobry klucz jest zapisywany,
  więc rotacja kluczy w Cloudflare nie zrywa połączenia.
- Kompaktowe okno: przycisk połączenia z prędkością i pingiem, zakładki Statystyki
  (wykres, publiczne IP, klucz ECH) i Konto. Ikona w zasobniku pokazuje stan.

## Jak to działa

```
Aplikacja ──ECH/TLS──► Cloudflare ──► Apache ──┬─ /<ścieżka VPN>   → Xray "vpn"   (UUID urządzeń)
                                               └─ /<ścieżka gościa> → Xray "guest" (jeden publiczny UUID)
                                                                        │
                       API kont (FastAPI + SQLite, tylko 127.0.0.1) ◄───┘  ← jedyny cel dostępny dla gościa
```

**Problem:** logowanie odbywa się przed włączeniem VPN-a, a sieć blokuje serwer.
**Rozwiązanie:** aplikacja ma wbudowany UUID gościa. Reguły Xray na serwerze
pozwalają mu łączyć się wyłącznie z API kont. Po akceptacji konta API wydaje
urządzeniu własny UUID, a aplikacja przełącza się na zwykłe połączenie.

**Bezpieczeństwo w skrócie:**

- **Rozmowa z API:** szyfruje ją dodatkowo TLS z certyfikatem przypiętym
  w aplikacji. Cloudflare rozszyfrowuje WebSocket i bez tego widziałby hasła.
- **Hasła:** argon2id.
- **Tokeny i kody z maili:** baza przechowuje tylko ich skróty.
- **Limity prób:** liczone na konto i globalnie.
- **Dostęp do API Xray, usług lokalnych serwera, sieci prywatnych i portu 25:**
  zablokowany z tuneli.
- **Dane wejścia gościa w exe:** są tylko **zaciemnione**, nie zaszyfrowane.
  Gość ma jednak dostęp wyłącznie do API logowania, więc nie są tajemnicą.

## Instalacja (użytkownik)

1. Pobierz `ObfuskatorVPN-<wersja>.zip` z [Releases](../../releases).
2. Rozpakuj go do stałego folderu, np. `C:\ObfuskatorVPN`, i uruchom `ObfuskatorVPN.exe`.
3. Załóż konto i wpisz kod z maila. Po akceptacji konta aplikacja połączy się sama.

Szczegóły są w [src/paczka/CZYTAJ.txt](src/paczka/CZYTAJ.txt), a dane, które
zbiera serwer, opisuje [src/paczka/PRYWATNOSC.txt](src/paczka/PRYWATNOSC.txt).

## Własny serwer

Kod serwera jest w [server/](server/): API kont, synchronizacja użytkowników
z Xray przez `xray api` (bez restartu) i CLI `akvpn-admin`. Wdrożenie na VPS
krok po kroku opisuje [server/INSTRUKCJA_SERWER.md](server/INSTRUKCJA_SERWER.md).

Budowanie aplikacji pod własny serwer (Windows, Python 3.11, `pip install pillow pystray pyinstaller`):

1. Skopiuj `src/serwer_prywatny.przyklad.json` do `src/serwer_prywatny.json` i wpisz swoje dane.
2. Wygeneruj certyfikat API:
   `openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 7300 -subj "/CN=akvpn-api" -keyout server/tajne/tls.key -out server/tajne/tls.crt`
3. Wgraj `xray.exe` i `sing-box.exe` do folderu `bin/`.
4. Uruchom `src\build.cmd`, który zbuduje i zainstaluje aplikację. `src\pakiet.cmd` tworzy paczkę zip.
5. `python server\zbuduj_paczke.py` składa paczkę na serwer.

## Testy

```
cd server && python -m pytest tests/test_api.py          # API (podstawione Xray i poczta)
python server\tests\e2e_lokalny.py                         # pełny przebieg z prawdziwym xray.exe
```

## Licencja

Kod w tym repozytorium jest udostępniany na licencji [MIT](LICENSE). Xray-core (MPL-2.0),
sing-box (GPL-3.0+), czcionka PT Root UI (OFL) i biblioteki w paczce mają
własne licencje. Ich spis jest w [src/paczka/LICENCJE.txt](src/paczka/LICENCJE.txt),
a pełne teksty w [src/paczka/licencje/](src/paczka/licencje/).

Korzystając z aplikacji, przestrzegaj regulaminu swojej sieci i prawa.
Autor nie odpowiada za sposób jej użycia.
