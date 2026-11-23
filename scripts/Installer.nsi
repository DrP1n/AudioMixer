; =====================================================================
;  Installer di AudioMixer (NSIS 3.x)
;
;  Non compilarlo a mano: esegui scripts\"Crea Installer.bat", che lo lancia
;  dalla cartella del progetto (/NOCD). Usa:
;     dist\AudioMixer.exe  (creato da "Crea AudioMixer.bat")
;     icon.ico, lingue\
;  e crea release\AudioMixer_Setup_<versione>.exe
; =====================================================================
Unicode True

!ifndef VERSION
  !define VERSION "0.0.0"
!endif

!define APPNAME "AudioMixer"
!define EXENAME "AudioMixer.exe"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}"
!define APP_KEY "Software\${APPNAME}"
!define RUN_KEY "Software\Microsoft\Windows\CurrentVersion\Run"
!define APPROVED_KEY "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"

Name "${APPNAME}"
OutFile "release\${APPNAME}_Setup_${VERSION}.exe"
InstallDir "$PROGRAMFILES64\${APPNAME}"
InstallDirRegKey HKLM "${APP_KEY}" "InstallDir"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
BrandingText "${APPNAME} ${VERSION}"

VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${APPNAME}"
VIAddVersionKey "FileDescription" "${APPNAME} - installer"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" " "

!include "MUI2.nsh"
!include "LogicLib.nsh"

!define MUI_ICON "icon.ico"
!define MUI_UNICON "icon.ico"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\${EXENAME}"
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchApp

!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

; La prima lingua e' quella usata se la lingua di Windows non e' tra queste.
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "Italian"
!insertmacro MUI_LANGUAGE "Spanish"
!insertmacro MUI_LANGUAGE "German"
!insertmacro MUI_LANGUAGE "Russian"

; --- Testi dell'installer nelle cinque lingue ------------------------
LangString SEC_MAIN ${LANG_ENGLISH} "AudioMixer (required)"
LangString SEC_MAIN ${LANG_ITALIAN} "AudioMixer (obbligatorio)"
LangString SEC_MAIN ${LANG_SPANISH} "AudioMixer (obligatorio)"
LangString SEC_MAIN ${LANG_GERMAN} "AudioMixer (erforderlich)"
LangString SEC_MAIN ${LANG_RUSSIAN} "AudioMixer (обязательно)"

LangString SEC_DESKTOP ${LANG_ENGLISH} "Desktop shortcut"
LangString SEC_DESKTOP ${LANG_ITALIAN} "Collegamento sul desktop"
LangString SEC_DESKTOP ${LANG_SPANISH} "Acceso directo en el escritorio"
LangString SEC_DESKTOP ${LANG_GERMAN} "Desktopverknüpfung"
LangString SEC_DESKTOP ${LANG_RUSSIAN} "Ярлык на рабочем столе"

LangString DESC_MAIN ${LANG_ENGLISH} "The program and the language files."
LangString DESC_MAIN ${LANG_ITALIAN} "Il programma e i file delle lingue."
LangString DESC_MAIN ${LANG_SPANISH} "El programa y los archivos de idioma."
LangString DESC_MAIN ${LANG_GERMAN} "Das Programm und die Sprachdateien."
LangString DESC_MAIN ${LANG_RUSSIAN} "Программа и файлы языков."

LangString DESC_DESKTOP ${LANG_ENGLISH} "Creates a shortcut to AudioMixer on the desktop."
LangString DESC_DESKTOP ${LANG_ITALIAN} "Crea un collegamento ad AudioMixer sul desktop."
LangString DESC_DESKTOP ${LANG_SPANISH} "Crea un acceso directo a AudioMixer en el escritorio."
LangString DESC_DESKTOP ${LANG_GERMAN} "Erstellt eine Verknüpfung zu AudioMixer auf dem Desktop."
LangString DESC_DESKTOP ${LANG_RUSSIAN} "Создаёт ярлык AudioMixer на рабочем столе."

LangString MSG_RUNNING ${LANG_ENGLISH} "AudioMixer is running. Click OK to close it and continue."
LangString MSG_RUNNING ${LANG_ITALIAN} "AudioMixer è in esecuzione. Premi OK per chiuderlo e continuare."
LangString MSG_RUNNING ${LANG_SPANISH} "AudioMixer se está ejecutando. Pulsa Aceptar para cerrarlo y continuar."
LangString MSG_RUNNING ${LANG_GERMAN} "AudioMixer wird ausgeführt. Klicke auf OK, um es zu schließen und fortzufahren."
LangString MSG_RUNNING ${LANG_RUSSIAN} "AudioMixer запущен. Нажмите «ОК», чтобы закрыть его и продолжить."

