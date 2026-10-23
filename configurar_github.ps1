<#
.SYNOPSIS
  Sube el modelo y sus automatizaciones a GitHub. Se ejecuta UNA sola vez.
  - Si el repositorio NO existe, lo crea (privado salvo -Publico) en tu cuenta.
  - Si YA existe (por ejemplo, uno que te compartió un amigo), agrega los archivos sin borrar
    lo que ya tiene. Necesitas permiso de escritura (Write) en ese repositorio.

.EXAMPLE
  # repositorio nuevo en tu cuenta
  pwsh -ExecutionPolicy Bypass -File .\configurar_github.ps1 -Repo laliga-modelo

  # repositorio que ya existe (de otra persona u organización)
  pwsh -ExecutionPolicy Bypass -File .\configurar_github.ps1 -Repo su-usuario/su-repositorio

.NOTES
  Necesita Git (winget install Git.Git) y GitHub CLI (winget install GitHub.cli).
  Por defecto trabaja en una carpeta fuera de OneDrive (OneDrive y Git no se llevan bien):
  $HOME\<nombre del repositorio>. Se puede cambiar con -Destino.
#>
param(
    [Parameter(Mandatory = $true)][string]$Repo,
    [string]$Destino = "",
    [switch]$Publico
)
$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding $false

function Requiere($cmd, $ayuda) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        throw "Falta '$cmd'. $ayuda Después cierra y vuelve a abrir PowerShell."
    }
}
Requiere git "Instálalo con: winget install Git.Git"
Requiere gh  "Instálalo con: winget install GitHub.cli"

# ---------------------------------------------------------------------------
# 1. Sesión de GitHub con permiso para subir automatizaciones (scope "workflow")
# ---------------------------------------------------------------------------
gh auth status *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Inicia sesión en GitHub (se abrirá el navegador)..." -ForegroundColor Cyan
    gh auth login --hostname github.com --web --git-protocol https --scopes workflow
    if ($LASTEXITCODE -ne 0) { throw "No se pudo iniciar sesión en GitHub." }
}
else {
    $estado = gh auth status 2>&1 | Out-String
    if ($estado -notmatch "workflow") {
        Write-Host "GitHub te pedirá autorizar un permiso extra para subir automatizaciones (workflow)..." -ForegroundColor Cyan
        gh auth refresh --hostname github.com --scopes workflow
        if ($LASTEXITCODE -ne 0) { throw "No se pudo agregar el permiso 'workflow' a tu sesión." }
    }
}
gh auth setup-git *> $null
$usuario = gh api user --jq .login
if ($Repo -notmatch "/") { $Repo = "$usuario/$Repo" }
if (-not $Destino) { $Destino = Join-Path $HOME ($Repo.Split("/")[-1]) }
$origen = (Resolve-Path $PSScriptRoot).Path

