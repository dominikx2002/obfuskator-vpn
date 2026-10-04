## Obfuskator VPN 2.1.0 – własny serwer

### Nowość: własny serwer, bez konta
Na starcie wybierasz serwer:
- **Serwer Obfuskator VPN:** konto i dostęp po akceptacji, jak dotąd.
- **Własny serwer:** Twój VPS, bez konta.
  - **Wklej link `vless://`** z dowolnego serwera za Cloudflare. Routing, ECH, ALPN i adres
    Cloudflare aplikacja ustawia sama, bez ręcznej konfiguracji jak w v2rayN.
  - **Kreator Mikrus:**
    1. Dodaj w panelu Mikrusa subdomenę na porcie 80.
    2. Wpisz ją w aplikacji i wklej w SSH jedną komendę. Komenda instaluje Apache i Xray,
       ustawia VLESS przez WebSocket i sprawdza działanie.
    3. Kliknij „Połącz”.

Działa jeden tryb naraz. Żeby zmienić serwer, wyloguj się w zakładce Konto.

### Poprawki
- Pierwsze pobranie klucza ECH korzysta z DNS Twojej sieci (wcześniej ze stałego adresu).

### Instalacja / aktualizacja
Pobierz i uruchom **`ObfuskatorVPN-Setup-2.1.0.exe`**. Wersja przenośna: `ObfuskatorVPN-2.1.0.zip`.