LangString MSG_REMOVE_DATA ${LANG_ENGLISH} "Do you also want to delete AudioMixer's saved settings, presets and hotkeys?"
LangString MSG_REMOVE_DATA ${LANG_ITALIAN} "Vuoi eliminare anche le impostazioni, i preset e i tasti salvati di AudioMixer?"
LangString MSG_REMOVE_DATA ${LANG_SPANISH} "¿Quieres eliminar también los ajustes, preajustes y atajos de teclado guardados de AudioMixer?"
LangString MSG_REMOVE_DATA ${LANG_GERMAN} "Möchtest du auch die gespeicherten Einstellungen, Presets und Tastenkürzel von AudioMixer löschen?"
LangString MSG_REMOVE_DATA ${LANG_RUSSIAN} "Удалить также сохранённые настройки, пресеты и горячие клавиши AudioMixer?"

; --- Chiude AudioMixer se e' in esecuzione (installer e disinstallatore) ---
!macro DEFINE_CLOSE_APP UN
  Function ${UN}CloseApp
    nsExec::ExecToStack 'cmd /c tasklist /FI "IMAGENAME eq ${EXENAME}" /NH | find /I "${EXENAME}"'
    Pop $0
    Pop $1
    ${If} $0 == 0
      MessageBox MB_OKCANCEL|MB_ICONEXCLAMATION "$(MSG_RUNNING)" /SD IDOK IDCANCEL cancel
      nsExec::Exec 'taskkill /F /IM ${EXENAME}'
      Sleep 800
    ${EndIf}
    Return
    cancel:
    Abort
  FunctionEnd
!macroend
!insertmacro DEFINE_CLOSE_APP ""
!insertmacro DEFINE_CLOSE_APP "un."

; Avvia il programma come utente normale (l'installer gira da amministratore).
Function LaunchApp
  Exec '"$WINDIR\explorer.exe" "$INSTDIR\${EXENAME}"'
FunctionEnd

Function .onInit
  SetRegView 64
FunctionEnd

Function un.onInit
  SetRegView 64
FunctionEnd

; --- Installazione ---------------------------------------------------
Section "$(SEC_MAIN)" SecMain
  SectionIn RO
  Call CloseApp

  SetOutPath "$INSTDIR"
  File "dist\${EXENAME}"
  File "LICENSE"
  SetOutPath "$INSTDIR\lingue"
  File "lingue\*.*"

  WriteUninstaller "$INSTDIR\Uninstall.exe"

  SetShellVarContext all
  CreateShortCut "$SMPROGRAMS\${APPNAME}.lnk" "$INSTDIR\${EXENAME}" "" "$INSTDIR\${EXENAME}" 0

  WriteRegStr HKLM "${APP_KEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayName" "${APPNAME}"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\${EXENAME}"
  WriteRegStr HKLM "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKLM "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKLM "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${UNINST_KEY}" "NoRepair" 1
SectionEnd

Section "$(SEC_DESKTOP)" SecDesktop
  SetShellVarContext all
  CreateShortCut "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\${EXENAME}" "" "$INSTDIR\${EXENAME}" 0
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecMain} "$(DESC_MAIN)"
  !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} "$(DESC_DESKTOP)"
!insertmacro MUI_FUNCTION_DESCRIPTION_END

; --- Disinstallazione ------------------------------------------------
Section "Uninstall"
  Call un.CloseApp

  SetShellVarContext all
  Delete "$INSTDIR\${EXENAME}"
  Delete "$INSTDIR\LICENSE"
  RMDir /r "$INSTDIR\lingue"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\${APPNAME}.lnk"
  Delete "$DESKTOP\${APPNAME}.lnk"
  DeleteRegKey HKLM "${UNINST_KEY}"
  DeleteRegKey HKLM "${APP_KEY}"

  ; l'avvio con Windows impostato dal programma (utente corrente)
  DeleteRegValue HKCU "${RUN_KEY}" "${APPNAME}"
  DeleteRegValue HKCU "${APPROVED_KEY}" "${APPNAME}"

  ; impostazioni, preset e tasti dell'utente: si eliminano solo se lo chiede
  SetShellVarContext current
  MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "$(MSG_REMOVE_DATA)" /SD IDNO IDNO keep_data
  RMDir /r "$APPDATA\${APPNAME}"
  keep_data:
SectionEnd
