#ifndef MyAppVersion
  #error MyAppVersion must be supplied by build_windows.ps1
#endif
#ifndef BuildRoot
  #error BuildRoot must be supplied by build_windows.ps1
#endif
#ifndef ReleaseRoot
  #error ReleaseRoot must be supplied by build_windows.ps1
#endif
#ifndef DmsIconFile
  #error DmsIconFile must point to the official DMS icon
#endif

#define MyAppName "Data Mask Studio 中文团队版"
#define MyAppExeName "DataMaskStudio.exe"

[Setup]
AppId={{A99ABF1D-AF79-4F18-9C01-AF80EC65B100}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Data Mask Studio
VersionInfoVersion={#MyAppVersion}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoDescription=Data Mask Studio Setup
VersionInfoProductName=Data Mask Studio
DefaultDirName={localappdata}\Programs\Data Mask Studio Team
DefaultGroupName=Data Mask Studio 中文团队版
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
LicenseFile={#BuildRoot}\LICENSE.txt
InfoBeforeFile={#BuildRoot}\团队使用说明.txt
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#ReleaseRoot}
OutputBaseFilename=DataMaskStudio-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
RestartApplications=no
SetupMutex=DataMaskStudio-Team-Setup-Mutex-A99ABF1D
UsePreviousAppDir=yes
SetupIconFile={#DmsIconFile}

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked

[Files]
Source: "{#BuildRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Data Mask Studio 中文团队版"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Data Mask Studio 中文团队版"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "运行 Data Mask Studio 中文团队版"; Flags: nowait postinstall skipifsilent unchecked

; Não há seção [UninstallDelete] para %LOCALAPPDATA%\DataMaskStudio.
; O cofre, as chaves, os perfis e as configurações locais são preservados.
