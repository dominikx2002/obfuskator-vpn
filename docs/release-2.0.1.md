## Obfuskator VPN 2.0.1

**Instalator.** Pobierz `ObfuskatorVPN-Setup-2.0.1.exe`, uruchom i gotowe.
Nie trzeba niczego rozpakowywać.

### Instalacja
1. Pobierz i uruchom **`ObfuskatorVPN-Setup-2.0.1.exe`**. Instalator nie wymaga uprawnień administratora.
2. Uruchom Obfuskator VPN. Za pierwszym razem aplikacja raz zapyta o uprawnienia administratora,
   bo VPN tworzy wirtualną kartę sieciową.
3. Załóż konto, wpisz kod z maila i poczekaj na akceptację. Aplikacja połączy się sama.

Exe nie jest podpisany cyfrowo, więc Windows SmartScreen może pokazać ostrzeżenie
(„Więcej informacji” → „Uruchom mimo to”).

### Zmiany
- Instalator (Inno Setup): skrót w menu Start, opcjonalnie na pulpicie, i wpis w **Ustawienia → Aplikacje**.
  - **Aktualizacja:** instalator sam zamyka działającą aplikację, a konto i ustawienia zostają.
  - **Odinstalowanie:** rozłącza VPN, usuwa zadanie harmonogramu i dane aplikacji.
- Wersja przenośna dalej jest dostępna jako `ObfuskatorVPN-2.0.1.zip`.
- Poprawka: zamknięcie aplikacji w trakcie łączenia nie zostawia już działającego TUN-a bez internetu.
