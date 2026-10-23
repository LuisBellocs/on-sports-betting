<#
.SYNOPSIS
  Sube a GitHub todo lo que cambiaste (partidos nuevos, ajustes.csv, el código...).
  GitHub reacciona solo:
    - un CSV nuevo o modificado en partidos\  -> predice esos partidos (unos 5 minutos)
    - cambios en ajustes.csv o modelo_laliga.py -> reentrena y crea una versión nueva

.EXAMPLE
  .\subir_cambios.ps1
  .\subir_cambios.ps1 -Mensaje "Jornada 10 con lesiones"
#>
param([string]$Mensaje = "")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

git pull --rebase --autostash --quiet
git add -A
$cambios = @(git diff --cached --name-only)
if ($cambios.Count -eq 0) {
    Write-Host "No hay cambios que subir." -ForegroundColor Yellow
    exit 0
}
Write-Host "Archivos a subir:" -ForegroundColor Cyan
$cambios | ForEach-Object { Write-Host "  $_" }

if (-not $Mensaje) { $Mensaje = "Actualización $(Get-Date -Format 'yyyy-MM-dd HH:mm')" }
git commit -m $Mensaje --quiet
git push --quiet
if ($LASTEXITCODE -ne 0) { throw "No se pudo subir. Revisa tu conexión o tus permisos en el repositorio." }

$repo = gh repo view --json nameWithOwner --jq .nameWithOwner
Write-Host ""
Write-Host "Subido." -ForegroundColor Green
if ($cambios | Where-Object { $_ -match '^partidos/[^/]+\.csv$' }) {
    Write-Host "  -> Predicción en marcha. Resultados en predicciones\ en unos 5 minutos (después: git pull)."
}
if ($cambios | Where-Object { $_ -in @("modelo_laliga.py", "ajustes.csv", "requirements.txt") }) {
    Write-Host "  -> Reentrenamiento en marcha. Nueva versión en unos 10 minutos."
}
Write-Host "  Progreso: https://github.com/$repo/actions"
