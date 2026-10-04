# Obfuskator VPN

### Dostawcy internetu używają systemów DPI (Deep Packet Inspection), aby blokować ruch VPN oraz określone porty czy aplikacje. Zwykłe protokoły VPN charakteryzują się specyficznym „podpisem” w pakietach, przez co firewall łatwo je identyfikuje i odrzuca.

**Rozwiązanie polega na:**

Obfuskacji pakietów w ruchu sieciowym. Innymi słowy maskowanie ruchu jako standardowe połączenie przeglądarkowe **HTTPS** do bezpiecznej domeny. Wykorzystywany jest serwer **Xray** z protokołem **VLESS** połączonym przez **WebSocket** (ws) oraz technologię **ECH** (Encrypted Client Hello) z **Cloudflare**. Dla firewalla cały ruch (jakiekolwiek pakiety) wyglądają jak przeglądanie strony.

Aplikacja sama uruchamia [Xray-core](https://github.com/XTLS/Xray-core) (proxy)
i [sing-box](https://github.com/SagerNet/sing-box) (TUN – cały ruch komputera).
Ma też własny lokalny DNS, który podaje Xray aktualny klucz ECH.

<p align="center"><img src="docs/ikona.png" width="128" alt="Ikona: pająk na nitce w tarczy"></p>

<p align="center">
  <img src="docs/logowanie.png" width="260" alt="Ekran logowania">
  <img src="docs/okno.png" width="260" alt="Okno po połączeniu">
  <img src="docs/statystyki.png" width="260" alt="Zakładka Statystyki">
</p>

- **Xray z protokołem VLESS przez WebSocket i TLS.** Dla zapory cały ruch
  wygląda jak połączenie HTTPS ze stroną.
- **Cloudflare jako pośrednik.** Zamiast ciągłego transferu do jednego prywatnego
  adresu IP sieć widzi połączenia z CDN-em, który obsługuje dużą część internetu.
- **ECH (Encrypted Client Hello).** Szyfruje także nazwę domeny. Zapora widzi
  tylko anonimowy ruch HTTPS do Cloudflare i nie wie, z jaką stroną się łączysz.
- **Prawdziwa strona WWW pod tą samą domeną** (Apache). Kto wejdzie na nią
  z przeglądarki, zobaczy zwykłą stronę, a VPN działa pod tajną ścieżką.

**Dlaczego nie wystarczył v2rayN.** Ten zestaw działał z ogólnym klientem
v2rayN, ale wymagał ręcznej pracy:

| Ręcznie w v2rayN | Obfuskator VPN |
|---|---|
| Reguła routingu: adres IP Cloudflare i `xray.exe` → `direct`, inaczej TUN zapętla ruch | Reguły w configu sing-box generuje aplikacja |
| `nslookup` domeny, wpisanie IP Cloudflare jako adresu serwera, ALPN `http/1.1` | Wszystko przychodzi z serwera w profilu konta |
| `dig +short HTTPS <domena>` na serwerze i wklejenie klucza `ech=…` do profilu | Klucz pobierany automatycznie |
| Cloudflare co jakiś czas zmienia klucz ECH, więc połączenie pada i trzeba wkleić nowy (dlatego powstał nawet skrypt wysyłający maila o zmianie klucza) | Aplikacja odświeża klucz w tle co kilka minut, różnymi drogami, i trzyma ostatni dobry |
| Przy włączonym TUN zapytanie Xray o klucz ECH ginęło w tunelu (`tls: malformed ECHConfigList`) | Lokalny DNS na 127.0.0.1 podaje Xray klucz z pominięciem TUN |

## Funkcje

- Ruch wygląda jak zwykłe HTTPS do Cloudflare, a nazwę serwera ukrywa ECH.
- Klucz ECH jest pobierany w tle kilkoma drogami. Ostatni dobry klucz jest zapisywany,
  więc rotacja kluczy w Cloudflare nie zrywa połączenia.
- Cały ruch idzie przez TUN (StrictRoute), a QUIC jest blokowany, żeby ruch wracał do TCP w tunelu.
- Jedno kliknięcie: bez ręcznej konfiguracji routingu, adresów i kluczy jak w v2rayN.
- Kompaktowe okno: przycisk połączenia z prędkością i pingiem, zakładki Statystyki
  (wykres, publiczne IP, klucz ECH) i Konto. Ikona w zasobniku pokazuje stan.
- Dostęp do serwera tylko dla zaakceptowanych osób: konto z e-mailem, osobny UUID
  na urządzenie (do 3 komputerów), „Zapamiętaj mnie” z sesją szyfrowaną Windows DPAPI.

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

## Instalacja (użytkownik)

1. Pobierz **`ObfuskatorVPN-Setup-<wersja>.exe`** z [najnowszego wydania](../../releases/latest)
   i uruchom go. Instalator nie wymaga uprawnień administratora.
2. Uruchom Obfuskator VPN. Za pierwszym razem aplikacja raz zapyta o uprawnienia
   administratora, bo VPN tworzy wirtualną kartę sieciową.
3. Załóż konto i wpisz kod z maila. Po akceptacji konta aplikacja połączy się sama.

Aktualizacja: pobierz nowy instalator i uruchom go. Działającą aplikację zamknie sam,
a konto i ustawienia zostaną. Odinstalujesz ją w **Ustawienia → Aplikacje**.
Bez instalacji możesz też pobrać `ObfuskatorVPN-<wersja>.zip`, rozpakować go
w stałym miejscu i uruchomić `ObfuskatorVPN.exe`.

Szczegóły są w [src/paczka/CZYTAJ.txt](src/paczka/CZYTAJ.txt), a dane, które
zbiera serwer, opisuje [src/paczka/PRYWATNOSC.txt](src/paczka/PRYWATNOSC.txt).

## Znane ograniczenia

- **Gry online (UDP).** Cloudflare przenosi przez WebSocket tylko TCP, więc UDP
  jedzie w środku TCP. Przy utracie pakietów rośnie opóźnienie, co czuć w grach FPS.
- **Brak podpisu cyfrowego exe.** Windows SmartScreen pokazuje ostrzeżenie przy
  pierwszym uruchomieniu.
- **Tylko Windows.** Aplikacja korzysta z TUN przez sing-box, DPAPI i harmonogramu zadań.

## Licencja

Kod w tym repozytorium jest udostępniany na licencji [MIT](LICENSE). Xray-core (MPL-2.0),
sing-box (GPL-3.0+), czcionka PT Root UI (OFL) i biblioteki w paczce mają
własne licencje. Ich spis jest w [src/paczka/LICENCJE.txt](src/paczka/LICENCJE.txt),
a pełne teksty w [src/paczka/licencje/](src/paczka/licencje/).

Korzystając z aplikacji, przestrzegaj regulaminu swojej sieci i prawa.
Autor nie odpowiada za sposób jej użycia.
