; Installatore di Jarvis (Inno Setup 6). Si costruisce con: python scripts/build_installer.py
;
; Per utente, senza permessi di amministratore, in %LOCALAPPDATA%\Programs\Jarvis. Mette il codice (solo i file del
; repository) e uv; poi prepara Python e le librerie con le versioni esatte di uv.lock, dentro la cartella di Jarvis
; (tools\python), così la disinstallazione toglie davvero tutto. Motore e modelli li scarica il primo avvio, che
; parte da solo all'accensione: qui non si scarica niente di pesante.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef Staging
  #define Staging "..\build\installer"
#endif
#ifndef OutputDir
  #define OutputDir "..\build"
#endif

[Setup]
AppId={{6C1D4F2A-9B3E-4E57-A8D2-3F0B7C5E1A94}
AppName=Jarvis
AppVersion={#AppVersion}
AppVerName=Jarvis {#AppVersion}
AppPublisher=Jarvis
DefaultDirName={localappdata}\Programs\Jarvis
DefaultGroupName=Jarvis
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=Jarvis-Setup-{#AppVersion}
SetupIconFile={#Staging}\app\jarvis\ui\jarvis.ico
UninstallDisplayIcon={app}\jarvis\ui\jarvis.ico
UninstallDisplayName=Jarvis
WizardStyle=modern dark
WizardImageFile={#Staging}\images\wizard-100.bmp,{#Staging}\images\wizard-125.bmp,{#Staging}\images\wizard-150.bmp
WizardSmallImageFile={#Staging}\images\small-100.bmp,{#Staging}\images\small-125.bmp,{#Staging}\images\small-150.bmp
Compression=lzma2/ultra64
SolidCompression=yes
ShowLanguageDialog=auto
CloseApplications=yes

[Languages]
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
it.Preparing=Preparo Jarvis
it.PreparingWhat=Scarico Python e le librerie nelle versioni giuste. Qualche minuto, dipende dalla connessione.
it.PreparingFailed=Non sono riuscito a preparare Python e le librerie (codice %1). Controlla la connessione e riprova a installare: si riprende da dove si era fermato. Il dettaglio è in %2.
it.Launch=Avvia Jarvis (parte il primo avvio)
it.KeepData=Tenere i dati di Jarvis (memoria, voci, impostazioni) e i modelli scaricati?%n%nSì: una nuova installazione li ritrova. No: si cancella tutto.
en.Preparing=Preparing Jarvis
en.PreparingWhat=Downloading Python and the libraries in the right versions. A few minutes, depending on your connection.
en.PreparingFailed=I couldn't prepare Python and the libraries (code %1). Check your connection and run the installer again: it picks up where it stopped. Details are in %2.
en.Launch=Start Jarvis (the first launch begins)
en.KeepData=Keep Jarvis's data (memory, voices, settings) and the downloaded models?%n%nYes: a new installation will find them again. No: everything is deleted.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#Staging}\app\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#Staging}\tools\uv.exe"; DestDir: "{app}\tools"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Jarvis"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: """{app}\avvia_jarvis.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\jarvis\ui\jarvis.ico"
Name: "{autodesktop}\Jarvis"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: """{app}\avvia_jarvis.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\jarvis\ui\jarvis.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: """{app}\avvia_jarvis.pyw"""; WorkingDir: "{app}"; Description: "{cm:Launch}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\.git"
Type: files; Name: "{app}\jarvis.bundle"
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\jarvis"
Type: filesandordirs; Name: "{app}\scripts"
Type: filesandordirs; Name: "{app}\tests"

[Code]
var
  Prep: TOutputProgressWizardPage;

function PrepareLog(): String;
begin
  Result := ExpandConstant('{app}\data\logs\installazione.log');
end;

{ uv sync con Python e cache dentro la cartella di Jarvis; senza finestra, l'uscita nel registro }
function SyncLibraries(): Integer;
var
  Cmd: String;
  Code: Integer;
begin
  ForceDirectories(ExpandConstant('{app}\data\logs'));
  Cmd := '/c set "UV_PYTHON_INSTALL_DIR=' + ExpandConstant('{app}\tools\python') + '" && ' +
         'set "UV_LINK_MODE=copy" && ' +
         '"' + ExpandConstant('{app}\tools\uv.exe') + '" sync --frozen --no-dev --no-cache --python-preference only-managed ' +
         '> "' + PrepareLog() + '" 2>&1';
  if not Exec(ExpandConstant('{cmd}'), Cmd, ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code) then
    Code := -1;
  Result := Code;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    Prep := CreateOutputProgressPage(CustomMessage('Preparing'), CustomMessage('PreparingWhat'));
    Prep.ProgressBar.Style := npbstMarquee;
    Prep.Show;
    try
      Code := SyncLibraries();
    finally
      Prep.Hide;
    end;
    if Code <> 0 then
      MsgBox(FmtMessage(CustomMessage('PreparingFailed'), [IntToStr(Code), PrepareLog()]), mbError, MB_OK);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
begin
  { uv lascia alcuni file in sola lettura: senza questo resterebbero dopo la disinstallazione }
  if CurUninstallStep = usUninstall then
    Exec(ExpandConstant('{cmd}'), '/c attrib -r -s -h "' + ExpandConstant('{app}\tools\*') + '" /s /d', '', SW_HIDE,
         ewWaitUntilTerminated, Code);
  { senza finestre (disinstallazione silenziosa) i dati restano sempre: cancellarli va chiesto }
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    if MsgBox(CustomMessage('KeepData'), mbConfirmation, MB_YESNO) = IDNO then
    begin
      DelTree(ExpandConstant('{app}\data'), True, True, True);
      DelTree(ExpandConstant('{app}\models'), True, True, True);
      DelTree(ExpandConstant('{app}\engine'), True, True, True);
      DelTree(ExpandConstant('{app}'), True, True, True);
    end;
end;
