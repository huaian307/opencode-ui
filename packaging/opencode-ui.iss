; opencode-ui 安装脚本（Inno Setup 6）
;
; 用法（由 packaging/build.py 调用）：
;   ISCC.exe /DAppVersion=260926zk /DPayloadDir=<dist\payload> ^
;            /DOutputExe=<dist\opencode-ui-setup-x.exe> /DComponents=music,audio opencode-ui.iss
;
; 设计要点：
;   * **per-user 安装**（PrivilegesRequired=lowest）→ 不弹 UAC，装到 %LOCALAPPDATA%\Programs
;   * 可选组件用 [Components]：音乐服务 / 音频频谱 —— **默认都不勾**（要用户自己选）
;   * 随包 agent（node + codex-acp）默认勾上（否则装完没有可用 agent）
;   * 装完调 init_state.py 写 runtime/state（agent 注册表 + 默认引擎），**不写任何密钥**
;   * 卸载时先按端口杀掉面板/守护进程，再删自己的目录；**不碰**用户的 OpenCode 与其它程序

#define AppName "opencode-ui"
#define AppPublisher "opencode-ui"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef PayloadDir
  #define PayloadDir "..\dist\payload"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

[Setup]
AppId={{8E31A0F1-6C2B-4F4A-9E7D-3B2C1A5D9E01}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputBaseFilename={#AppName}-setup-{#AppVersion}
OutputDir={#OutputDir}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName} 面板
MinVersion=10.0
DisableWelcomePage=no

[Languages]
Name: "chinese"; MessagesFile: "compiler:Default.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

; —— 可选组件的 payload 只有 build.py --components 备了才存在，这里按需声明 ——
#define HaveMusic DirExists(AddBackslash(PayloadDir) + "components\music\site-packages")
#define HaveAudio DirExists(AddBackslash(PayloadDir) + "components\audio\site-packages")
#define HaveAgent FileExists(AddBackslash(PayloadDir) + "agents\node\node.exe")
#define HaveCodex DirExists(AddBackslash(PayloadDir) + "agents\codex\node_modules")
#define HaveClaude DirExists(AddBackslash(PayloadDir) + "agents\claude\node_modules")

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: checkedonce

[Components]
Name: "core";    Description: "核心（面板 + 后端 + 嵌入式 Python）"; Types: full compact custom; Flags: fixed
#if HaveAgent
Name: "agent_node";   Description: "Node 运行时（两个 agent 适配器共用）"; Types: full
#endif
#if HaveCodex
Name: "agent_codex";  Description: "Codex 适配器 + 自带 codex CLI（较大）"; Types: full
#endif
#if HaveClaude
Name: "agent_claude"; Description: "Claude Code 适配器（不含 Claude 本体；也可后装）"; Types: full
#endif
#if HaveMusic
Name: "music";   Description: "可选：音乐服务（网易云/QQ 搜歌放歌）"; Types: custom
#endif
#if HaveAudio
Name: "audio";   Description: "可选：音频频谱（真频谱可视化）"; Types: custom
#endif

[Files]
; —— 核心（必装）——
Source: "{#PayloadDir}\python\*";        DestDir: "{app}\python";   Flags: ignoreversion recursesubdirs createallsubdirs; Components: core
Source: "{#PayloadDir}\app\*";           DestDir: "{app}";          Flags: ignoreversion recursesubdirs createallsubdirs; Components: core
Source: "{#PayloadDir}\init_state.py";   DestDir: "{app}";          Flags: ignoreversion; Components: core
Source: "{#PayloadDir}\VERSION";         DestDir: "{app}";          Flags: ignoreversion; Components: core
; —— 随包 agent ——
#ifdef HaveAgent
Source: "{#PayloadDir}\agents\node\*";    DestDir: "{app}\agents\node";   Flags: ignoreversion recursesubdirs createallsubdirs; Components: agent_node
#endif
#ifdef HaveCodex
Source: "{#PayloadDir}\agents\codex\*";   DestDir: "{app}\agents\codex";  Flags: ignoreversion recursesubdirs createallsubdirs; Components: agent_codex
#endif
#ifdef HaveClaude
Source: "{#PayloadDir}\agents\claude\*";  DestDir: "{app}\agents\claude"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: agent_claude
#endif
; —— 可选组件（纯 site-packages；由 _component_python() 用自带解释器 + PYTHONPATH 跑）——
#if HaveMusic
Source: "{#PayloadDir}\components\music\site-packages\*"; DestDir: "{app}\runtime\site-packages\music"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: music
#endif
#if HaveAudio
Source: "{#PayloadDir}\components\audio\site-packages\*"; DestDir: "{app}\runtime\site-packages\audio"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: audio
#endif

[Icons]
Name: "{group}\opencode-ui 面板";     Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launchers\launch_opencode.py"""; WorkingDir: "{app}"; Comment: "打开 opencode-ui 面板"
Name: "{group}\opencode-ui 自检";     Filename: "{app}\python\python.exe";  Parameters: """{app}\tools\selftest.py"" --app-dir ""{app}"""; WorkingDir: "{app}"; Comment: "检查安装是否完整"
Name: "{group}\卸载 opencode-ui";     Filename: "{uninstallexe}"
Name: "{autodesktop}\opencode-ui";    Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launchers\launch_opencode.py"""; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; 初始化运行时状态（写 agent 注册表 + 默认引擎；不写密钥）
Filename: "{app}\python\python.exe"; Parameters: """{app}\init_state.py"" --app-dir ""{app}"""; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; StatusMsg: "初始化配置…"
; 装完直接打开面板
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launchers\launch_opencode.py"""; WorkingDir: "{app}"; Description: "立即打开 opencode-ui 面板"; Flags: postinstall nowait skipifsilent

[UninstallRun]
; 先按端口停掉面板服务与守护进程（谁占端口谁就是我们的），再清理残留辅助进程
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -Command ""foreach ($p in 8788,8787,8790,17888,17887,17990) {{ $q = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess; if ($q) {{ Stop-Process -Id $q -Force -ErrorAction SilentlyContinue }} }}"""; Flags: runhidden; RunOnceId: "killports"
Filename: "{app}\python\python.exe"; Parameters: """{app}\backend\cleanup.py"""; Flags: runhidden; RunOnceId: "cleanup"; Check: FileExists(ExpandConstant('{app}\backend\cleanup.py'))
Filename: "{app}\python\python.exe"; Parameters: """{app}\backend\taskbar.py"" restore"; Flags: runhidden; RunOnceId: "taskbar"

[UninstallDelete]
; 运行时产物（日志/状态/浏览器 profile/缓存）随卸载删掉；用户自己的文件不在里面
Type: filesandordirs; Name: "{app}\runtime"
; 随包 codex 运行后自己生成的 state（config.toml / sqlite / logs）也要清掉
Type: filesandordirs; Name: "{app}\agents\codex\codex-home"
; 音乐服务用 diskcache 落的缓存（实测卸载后会留 cache\cache.db*）
Type: filesandordirs; Name: "{app}\cache"
; Python 运行时产生的字节码目录（启动器也设了 PYTHONDONTWRITEBYTECODE，这里是双保险）
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\backend\__pycache__"
Type: filesandordirs; Name: "{app}\backend\engines\__pycache__"
Type: filesandordirs; Name: "{app}\backend\engines\acp\__pycache__"
Type: filesandordirs; Name: "{app}\python\__pycache__"
Type: filesandordirs; Name: "{app}\tools\__pycache__"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;

// 勾了任一「适配器」组件就必须带上 node 运行时（两个适配器都靠它跑）
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpSelectComponents then
  begin
    if WizardIsComponentSelected('agent_codex') or WizardIsComponentSelected('agent_claude') then
      WizardSelectComponents('agent_node');
  end;
end;
