; GifPet for Windows - Inno Setup 설치 스크립트
; 빌드 후 이 파일을 Inno Setup Compiler로 컴파일하세요.
; 다운로드: https://jrsoftware.org/isdl.php

[Setup]
AppId={{A3F7C2E1-8B4D-4F6A-9E2C-1D5B7A3F8C2E}
AppName=GifPet for Windows
AppVersion=1.4.2
AppPublisher=GifPet
DefaultDirName={autopf}\GifPet
DefaultGroupName=GifPet
OutputDir=installer
OutputBaseFilename=GifPet_Setup_v1.4.2
Compression=lzma
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\GifPet.exe

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startup"; Description: "Windows 시작 시 자동 실행 (권장)"; GroupDescription: "추가 옵션:"; Flags: checkedonce
Name: "desktopicon"; Description: "바탕화면 바로가기 생성"; GroupDescription: "추가 옵션:"; Flags: unchecked

[Files]
Source: "dist\GifPet\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\GifPet"; Filename: "{app}\GifPet.exe"
Name: "{group}\제거 (Uninstall)"; Filename: "{uninstallexe}"
Name: "{commondesktop}\GifPet"; Filename: "{app}\GifPet.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "GifPet"; ValueData: """{app}\GifPet.exe"""; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\GifPet.exe"; Description: "GifPet for Windows 지금 실행"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "taskkill"; Parameters: "/f /im GifPet.exe"; Flags: runhidden; RunOnceId: "KillApp"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssInstall then
    Exec('taskkill', '/f /im GifPet.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;
