<#
.SYNOPSIS
    Takes a fresh Windows 11 box to "ready to run docker compose up" for the
    Inventory Management System -- so this production host is reproducible
    by someone who has never touched it, not just documented in one
    person's head.

.DESCRIPTION
    Safe to re-run: every step checks whether it's already done before
    doing it, so running this against the CURRENT production Mini PC
    should be a no-op all the way through, not a reinstall.

    What this script CANNOT fully automate, and why:
      - Docker Desktop's first launch (EULA acceptance, WSL2 kernel
        component finish-up) is a GUI wizard with no supported silent
        flag for that part. You still have to open it once by hand after
        this script installs it.
      - The GitHub Actions runner registration token from
        Settings -> Actions -> Runners -> New self-hosted runner is
        short-lived and tied to your own GitHub login -- it can't be
        baked into a script. That step stays manual; see infra/README.md.
      - Real secrets (POSTGRES_PASSWORD, CLOUDFLARE_TUNNEL_TOKEN,
        SENTRY_DSN, EMAIL_*) have to come from you. This script generates
        a random DJANGO_SECRET_KEY (safe to automate, needs no human
        judgement) and then stops to let you fill in the rest of .env by
        hand before it will bring the stack up.

    No Windows Firewall rules are opened by this script, and none are
    needed: nginx binds only to 127.0.0.1:8000 (loopback, for your own
    local debugging) and cloudflared makes an outbound-only connection to
    Cloudflare. Nothing in this stack listens on a port the firewall
    would need to let traffic *into*.

.PARAMETER InstallDir
    Where to clone the repo. Defaults to C:\Inventory\InventoryManagement
    -- change this or pass -InstallDir to match wherever it actually
    lives on this machine.

.PARAMETER RepoUrl
    Defaults to the real GitHub repo. Override only if you're bootstrapping
    a fork.
#>

param(
    [string]$InstallDir = "C:\Inventory\InventoryManagement",
    [string]$RepoUrl = "https://github.com/Alexander-Chavez97/InventoryManagement.git"
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

# --- 0. Must be elevated -----------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    throw "Run this from an elevated (Administrator) PowerShell prompt."
}

# --- 1. WSL2 + Virtual Machine Platform (Docker Desktop's prerequisites) ---
Write-Step "Checking Windows features Docker Desktop needs"
$features = @("Microsoft-Windows-Subsystem-Linux", "VirtualMachinePlatform")
$rebootNeeded = $false
foreach ($f in $features) {
    $state = (Get-WindowsOptionalFeature -Online -FeatureName $f).State
    if ($state -ne "Enabled") {
        Write-Host "Enabling $f ..."
        Enable-WindowsOptionalFeature -Online -FeatureName $f -All -NoRestart | Out-Null
        $rebootNeeded = $true
    } else {
        Write-Host "$f already enabled."
    }
}
if ($rebootNeeded) {
    Write-Warning "Windows features were just enabled and need a reboot before Docker Desktop will work. Reboot this machine, then re-run this script -- it will pick up from here."
    exit 0
}

wsl --set-default-version 2 | Out-Null

# --- 2. Core tools via winget (idempotent: winget no-ops if present) ---
Write-Step "Installing Git, Python 3.12, and Docker Desktop"
$packages = @(
    @{ Id = "Git.Git";              Name = "Git" },
    @{ Id = "Python.Python.3.12";   Name = "Python 3.12" },
    @{ Id = "Docker.DockerDesktop"; Name = "Docker Desktop" }
)
foreach ($pkg in $packages) {
    Write-Host "Checking $($pkg.Name) ..."
    winget install --accept-package-agreements --accept-source-agreements -e --id $pkg.Id
}

Write-Warning "If Docker Desktop was just installed for the first time, open it from the Start menu now, accept the EULA, and let it finish its one-time WSL2 setup before continuing. Re-run this script afterward -- everything above is safe to skip on a second pass."

# --- 3. Clone the repo ---------------------------------------------------
Write-Step "Cloning the repo to $InstallDir"
if (Test-Path (Join-Path $InstallDir ".git")) {
    Write-Host "Already cloned, skipping."
} else {
    New-Item -ItemType Directory -Path (Split-Path $InstallDir -Parent) -Force | Out-Null
    git clone $RepoUrl $InstallDir
}

Set-Location $InstallDir
git checkout master

# --- 4. .env: generate what's safe to generate, stop for the rest ------
Write-Step "Setting up .env"
$envPath = Join-Path $InstallDir ".env"
if (-not (Test-Path $envPath)) {
    Copy-Item (Join-Path $InstallDir ".env.example") $envPath

    $secretKey = python -c "import secrets; print(secrets.token_urlsafe(50))"
    (Get-Content $envPath) -replace "DJANGO_SECRET_KEY=.*", "DJANGO_SECRET_KEY=$secretKey" | Set-Content $envPath

    Write-Warning @"
.env created at $envPath with a freshly generated DJANGO_SECRET_KEY.
Everything else in it is a placeholder -- open it now and fill in for real:
  - POSTGRES_PASSWORD (pick a strong one; this is a brand-new database, not
    one you need to match to anything)
  - DJANGO_ALLOWED_HOSTS / CSRF_TRUSTED_ORIGINS (inventory.novastartx.com)
  - CLOUDFLARE_TUNNEL_TOKEN (from this host's Cloudflare Tunnel)
  - SENTRY_DSN, EMAIL_* (optional, but needed for error tracking / the
    order-notice emails to actually work)
This script will not bring the stack up until you confirm that's done.
"@
    Read-Host "Press Enter once .env is filled in for real (or Ctrl+C to stop here and finish later)"
} else {
    Write-Host ".env already exists, leaving it alone."
}

# --- 5. backup/ directory + scheduled backup task ----------------------
Write-Step "Setting up the backup directory and its scheduled task"
New-Item -ItemType Directory -Path (Join-Path $InstallDir "backup") -Force | Out-Null

$taskName = "InventoryManagement-Backup"
if (-not (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)) {
    # Like the GitHub Actions runner, this must run inside the real desktop
    # session (not "whether user is logged on or not") -- backup.py shells
    # out to `docker compose exec`, which needs the same Docker Desktop
    # named pipe that's only visible from an interactive session.
    $action = New-ScheduledTaskAction -Execute "python" -Argument "backup.py" -WorkingDirectory $InstallDir
    $trigger = New-ScheduledTaskTrigger -Daily -At 2am
    $settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited
    Write-Host "Scheduled '$taskName' to run daily at 2am as the logged-in user."
} else {
    Write-Host "Scheduled task '$taskName' already exists, skipping."
}

# --- 6. Bring the stack up ----------------------------------------------
Write-Step "Starting the stack"
docker compose up -d --build
docker compose ps

Write-Host ""
Write-Host "Bootstrap steps done. Still manual, on purpose (see the .DESCRIPTION" -ForegroundColor Yellow
Write-Host "above and infra/README.md): the one-time Django migrate/createsuperuser," -ForegroundColor Yellow
Write-Host "the Cloudflare Tunnel route, and registering the GitHub Actions runner." -ForegroundColor Yellow