gh repo view $Repo --json name *> $null
$existe = ($LASTEXITCODE -eq 0)
$dueno = $Repo.Split("/")[0]
if (-not $existe -and $dueno -ne $usuario) {
    throw ("No puedo ver el repositorio $Repo con la cuenta $usuario. Revisa que el nombre esté bien escrito " +
           "y que hayas aceptado la invitación de $dueno (https://github.com/$Repo/invitations o en tu correo).")
}
Write-Host "Cuenta: $usuario · Repositorio: $Repo ($(if ($existe) { 'ya existe' } else { 'nuevo' }))" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# 2. Carpeta local del repositorio
# ---------------------------------------------------------------------------
$rama = "main"
if ($existe) {
    $permiso = gh repo view $Repo --json viewerPermission --jq .viewerPermission
    if ($permiso -notin @("ADMIN", "MAINTAIN", "WRITE")) {
        throw "Tu cuenta tiene permiso '$permiso' en $Repo. Pide a su dueño que te dé permiso de escritura (Write) en Settings > Collaborators."
    }
    $ramaRemota = gh repo view $Repo --json defaultBranchRef --jq .defaultBranchRef.name
    if ($ramaRemota) { $rama = $ramaRemota }

    # Una carpeta de un intento anterior que no está conectada a este repositorio se aparta (no se borra)
    if (Test-Path (Join-Path $Destino ".git")) {
        $remoto = git -C $Destino remote get-url origin 2>$null
        if (-not $remoto -or $remoto -notmatch [regex]::Escape($Repo)) {
            $respaldo = "$Destino-anterior-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
            Rename-Item -Path $Destino -NewName (Split-Path $respaldo -Leaf)
            Write-Host "  $Destino no estaba conectada a $Repo; se movió a $respaldo" -ForegroundColor Yellow
        }
    }
    if (Test-Path (Join-Path $Destino ".git")) {
        Set-Location $Destino
        Write-Host "Usando la copia que ya tienes en $Destino"
    }
    elseif (Test-Path $Destino) {
        throw "$Destino ya existe y no es un repositorio. Bórrala o usa otro -Destino."
    }
    else {
        gh repo clone $Repo $Destino
        if ($LASTEXITCODE -ne 0) { throw "No se pudo clonar $Repo." }
        Set-Location $Destino
    }
    git rev-parse --verify HEAD *> $null
    if ($LASTEXITCODE -eq 0) {
        git checkout $rama --quiet
        git pull --quiet
    }
    else {
        git symbolic-ref HEAD "refs/heads/$rama"   # repositorio vacío
    }
}
else {
    if (Test-Path (Join-Path $Destino ".git")) { throw "$Destino ya es un repositorio git. Elige otro -Destino." }
    New-Item -ItemType Directory -Force -Path $Destino | Out-Null
    Set-Location $Destino
    git init -b main | Out-Null
}

# ---------------------------------------------------------------------------
# 3. Copiar los archivos del modelo sin pisar lo que ya tenga el repositorio
# ---------------------------------------------------------------------------
if ($origen -ne (Resolve-Path $Destino).Path) {
    foreach ($item in Get-ChildItem -Path $origen -Force) {
        if ($item.Name -eq ".git") { continue }
        $dest = Join-Path $Destino $item.Name
        if (-not $item.PSIsContainer -and (Test-Path $dest)) {
            if ($item.Name -eq "README.md") {
                if ((Get-FileHash $item.FullName).Hash -ne (Get-FileHash $dest).Hash) {
                    Copy-Item $item.FullName (Join-Path $Destino "README_modelo.md") -Force
                    Write-Host "  Ya había un README.md: el del modelo queda como README_modelo.md"
                }
                continue
            }
            if ($item.Name -in @(".gitignore", ".gitattributes")) {
                $actuales = @(Get-Content $dest)
                $nuevas = @(Get-Content $item.FullName | Where-Object { $_ -and ($actuales -notcontains $_) })
                if ($nuevas.Count -gt 0) { Add-Content -Path $dest -Value $nuevas }
                continue
            }
        }
        Copy-Item -Path $item.FullName -Destination $Destino -Recurse -Force
    }
}

# Los workflows vienen en flujos_github\ y van en .github\workflows\
$flujos = Join-Path $Destino "flujos_github"
$workflows = Join-Path $Destino ".github\workflows"
if (Test-Path $flujos) {
    New-Item -ItemType Directory -Force -Path $workflows | Out-Null
    Copy-Item -Path (Join-Path $flujos "*.yml") -Destination $workflows -Force
    Remove-Item -Path $flujos -Recurse -Force
}
if (-not (Test-Path (Join-Path $workflows "entrenar.yml"))) { throw "Faltan los workflows en $workflows." }

