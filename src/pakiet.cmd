@echo off
:: Buduje paczke dla uzytkownikow: ObfuskatorVPN-<wersja>.zip na pulpicie (np. do release na GitHubie).
:: Nie zawiera Twoich danych (data, settings.json) - tylko aplikacje, rdzenie i licencje.
:: Zainstalowanej aplikacji nie rusza, wiec moze byc uruchomiona.
setlocal
set "SRC=%~dp0"
set "APP=%~dp0.."
call "%SRC%build.cmd" --bez-instalacji || exit /b 1
for /f "usebackq delims=" %%V in (`powershell -NoProfile -Command "(Select-String -Path '%SRC%version.txt' -Pattern 'ProductVersion., .([0-9.]+)').Matches[0].Groups[1].Value"`) do set "VER=%%V"
set "OUT=%TEMP%\akvpn_pakiet\ObfuskatorVPN"
if exist "%TEMP%\akvpn_pakiet" rmdir /s /q "%TEMP%\akvpn_pakiet"
mkdir "%OUT%\bin"
xcopy /e /i /q /y "%TEMP%\akvpn_dist\ObfuskatorVPN" "%OUT%" >nul || exit /b 1
copy /y "%APP%\bin\xray.exe" "%OUT%\bin\" >nul || (echo Brak bin\xray.exe & exit /b 1)
copy /y "%APP%\bin\sing-box.exe" "%OUT%\bin\" >nul || (echo Brak bin\sing-box.exe & exit /b 1)
copy /y "%SRC%paczka\CZYTAJ.txt" "%OUT%\" >nul
copy /y "%SRC%paczka\LICENCJE.txt" "%OUT%\" >nul
copy /y "%SRC%paczka\PRYWATNOSC.txt" "%OUT%\" >nul
xcopy /e /i /q /y "%SRC%paczka\licencje" "%OUT%\licencje" >nul || exit /b 1
copy /y "%APP%\LICENSE" "%OUT%\licencje\MIT-ObfuskatorVPN.txt" >nul
for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')"`) do set "DESK=%%D"
:: zipfile z Pythona - Compress-Archive z PowerShell 5.1 zapisuje sciezki z "\" (psuje je poza Windows)
python -c "import shutil,sys; shutil.make_archive(sys.argv[1], 'zip', sys.argv[2], 'ObfuskatorVPN')" "%DESK%\ObfuskatorVPN-%VER%" "%TEMP%\akvpn_pakiet" || exit /b 1
rmdir /s /q "%TEMP%\akvpn_pakiet"
echo Gotowe: %DESK%\ObfuskatorVPN-%VER%.zip
