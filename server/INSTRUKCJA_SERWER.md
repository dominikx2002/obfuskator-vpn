# Akademik VPN – konta: wdrożenie na Mikrusa

Wszystkie komendy na serwerze wykonujesz jako root (SSH, port 10139).
Twój obecny dostęp do VPN działa przez cały czas wdrożenia. Stare UUID
usuwasz dopiero w kroku 7, gdy nowa aplikacja już działa.

## 0. Na Windowsie: paczka i wysłanie

```powershell
cd C:\Users\Dominik\AkademikVPN
python server\zbuduj_paczke.py
scp -P 10139 "$HOME\Desktop\akvpn-serwer.tar.gz" root@SERWER:/root/
```
`SERWER` to ten sam adres, którego używasz w `ssh` (np. `srv39.mikr.us`).
Paczka zawiera klucz prywatny API, więc nie wysyłaj jej nikomu.

## 1. Instalacja

```bash
apt-get update && apt-get install -y python3-venv
id akvpn >/dev/null 2>&1 || useradd --system --home-dir /var/lib/akvpn --shell /usr/sbin/nologin akvpn
cd /root && tar -xzf akvpn-serwer.tar.gz
install -d -m 755 /opt/akvpn /opt/akvpn/deploy
cp -r /root/akvpn-serwer/akvpn /root/akvpn-serwer/requirements.txt /opt/akvpn/
cp /root/akvpn-serwer/deploy/* /opt/akvpn/deploy/
python3 -m venv /opt/akvpn/venv
/opt/akvpn/venv/bin/pip install -q -r /opt/akvpn/requirements.txt && echo PIP OK
install -d -m 750 -o akvpn -g akvpn /etc/akvpn /var/lib/akvpn
install -m 640 -o root -g akvpn /root/akvpn-serwer/config.json /etc/akvpn/config.json
install -m 644 /root/akvpn-serwer/tls.crt /etc/akvpn/tls.crt
install -m 600 -o akvpn -g akvpn /root/akvpn-serwer/tls.key /etc/akvpn/tls.key
install -m 755 /opt/akvpn/deploy/akvpn-admin /usr/local/bin/akvpn-admin
```

## 2. Poczta (kopia Twojej konfiguracji msmtp dla użytkownika akvpn)

```bash
SRC=$(ls /root/.msmtprc /etc/msmtprc 2>/dev/null | head -1); echo "msmtp: $SRC"
sed '/^[[:space:]]*logfile/d' "$SRC" > /etc/akvpn/msmtprc
chown akvpn:akvpn /etc/akvpn/msmtprc && chmod 600 /etc/akvpn/msmtprc
grep -nE '^[[:space:]]*(account|from|passwordeval|tls_trust_file)' /etc/akvpn/msmtprc
printf 'To: TWOJ_EMAIL\nSubject: test akvpn\n\nTest wysylki jako uzytkownik akvpn.\n' \
  | runuser -u akvpn -- /usr/bin/msmtp -C /etc/akvpn/msmtprc -t && echo MAIL OK
```
Jeśli zamiast `MAIL OK` jest błąd, a `grep` pokazał `passwordeval` czytające plik
z `/root`, wklej mi wynik. Użytkownik `akvpn` nie ma dostępu do `/root`.

## 3. Xray: wejście gościa, API Xray, reguły (starzy klienci zostają)

```bash
cp -a /usr/local/etc/xray/config.json /root/xray-config.przed-kontami.json
python3 /opt/akvpn/deploy/xray_config.py /usr/local/etc/xray/config.json > /tmp/xray-new.json
xray run -test -c /tmp/xray-new.json && cp /tmp/xray-new.json /usr/local/etc/xray/config.json \
  && chmod 644 /usr/local/etc/xray/config.json && systemctl restart xray && sleep 1 && systemctl is-active xray
xray api inbounduser -s 127.0.0.1:10085 -tag=guest
```
Ostatnia komenda ma pokazać użytkownika `guest@akvpn`.

## 4. Apache: ścieżka gościa

```bash
F=$(readlink -f /etc/apache2/conf-enabled/xray.conf); cp -a "$F" /root/apache-xray.conf.bak
grep -qF "$(cut -d' ' -f2 /root/akvpn-serwer/apache-linia.txt)" "$F" || cat /root/akvpn-serwer/apache-linia.txt >> "$F"
cat "$F"
apache2ctl configtest && systemctl reload apache2
```

## 5. Usługa API i test

```bash
cp /opt/akvpn/deploy/akvpn-api.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now akvpn-api
sleep 3; systemctl status akvpn-api --no-pager -n 15
python3 /opt/akvpn/deploy/sprawdz.py
akvpn-admin list
```
`sprawdz.py` ma pokazać trzy razy `OK`. Jeśli wariant „Apache :80” ma `BLAD`,
a dwa pozostałe są OK, vhost na porcie 80 pewnie przekierowuje na HTTPS. To
niegroźne, prawdziwy test zrobi aplikacja w kroku 6.

## 6. Na Windowsie: nowa aplikacja i Twoje konto

1. Zamknij Akademik VPN (menu `⋯` → Zakończ).
2. Uruchom `src\build.cmd`.
3. Uruchom `AkademikVPN.exe`, kliknij **Załóż konto**, wpisz kod z maila.
4. Na serwerze zaakceptuj swoje konto:
   ```bash
   akvpn-admin approve TWOJ_EMAIL
   ```
5. Aplikacja połączy się sama w ciągu minuty (albo kliknij „Sprawdź teraz”).

## 7. Usunięcie starych UUID (dopiero gdy krok 6 działa)

```bash
python3 /opt/akvpn/deploy/xray_config.py --drop-old > /tmp/xray-new.json
xray run -test -c /tmp/xray-new.json && cp /tmp/xray-new.json /usr/local/etc/xray/config.json \
  && systemctl restart xray && sleep 35 && akvpn-admin xray
```
Po restarcie Xray urządzenia z bazy wracają w ciągu 30 s. Ostatnia komenda to pokazuje.

## 8. Paczka dla znajomych

`src\pakiet.cmd` tworzy `AkademikVPN.zip` na pulpicie. Znajomi zakładają konta,
a Ty dostajesz maila i akceptujesz je przez `akvpn-admin approve EMAIL`.

## Codzienna obsługa

```bash
akvpn-admin pending              # kto czeka na akceptację
akvpn-admin approve EMAIL        # akceptacja
akvpn-admin reject EMAIL         # odrzucenie
akvpn-admin list                 # wszyscy: status, urządzenia, ruch
akvpn-admin devices EMAIL        # urządzenia konta (ID do revoke)
akvpn-admin block EMAIL          # blokada (natychmiast znika z Xray)
akvpn-admin unblock EMAIL
akvpn-admin revoke EMAIL [ID]    # wylogowanie urządzenia / wszystkich
akvpn-admin passwd EMAIL         # ustawienie hasła ręcznie
akvpn-admin delete EMAIL
journalctl -u akvpn-api -f       # log API
```

## Wycofanie wszystkiego

```bash
systemctl disable --now akvpn-api
cp /root/xray-config.przed-kontami.json /usr/local/etc/xray/config.json && systemctl restart xray
cp /root/apache-xray.conf.bak "$(readlink -f /etc/apache2/conf-enabled/xray.conf)" && systemctl reload apache2
```

## Aktualizacja kodu serwera w przyszłości

```bash
cd /root && rm -rf akvpn-serwer && tar -xzf akvpn-serwer.tar.gz
cp -r /root/akvpn-serwer/akvpn /opt/akvpn/ && systemctl restart akvpn-api
```
