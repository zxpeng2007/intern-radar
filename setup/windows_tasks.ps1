<#
.SYNOPSIS
  Register (or remove) the two Windows scheduled tasks that keep Intern Radar running:
    "Intern Radar Refresh" - every 3 hours, runs autorefresh.py
    "Intern Radar Server"  - at your logon, runs serve.py in the background (no window)

.USAGE
  From the repo folder, in a normal (non-admin) PowerShell window:
    .\setup\windows_tasks.ps1            # install both tasks and start the server now
    .\setup\windows_tasks.ps1 -Remove    # delete both tasks
    .\setup\windows_tasks.ps1 -Hours 6   # refresh every 6 hours instead of 3

  If PowerShell refuses to run scripts:
    powershell -NoProfile -ExecutionPolicy Bypass -File .\setup\windows_tasks.ps1
#>
param(
    [switch] $Remove,
    [int] $Hours = 3
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$refreshTask = 'Intern Radar Refresh'
$serverTask  = 'Intern Radar Server'

if ($Remove) {
    foreach ($t in @($refreshTask, $serverTask)) {
        if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
            Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $t -Confirm:$false
            Write-Host "removed '$t'"
        }
    }
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -like "*$repo\serve.py*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    exit 0
}

# pythonw.exe runs Python with no console window, which is what a background task wants.
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { throw "python was not found on PATH. Install Python 3.9+ from python.org and tick 'Add to PATH'." }
$pyw = Join-Path (Split-Path $py) 'pythonw.exe'
if (-not (Test-Path $pyw)) { $pyw = $py }

$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

# Refresh every N hours, starting at the next whole hour.
$start = (Get-Date).AddHours(1).Date.AddHours((Get-Date).Hour + 1)
$refreshTrigger = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Hours $Hours)
$refreshAction  = New-ScheduledTaskAction -Execute $pyw -Argument "`"$repo\autorefresh.py`"" -WorkingDirectory $repo
Register-ScheduledTask -TaskName $refreshTask -Action $refreshAction -Trigger $refreshTrigger -Settings $settings -Force | Out-Null
Write-Host "registered '$refreshTask' (every $Hours h, first run $($start.ToString('HH:mm')))"

# Server at logon for the current user only (an any-user logon trigger would need admin).
$serverTrigger  = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$serverSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$serverAction   = New-ScheduledTaskAction -Execute $pyw -Argument "`"$repo\serve.py`"" -WorkingDirectory $repo
Register-ScheduledTask -TaskName $serverTask -Action $serverAction -Trigger $serverTrigger -Settings $serverSettings -Force | Out-Null
Write-Host "registered '$serverTask' (at logon)"

# Start the server now so you do not have to log out and in.
Start-ScheduledTask -TaskName $serverTask
Start-Sleep -Seconds 3
$port = 8765
$cfg = Join-Path $repo 'config.json'
if (Test-Path $cfg) { $c = Get-Content $cfg -Raw | ConvertFrom-Json; if ($c.port) { $port = $c.port } }
Write-Host ""
Write-Host "Server started. Open http://localhost:$port"
Write-Host "Logs: $repo\serve.log and $repo\refresh.log"
Write-Host ""
Write-Host "If the phone cannot connect, Windows Firewall is probably blocking Python on the"
Write-Host "Tailscale adapter. In an ADMIN PowerShell, allow only Tailscale addresses on this port:"
Write-Host "  New-NetFirewallRule -DisplayName 'Intern Radar (Tailscale only)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port -RemoteAddress 100.64.0.0/10 -Profile Private -Program `"$pyw`""
