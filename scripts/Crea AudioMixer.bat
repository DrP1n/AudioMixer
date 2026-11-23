@echo off
rem Crea dist\AudioMixer.exe dal codice nella cartella principale del progetto.
cd /d "%~dp0.."
python -m pip install --quiet pyinstaller
python -m PyInstaller --onefile --windowed --noconfirm --name AudioMixer --icon icon.ico --add-data "icon.ico;." mixer.pyw
if exist dist\AudioMixer.exe (
    rmdir /s /q build
    del /q AudioMixer.spec
    echo.
    echo Fatto! L'exe e' in dist\AudioMixer.exe. Ora esegui "Crea Installer.bat".
) else (
    echo.
    echo Errore durante la creazione. Copia il messaggio qui sopra.
)
pause