# Si la rama principal no se llama main (por ejemplo master), se ajustan los workflows
if ($rama -ne "main") {
    foreach ($f in @("entrenar.yml", "predecir.yml")) {
        $ruta = Join-Path $workflows $f
        $texto = [IO.File]::ReadAllText($ruta, $utf8) -replace "branches: \[main\]", "branches: [$rama]"
        [IO.File]::WriteAllText($ruta, $texto, $utf8)
    }
    Write-Host "  Workflows ajustados a la rama '$rama'"
}

# ---------------------------------------------------------------------------
# 4. Commit
# ---------------------------------------------------------------------------
if (-not (git config user.name)) {
    git config user.name $usuario
    $id = gh api user --jq .id
    git config user.email "$id+$usuario@users.noreply.github.com"
}
git add -A
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    git commit --quiet -m "Modelo de LaLiga: código, datos y automatizaciones"
}
else {
    Write-Host "No había archivos nuevos que agregar."
}

# ---------------------------------------------------------------------------
# 5. Subir
# ---------------------------------------------------------------------------
$pullRequest = $false
if (-not $existe) {
    $visibilidad = if ($Publico) { "--public" } else { "--private" }
    gh repo create $Repo $visibilidad --source . --remote origin --push --description "Modelo de marcadores y over/under de LaLiga"
    if ($LASTEXITCODE -ne 0) { throw "No se pudo crear el repositorio $Repo." }
}
else {
    git push --quiet origin "HEAD:refs/heads/$rama"
    if ($LASTEXITCODE -ne 0) {
        # rama protegida: se sube a otra rama y se abre un pull request
        $ramaPr = "modelo-laliga"
        Write-Host "No se pudo subir directo a '$rama' (probablemente está protegida). Abriendo un pull request..." -ForegroundColor Yellow
        git push --quiet --force origin "HEAD:refs/heads/$ramaPr"
        if ($LASTEXITCODE -ne 0) { throw "No se pudo subir a $Repo. Revisa tus permisos." }
        gh pr create --repo $Repo --base $rama --head $ramaPr `
            --title "Modelo de LaLiga y automatizaciones" `
            --body "Agrega el modelo de marcadores de LaLiga, los datos y los workflows de GitHub Actions (entrenamiento programado y predicciones). Al aprobar este pull request, las automatizaciones quedan activas."
        $pullRequest = $true
    }
    else {
        git branch --quiet --set-upstream-to "origin/$rama" 2>$null
    }
}

# ---------------------------------------------------------------------------
# 6. Primer entrenamiento
# ---------------------------------------------------------------------------
if (-not $pullRequest) {
    Write-Host "Esperando a que GitHub registre los workflows..." -ForegroundColor Cyan
    Start-Sleep -Seconds 20
    $corridas = gh run list --repo $Repo --workflow entrenar.yml --limit 1 --json databaseId --jq length 2>$null
    if (-not $corridas -or $corridas -eq "0") {
        for ($i = 1; $i -le 3; $i++) {
            gh workflow run entrenar.yml --repo $Repo --ref $rama 2>$null
            if ($LASTEXITCODE -eq 0) { break }
            Start-Sleep -Seconds 15
        }
    }
}

Write-Host ""
Write-Host "Listo." -ForegroundColor Green
Write-Host "  Repositorio:     https://github.com/$Repo"
Write-Host "  Carpeta local:   $Destino"
if ($pullRequest) {
    Write-Host "  Pull request:    https://github.com/$Repo/pulls"
    Write-Host "  Pide al dueño del repositorio que lo apruebe (Merge). Después, lanza el primer entrenamiento con:"
    Write-Host "    gh workflow run entrenar.yml --repo $Repo"
}
else {
    Write-Host "  Entrenamiento:   https://github.com/$Repo/actions  (tarda unos 5-10 minutos)"
    Write-Host "  Para seguirlo aquí:  gh run watch --repo $Repo"
}
Write-Host ""
Write-Host "Si en Actions no aparece ninguna ejecución, revisa que estén permitidas en"
Write-Host "  https://github.com/$Repo/settings/actions"
