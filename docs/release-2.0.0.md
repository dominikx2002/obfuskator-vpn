## Obfuskator VPN 2.0.0

Pierwsze publiczne wydanie (dawniej Akademik VPN).

### Nowości
- **Konta użytkowników.** Rejestrujesz się e-mailem i hasłem, adres potwierdzasz kodem z maila, a dostęp przyznaje administrator.
  Każde urządzenie dostaje własny identyfikator. Na jednym koncie mogą działać do 3 komputerów.
- **„Zapamiętaj mnie”.** Sesja jest zaszyfrowana przez Windows (DPAPI).
- **Nowy wygląd.** Kompaktowe okno z zakładkami Połączenie / Statystyki / Konto, fioletowy motyw,
  czcionka PT Root UI (ta sama co w AmneziaVPN) i nowa ikona: pająk w tarczy.
- **Bezpieczniejsze logowanie.** Logowanie działa jeszcze przed połączeniem VPN, przez ograniczone wejście gościa.
  Hasła są dodatkowo szyfrowane TLS-em z przypiętym certyfikatem.

### Instalacja
1. Pobierz `ObfuskatorVPN-2.0.0.zip` i rozpakuj go w stałym miejscu, np. `C:\ObfuskatorVPN`.
2. Uruchom `ObfuskatorVPN.exe`. Za pierwszym razem pojawi się pytanie o uprawnienia administratora, bo VPN tworzy wirtualną kartę sieciową.
3. Załóż konto i poczekaj na akceptację. Aplikacja połączy się sama.

Exe nie jest podpisany cyfrowo, więc Windows SmartScreen może pokazać ostrzeżenie
(„Więcej informacji” → „Uruchom mimo to”).

**Aktualizacja z Akademik VPN:** rozpakuj nową wersję w miejsce starej. Folder `data` zostaje,
stare skróty i zadanie harmonogramu aplikacja usunie sama.

### W paczce
Xray-core 26.9.9 (MPL-2.0) i sing-box 1.14.2 (GPL-3.0+) bez zmian.
Licencje są w `LICENCJE.txt` i w folderze `licencje`, a informacje o danych w `PRYWATNOSC.txt`.
