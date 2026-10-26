param(
    # Default is now the real user directory. A junction is only needed if you
    # explicitly pass a separate ASCII path.
    [string]$Junction = "C:\Users\71008\OneDrive\文档\Paradox Interactive\Hearts of Iron IV",
    [string]$GameDir = "D:\game\steam\steamapps\common\Hearts of Iron IV"
)

$ErrorActionPreference = "Stop"
$realCandidates = @(
    (Join-Path $env:USERPROFILE "OneDrive\文档\Paradox Interactive\Hearts of Iron IV"),
    (Join-Path $env:USERPROFILE "Documents\Paradox Interactive\Hearts of Iron IV"),
    (Join-Path $env:USERPROFILE "OneDrive\Documents\Paradox Interactive\Hearts of Iron IV")
)
$real = $realCandidates | Where-Object { Test-Path (Join-Path $_ "save games") } | Select-Object -First 1
if (-not $real) {
    throw "HOI4 user directory with 'save games' was not found."
}

if (-not (Test-Path $Junction)) {
    cmd /c mklink /J "$Junction" "$real" | Out-Null
}

$settings = Join-Path $GameDir "launcher-settings.json"
if (-not (Test-Path $settings)) {
    throw "launcher-settings.json not found: $settings"
}

$backup = Join-Path $PSScriptRoot "launcher-settings.backup.json"
if (-not (Test-Path $backup)) {
    Copy-Item $settings $backup
}

$text = Get-Content $settings -Raw
$asciiPath = $Junction.Replace('\', '/')
$text = [regex]::Replace(
    $text,
    '"gameDataPath"\s*:\s*"[^"]*"',
    '"gameDataPath": "' + $asciiPath + '"'
)
[System.IO.File]::WriteAllText($settings, $text, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "Real user dir : $real"
Write-Host "User data path: $Junction"
Write-Host "gameDataPath  : $asciiPath"
Write-Host "Backup        : $backup"
