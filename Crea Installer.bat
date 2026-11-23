@echo off
cd /d "%~dp0"

set "MAKENSIS="
if exist "%ProgramFiles(x86)%\NSIS\makensis.exe" set "MAKENSIS=%ProgramFiles(x86)%\NSIS\makensis.exe"
if exist "%ProgramFiles%\NSIS\makensis.exe" set "MAKENSIS=%ProgramFiles%\NSIS\makensis.exe"
if not defined MAKENSIS (
  echo NSIS non trovato.
  echo Installalo da https://nsis.sourceforge.io/Download e rilancia questo file.
  pause
  exit /b 1
)

if not exist "AudioMixer.exe" (
  echo Manca AudioMixer.exe: esegui prima "Crea AudioMixer.bat".
  pause
  exit /b 1
)
if not exist "lingue\en.json" (
  echo Manca la cartella "lingue" accanto a questo file.
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

"%MAKENSIS%" /DVERSION=%VERSION% Installer.nsi
if errorlevel 1 (
  echo.
  echo Errore nella creazione dell'installer.
  pause
  exit /b 1
)

echo.
echo Creato: AudioMixer_Setup_%VERSION%.exe
pause