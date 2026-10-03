@echo off
:: Buduje pliki do release na pulpicie:
::   ObfuskatorVPN-Setup-<wersja>.exe - instalator (Inno Setup), glowny plik dla uzytkownikow
::   ObfuskatorVPN-<wersja>.zip       - wersja przenosna (rozpakuj i uruchom)
:: Nie zawieraja Twoich danych (data, settings.json). Zainstalowanej aplikacji nie rusza.
setlocal
set "SRC=%~dp0"
set "APP=%~dp0.."
call "%SRC%build.cmd" --bez-instalacji || exit /b 1
for /f "usebackq delims=" %%V in (`powershell -NoProfile -Command "(Select-String -Path '%SRC%version.txt' -Pattern 'ProductVersion., .([0-9.]+)').Matches[0].Groups[1].Value"`) do set "VER=%%V"
for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')"`) do set "DESK=%%D"

:: --- folder z aplikacja (wspolny dla zip i instalatora) ---
set "OUT=%TEMP%\akvpn_pakiet\ObfuskatorVPN"
if exist "%TEMP%\akvpn_pakiet" rmdir /s /q "%TEMP%\akvpn_pakiet"
mkdir "%OUT%\bin"
xcopy /e /i /q /y "%TEMP%\akvpn_dist\ObfuskatorVPN" "%OUT%" >nul || exit /b 1
copy /y "%APP%\bin\xray.exe" "%OUT%\bin\" >nul || (echo Brak bin\xray.exe & exit /b 1)
copy /y "%APP%\bin\sing-box.exe" "%OUT%\bin\" >nul || (echo Brak bin\sing-box.exe & exit /b 1)
copy /y "%APP%\app.ico" "%OUT%\" >nul || exit /b 1
copy /y "%SRC%paczka\CZYTAJ.txt" "%OUT%\" >nul
copy /y "%SRC%paczka\LICENCJE.txt" "%OUT%\" >nul
copy /y "%SRC%paczka\PRYWATNOSC.txt" "%OUT%\" >nul
xcopy /e /i /q /y "%SRC%paczka\licencje" "%OUT%\licencje" >nul || exit /b 1
copy /y "%APP%\LICENSE" "%OUT%\licencje\MIT-ObfuskatorVPN.txt" >nul

:: --- zip: zipfile z Pythona - Compress-Archive z PowerShell 5.1 zapisuje sciezki z "\" ---
python -c "import shutil,sys; shutil.make_archive(sys.argv[1], 'zip', sys.argv[2], 'ObfuskatorVPN')" "%DESK%\ObfuskatorVPN-%VER%" "%TEMP%\akvpn_pakiet" || exit /b 1
echo Gotowe: %DESK%\ObfuskatorVPN-%VER%.zip

:: --- instalator ---
set "ISCC="
for %%I in ("%LOCALAPPDATA%\Programs\Inno\ISCC.exe" "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe") do if not defined ISCC if exist %%I set "ISCC=%%~I"
if not defined ISCC (
  echo Brak Inno Setup 6 ^(winget install JRSoftware.InnoSetup^) - pomijam instalator.
  goto :sprzatanie
)
"%ISCC%" /Q "/DAppVersion=%VER%" "/DSourceDir=%OUT%" "/DOutDir=%DESK%" "%SRC%instalator.iss" || exit /b 1
echo Gotowe: %DESK%\ObfuskatorVPN-Setup-%VER%.exe

:sprzatanie
rmdir /s /q "%TEMP%\akvpn_pakiet"
