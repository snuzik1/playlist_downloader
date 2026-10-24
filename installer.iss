#define MyAppName "PLDL"
#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif
#define MyAppPublisher "PLDL"
#define MyAppExeName "PLDL.exe"

[Setup]
AppId={{16896A40-C283-4FDF-9EAB-3DE218F90612}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\PLDL
DefaultGroupName=PLDL
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=PLDL-Setup-{#MyAppVersion}
SetupIconFile=assets\pldl.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
WizardImageFile=assets\installer-large.bmp
WizardSmallImageFile=assets\installer-small.bmp
WizardImageStretch=no
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=PLDL — загрузчик плейлистов
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
AppMutex=Local\PLDL_Desktop_SingleInstance
SetupLogging=yes
UsePreviousAppDir=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительно:"; Flags: checkedonce

[Files]
Source: "PLDL.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "NOTICE"; DestDir: "{app}"; Flags: ignoreversion
Source: "THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\PLDL"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Comment: "Загрузчик плейлистов"
Name: "{autodesktop}\PLDL"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Comment: "Загрузчик плейлистов"; Tasks: desktopicon
Name: "{group}\Удалить PLDL"; Filename: "{uninstallexe}"; Comment: "Удалить PLDL с компьютера"

[Run]
Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Description: "Запустить PLDL"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
function FFmpegFound(): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(
    ExpandConstant('{sys}\cmd.exe'),
    '/c where ffmpeg >nul 2>&1',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  ) and (ResultCode = 0);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and (not WizardSilent) then
  begin
    if not FFmpegFound() then
      MsgBox(
        'FFmpeg не найден.' + #13#10 + #13#10 +
        'Он нужен для MP3/M4A, обложек и тегов. Установите его командой:' + #13#10 + #13#10 +
        'winget install Gyan.FFmpeg' + #13#10 + #13#10 +
        'После установки перезапустите PLDL. Путь к ffmpeg.exe также можно указать в настройках приложения.',
        mbInformation,
        MB_OK
      );
  end;
end;
