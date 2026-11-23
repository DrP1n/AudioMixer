@echo off
rem Crea release\AudioMixer_Setup_<versione>.exe: il file da dare agli utenti.
cd /d "%~dp0.."

set "MAKENSIS="
if exist "%ProgramFiles(x86)%\NSIS\makensis.exe" set "MAKENSIS=%ProgramFiles(x86)%\NSIS\makensis.exe"
if exist "%ProgramFiles%\NSIS\makensis.exe" set "MAKENSIS=%ProgramFiles%\NSIS\makensis.exe"
if not defined MAKENSIS (
  echo NSIS non trovato.
  echo Installalo da https://nsis.sourceforge.io/Download e rilancia questo file.
  pause
  exit /b 1
)

if not exist "dist\AudioMixer.exe" (
  echo Manca dist\AudioMixer.exe: esegui prima "Crea AudioMixer.bat".
  pause
  exit /b 1
)
if not exist "lingue\en.json" (
  echo Manca la cartella "lingue" nella cartella del progetto.
  pause
  exit /b 1
)
if not exist "icon.ico" (
  echo Manca icon.ico.
  pause
  exit /b 1
)

set "VERSION=0.0.0"
for /f "tokens=2 delims==" %%a in ('findstr /b /c:"APP_VERSION =" mixer.pyw 2^>nul') do set "VERSION=%%a"
set "VERSION=%VERSION: =%"
set "VERSION=%VERSION:"=%"

if not exist release mkdir release
call :sign "dist\AudioMixer.exe"
rem /NOCD: i percorsi in Installer.nsi partono dalla cartella del progetto, non da scripts\
"%MAKENSIS%" /NOCD /DVERSION=%VERSION% scripts\Installer.nsi
if errorlevel 1 (
  echo.
  echo Errore nella creazione dell'installer.
  pause
  exit /b 1
)

call :sign "release\AudioMixer_Setup_%VERSION%.exe"

rem versione portatile: exe + lingue + portable.txt (le impostazioni restano nella cartella data\)
set "PORT=dist\portable\AudioMixer"
if exist dist\portable rmdir /s /q dist\portable
mkdir "%PORT%\lingue"
copy /y dist\AudioMixer.exe "%PORT%\" >nul
copy /y lingue\*.json "%PORT%\lingue\" >nul
echo Versione portatile: le impostazioni vengono salvate nella cartella data qui accanto.> "%PORT%\portable.txt"
powershell -NoProfile -Command "Compress-Archive -Force -Path '%PORT%' -DestinationPath 'release\AudioMixer_Portable_%VERSION%.zip'"

rem pulizia: dist\ serviva solo a costruire; in release\ resta solo l'ultima versione
rmdir /s /q dist
for %%f in (release\AudioMixer_Setup_*.exe release\AudioMixer_Portable_*.zip) do (
  if /i not "%%~nxf"=="AudioMixer_Setup_%VERSION%.exe" if /i not "%%~nxf"=="AudioMixer_Portable_%VERSION%.zip" del /q "%%f"
)

echo.
echo Creati: release\AudioMixer_Setup_%VERSION%.exe
echo         release\AudioMixer_Portable_%VERSION%.zip
pause
exit /b 0

rem Firma un file se e' impostato AUDIOMIXER_PFX (certificato .pfx) e signtool e' nel PATH.
rem Senza firma, Smart App Control e SmartScreen avvisano o bloccano il programma.
:sign
if not defined AUDIOMIXER_PFX exit /b 0
signtool sign /f "%AUDIOMIXER_PFX%" /p "%AUDIOMIXER_PFX_PASSWORD%" /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 %1
exit /b 0