param(
    [string]$UserDir = "",
    [string]$ModSource = "",
    [string]$SettingsFile = ""
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir

function Resolve-ProjectPath([string]$Path) {
    if (-not $Path) { return "" }
    if (-not [System.IO.Path]::IsPathRooted($Path)) {
        $Path = Join-Path $repoRoot $Path
    }
    return [System.IO.Path]::GetFullPath($Path)
}

if (-not $SettingsFile) {
    $SettingsFile = Join-Path $repoRoot "settings.json"
}
if (Test-Path $SettingsFile) {
    try {
        $raw = [System.IO.File]::ReadAllText($SettingsFile, [System.Text.Encoding]::UTF8)
        $settings = $raw | ConvertFrom-Json
        if (-not $UserDir -and $settings.hoi4_user_dir) {
            $UserDir = [string]$settings.hoi4_user_dir
        }
        if (-not $ModSource -and $settings.mod_source_dir) {
            $ModSource = [string]$settings.mod_source_dir
        }
    } catch {
        throw "无法读取 settings.json：$SettingsFile`n$($_.Exception.Message)"
    }
}

$UserDir = Resolve-ProjectPath $UserDir
$ModSource = Resolve-ProjectPath $ModSource
if (-not $ModSource) {
    $ModSource = Join-Path $repoRoot "naval-tactical-command"
}

function Find-Hoi4UserDir {
    $userHome = $env:USERPROFILE
    $candidates = @(
        (Join-Path $userHome "OneDrive\文档\Paradox Interactive\Hearts of Iron IV"),
        (Join-Path $userHome "OneDrive\Documents\Paradox Interactive\Hearts of Iron IV"),
        (Join-Path $userHome "Documents\Paradox Interactive\Hearts of Iron IV"),
        (Join-Path $userHome "文档\Paradox Interactive\Hearts of Iron IV"),
        (Join-Path $userHome "OneDrive\ドキュメント\Paradox Interactive\Hearts of Iron IV")
    )
    foreach ($candidate in $candidates) {
        if ((Test-Path (Join-Path $candidate "save games")) -or (Test-Path (Join-Path $candidate "logs"))) {
            return $candidate
        }
    }
    return ""
}

if (-not $UserDir) {
    $UserDir = Find-Hoi4UserDir
}
if (-not $UserDir -or -not (Test-Path $UserDir)) {
    throw "找不到 HOI4 用户目录。请在 settings.json 的 hoi4_user_dir 填写，或运行 设置路径.bat。"
}

# If no separate source tree is available, reuse the installed .mod path or
# the installed copy. This lets a portable package repair an existing install.
if (-not (Test-Path $ModSource)) {
    $installedModDir = Join-Path $UserDir "mod\naval-tactical-command"
    $installedModFile = Join-Path $UserDir "mod\naval-tactical-command.mod"
    if (Test-Path $installedModFile) {
        $modText = [System.IO.File]::ReadAllText($installedModFile, [System.Text.Encoding]::UTF8)
        $match = [regex]::Match($modText, '(?m)^\s*path\s*=\s*"([^"]+)"')
        if ($match.Success) {
            $candidate = $match.Groups[1].Value.Replace('/', '\')
            if (-not [System.IO.Path]::IsPathRooted($candidate)) {
                $candidate = Join-Path $UserDir $candidate
            }
            if (Test-Path $candidate) { $ModSource = $candidate }
        }
    }
    if (-not (Test-Path $ModSource) -and (Test-Path $installedModDir)) {
        $ModSource = $installedModDir
    }
}
if (-not (Test-Path $ModSource)) {
    throw "找不到 mod 源目录：$ModSource`n请在 settings.json 的 mod_source_dir 填写 naval-tactical-command 文件夹。"
}

$modDir = Join-Path $UserDir "mod\naval-tactical-command"
$modFile = Join-Path $UserDir "mod\naval-tactical-command.mod"

Write-Host "HOI4 用户目录: $UserDir"
Write-Host "安装 mod 到:  $modDir"

if (Test-Path $modDir) {
    Remove-Item $modDir -Recurse -Force
}
New-Item -ItemType Directory -Path $modDir -Force | Out-Null
Copy-Item (Join-Path $ModSource "*") $modDir -Recurse -Force

# HOI4 can read the .mod file from the real (possibly Chinese) user directory,
# but PhysFS cannot reliably mount the mod files themselves from that path.
# Point path= at the ASCII mod source tree instead.
$pathForMod = ($ModSource -replace '\\', '/')
if ($pathForMod -match '[^\x00-\x7F]') {
    Write-Warning "mod 路径包含非 ASCII 字符，HOI4/PhysFS 可能无法挂载：$ModSource"
    Write-Warning "建议把 mod 复制到纯英文路径，并在 settings.json 的 mod_source_dir 中填写。"
}
$descriptor = @"
version="0.1.0"
tags={
	"Gameplay"
	"Military"
}
name="Naval Tactical Command (Local)"
supported_version="1.19.*"
path="$pathForMod"
"@
# Write without a BOM: the launcher expects `version=` as the first bytes.
[System.IO.File]::WriteAllText($modFile, $descriptor, (New-Object System.Text.UTF8Encoding($false)))

Write-Host ""
Write-Host "安装完成。" -ForegroundColor Green
Write-Host "1. 打开 HOI4 启动器，在播放集里启用 'Naval Tactical Command (Local) / 海军战术指挥（本地）'。"
Write-Host "   注意：不要选 Steam 创意工坊那个同名条目；这里要用本地条目。"
Write-Host "2. 推荐同时把存档设置改为文本存档（settings.txt 中 save_as_binary=no）。"
Write-Host "3. 进入游戏前先双击 run_bridge.bat 启动桥接程序。"
Write-Host ""
Write-Host "mod 文件: $modFile"

