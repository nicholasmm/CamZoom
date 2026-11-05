# Gera dist\CamZoom.exe a partir do código-fonte.
# Uso (PowerShell, na pasta do projeto):  .\build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (Get-Process CamZoom -ErrorAction SilentlyContinue) {
    throw "Feche o CamZoom antes de gerar um novo .exe."
}

if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -q -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar as dependências." }

$here = (Get-Location).Path
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --windowed `
    --name CamZoom `
    --icon "$here\camzoom.ico" `
    --add-data "$here\camzoom.ico;." `
    --collect-data cv2 `
    --workpath "$here\build" --specpath "$here\build" --distpath "$here\dist" `
    "$here\camzoom.py"
if ($LASTEXITCODE -ne 0) { throw "Falha ao gerar o .exe." }

Write-Host "Pronto: $here\dist\CamZoom.exe"
