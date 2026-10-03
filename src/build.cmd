@echo off
:: Buduje ObfuskatorVPN.exe z obfuskator_vpn.pyw i podmienia go w folderze nadrzednym.
::   build.cmd                  - buduje i instaluje (aplikacja musi byc zamknieta)
::   build.cmd --bez-instalacji - tylko buduje do %TEMP%\akvpn_dist (uzywa pakiet.cmd)
:: Wymaga src\serwer_prywatny.json i server\tajne\tls.crt (dane wejscia gościa, tylko u Ciebie).
setlocal
cd /d "%~dp0"
set "INSTALL=1"
if /i "%~1"=="--bez-instalacji" set "INSTALL="
if defined INSTALL for %%N in (ObfuskatorVPN.exe AkademikVPN.exe) do (
  tasklist /fi "imagename eq %%N" | find /i "%%N" >nul && (
    echo Aplikacja jest uruchomiona - zamknij ja: menu ... -^> Zakoncz, i uruchom build.cmd ponownie.
    exit /b 1
  )
)
python osadz.py || exit /b 1
python obfuskator_vpn.pyw --make-icon || exit /b 1
python -m PyInstaller --noconfirm --clean --windowed --name ObfuskatorVPN ^
  --icon "%~dp0..\app.ico" --version-file "%~dp0version.txt" ^
  --hidden-import pystray._win32 --hidden-import _wbudowane --paths "%~dp0." ^
  --add-data "%~dp0fonts;fonts" --exclude-module numpy ^
  --distpath "%TEMP%\akvpn_dist" --workpath "%TEMP%\akvpn_build" --specpath "%TEMP%\akvpn_build" ^
  obfuskator_vpn.pyw || exit /b 1
:: exe z --windowed - cmd sam nie czeka na jego zakonczenie
powershell -NoProfile -Command "Start-Process -Wait -FilePath '%TEMP%\akvpn_dist\ObfuskatorVPN\ObfuskatorVPN.exe' -ArgumentList '--selftest'"
if not exist "%TEMP%\akvpn_dist\ObfuskatorVPN\data\selftest.txt" (echo Selftest exe nieudany & exit /b 1)
rmdir /s /q "%TEMP%\akvpn_dist\ObfuskatorVPN\data"
if not defined INSTALL (echo Zbudowano: %TEMP%\akvpn_dist\ObfuskatorVPN & exit /b 0)
if exist "..\_internal" rmdir /s /q "..\_internal"
if exist "..\_internal" (echo Nie moge usunac starego _internal - czy aplikacja jest zamknieta? & exit /b 1)
xcopy /e /i /q /y "%TEMP%\akvpn_dist\ObfuskatorVPN\_internal" "..\_internal" >nul || exit /b 1
copy /y "%TEMP%\akvpn_dist\ObfuskatorVPN\ObfuskatorVPN.exe" "..\ObfuskatorVPN.exe" >nul || exit /b 1
copy /y "%~dp0paczka\PRYWATNOSC.txt" "..\PRYWATNOSC.txt" >nul
:: po zmianie nazwy: stary exe i ikona z poprzedniej wersji
if exist "..\AkademikVPN.exe" del /q "..\AkademikVPN.exe"
echo Gotowe: %~dp0..\ObfuskatorVPN.exe
