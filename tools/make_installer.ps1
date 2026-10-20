# Called by build_exe.bat after the program folder dist\OwnPOS is built and self-tested.
# Asks for the installer password twice (never stored anywhere) and runs Inno Setup with it.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { $iscc = "$env:ProgramFiles\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $iscc)) {
    Write-Host "  Inno Setup 6 (6.4 or newer) is not installed: get it from https://jrsoftware.org/isdl.php"
    Write-Host "  The program folder is built in dist\OwnPOS; run build_exe.bat again after installing Inno Setup."
    exit 2
}
function Read-Plain([string]$prompt) {
    $sec = Read-Host -AsSecureString $prompt
    $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}
Write-Host ""
Write-Host "  Installer password: the setup file will not install (and its files cannot be unpacked) without it."
Write-Host "  Use 10 or more characters: letters, digits and - _ . @ # ! only. Keep it somewhere safe."
for ($i = 0; $i -lt 3; $i++) {
    $pw = Read-Plain "  Password"
    if ($pw.Length -lt 10 -or $pw -notmatch '^[A-Za-z0-9_.@#!-]+$') { Write-Host "  Not accepted: 10+ characters from the allowed set."; continue }
    $pw2 = Read-Plain "  Same password again"
    if ($pw -ne $pw2) { Write-Host "  The two passwords differ."; continue }
    & $iscc "/Q" "/DInstallPassword=$pw" "installer\OwnPOS.iss"
    $code = $LASTEXITCODE
    $pw = $null; $pw2 = $null
    exit $code
}
Write-Host "  No valid password given: no installer made."
exit 1
