param(
    [string]$PythonExe = ".venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $PythonExe)) {
    throw "Python executable not found at $PythonExe"
}

& $PythonExe -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name "OrionStarPH" `
    --add-data "app\config.json;app" `
    --add-data "app\config.example.json;app" `
    app\orionstar_gui.py