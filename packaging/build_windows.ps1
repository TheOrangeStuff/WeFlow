# Build dist\WeFlow\ and dist\WeFlow-Setup-<version>.exe. Run from the repo root.
#
# Needs Inno Setup 6 (ISCC.exe on PATH or in its default folder).
# Optional code signing, read from the environment (NFR-07):
#   WINDOWS_CERT_FILE      path to a .pfx code-signing certificate
#   WINDOWS_CERT_PASSWORD  its password
# Without them the installer is unsigned (SmartScreen will warn).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$version = python -c "import WeFlow; print(WeFlow.__version__)"
if (Test-Path build) { Remove-Item build -Recurse -Force }
if (Test-Path dist) { Remove-Item dist -Recurse -Force }
pyinstaller --noconfirm packaging/WeFlow.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

function Sign-File([string]$path) {
    if ($env:WINDOWS_CERT_FILE -and $env:WINDOWS_CERT_PASSWORD) {
        $signtool = (Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" |
            Sort-Object FullName | Select-Object -Last 1).FullName
        & $signtool sign /f $env:WINDOWS_CERT_FILE /p $env:WINDOWS_CERT_PASSWORD `
            /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $path
        if ($LASTEXITCODE -ne 0) { throw "Signing $path failed" }
    } else {
        Write-Host "WINDOWS_CERT_FILE not set: leaving $path unsigned"
    }
}

Sign-File "dist\WeFlow\WeFlow.exe"

$iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
& $iscc "/DAppVersion=$version" packaging\installer.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

Sign-File "dist\WeFlow-Setup-$version.exe"

# In-app updates check the download against this (FR-44). Written after signing.
$hash = (Get-FileHash "dist\WeFlow-Setup-$version.exe" -Algorithm SHA256).Hash.ToLower()
Set-Content -Path "dist\WeFlow-Setup-$version.exe.sha256" -Value "$hash  WeFlow-Setup-$version.exe" -Encoding ascii -NoNewline
Write-Host "Built dist\WeFlow-Setup-$version.exe"
