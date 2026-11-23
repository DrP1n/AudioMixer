@echo off
cd /d "%~dp0"
python -m pip install --quiet pyinstaller
python -m PyInstaller --onefile --windowed --noconfirm --name AudioMixer --icon icon.ico --add-data "icon.ico;." mixer.pyw
if exist dist\AudioMixer.exe (
    move /y dist\AudioMixer.exe AudioMixer.exe >nul
    rmdir /s /q build
    rmdir /s /q dist
    del /q AudioMixer.spec
    echo.
    echo Fatto! AudioMixer.exe e pronto in questa cartella.
) else (
    echo.
    echo Errore durante la creazione. Copia il messaggio qui sopra.
)
pause