; Script generated for CNSlicer Subtractive Studio
; Professional Windows Installer matching Creality Print / Ultimaker Cura / PrusaSlicer standard

#define MyAppName "CNSlicer"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "CNSlicer Studio"
#define MyAppURL "https://github.com/CNSlicer/CNSlicer"
#define MyAppExeName "CNSlicer.exe"

[Setup]
; App Metadata
AppId={{9F5724B3-4A1C-4B92-80D7-E77A6C18E9B2}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; Installation directories
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes

; Allow user to install for current user or all users (UAC dialog if requested)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; Output configuration
OutputDir=..\dist
OutputBaseFilename=CNSlicer-Setup-v1.0-Windows-x64
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\assets\icon.ico

; Compression settings (ultra high compression)
Compression=lzma2/ultra64
InternalCompressLevel=ultra64
SolidCompression=yes

; Modern Wizard UI
WizardStyle=modern
DisableDirPage=no
DisableWelcomePage=no
LicenseFile=..\LICENSE

; Architecture: 64-bit Windows
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; Changes associations & environment
ChangesAssociations=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "quicklaunchicon"; Description: "{cm:CreateQuickLaunchIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked; OnlyBelowVersion: 6.1; Check: not IsAdminInstallMode
Name: "associate_stl"; Description: "Associate 3D STL files (*.stl) with CNSlicer"; GroupDescription: "File Associations:"; Flags: checkedonce
Name: "associate_gcode"; Description: "Associate G-Code toolpath files (*.gcode, *.nc) with CNSlicer"; GroupDescription: "File Associations:"; Flags: unchecked

[Files]
Source: "..\dist\CNSlicer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\icon.ico"
Name: "{autoprograms}\{#MyAppName}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\icon.ico"; Tasks: desktopicon

[Registry]
; Associate .stl files
Root: HKA; Subkey: "Software\Classes\.stl"; ValueType: string; ValueName: ""; ValueData: "CNSlicer.STLFile"; Flags: uninsdeletevalue; Tasks: associate_stl
Root: HKA; Subkey: "Software\Classes\CNSlicer.STLFile"; ValueType: string; ValueName: ""; ValueData: "STL 3D Model"; Flags: uninsdeletekey; Tasks: associate_stl
Root: HKA; Subkey: "Software\Classes\CNSlicer.STLFile\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\assets\icon.ico,0"; Tasks: associate_stl
Root: HKA; Subkey: "Software\Classes\CNSlicer.STLFile\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: associate_stl

; Associate .gcode files
Root: HKA; Subkey: "Software\Classes\.gcode"; ValueType: string; ValueName: ""; ValueData: "CNSlicer.GCodeFile"; Flags: uninsdeletevalue; Tasks: associate_gcode
Root: HKA; Subkey: "Software\Classes\CNSlicer.GCodeFile"; ValueType: string; ValueName: ""; ValueData: "CNC G-Code Toolpath"; Flags: uninsdeletekey; Tasks: associate_gcode
Root: HKA; Subkey: "Software\Classes\CNSlicer.GCodeFile\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\assets\icon.ico,0"; Tasks: associate_gcode
Root: HKA; Subkey: "Software\Classes\CNSlicer.GCodeFile\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: associate_gcode

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
