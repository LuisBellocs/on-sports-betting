<#
.SYNOPSIS
  Trae lo último del repositorio (resultados y predicciones) y descarga un modelo
  para usarlo en tu computadora. Sin -Version descarga el más reciente.

.EXAMPLE
  .\obtener_modelo.ps1                                   # el más reciente
  .\obtener_modelo.ps1 -Version modelo-20261023-0800     # una versión anterior
  .\obtener_modelo.ps1 -Lista                            # ver todas las versiones
  .\obtener_modelo.ps1 -InstalarEntorno                  # además instala las librerías exactas de esa versión
#>
param(
    [string]$Version = "",
    [switch]$Lista,
    [switch]$InstalarEntorno
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if ($Lista) { gh release list --limit 50; exit 0 }

git pull --rebase --autostash --quiet
if (-not $Version) { $Version = gh release view --json tagName --jq .tagName }
if (-not $Version) { throw "Todavía no hay ninguna versión del modelo." }

$carpeta = Join-Path $PSScriptRoot "modelo_local\$Version"
gh release download $Version --pattern "modelo_laliga.pkl" --pattern "modelo_laliga.py" --pattern "entorno.txt" --dir $carpeta --clobber
if ($InstalarEntorno -and (Test-Path "$carpeta\entorno.txt")) {
    python -m pip install -r "$carpeta\entorno.txt"
}

Write-Host ""
Write-Host "Modelo $Version descargado en modelo_local\$Version" -ForegroundColor Green
Write-Host "Ejemplo para predecir en tu computadora:"
Write-Host "  python modelo_local\$Version\modelo_laliga.py predecir --modelo modelo_local\$Version\modelo_laliga.pkl --partidos partidos\jornada10_2627.csv --salida predicciones_locales"
