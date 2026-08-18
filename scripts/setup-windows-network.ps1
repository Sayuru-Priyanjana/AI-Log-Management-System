<#
.SYNOPSIS
    Wires the LogIntel testbed VM to OpenSearch running inside WSL2.

.DESCRIPTION
    The VM cannot address WSL2 directly: WSL2 sits behind a NAT on the Windows
    host and its IP changes on every reboot. Windows, however, can always reach
    WSL2 services on 127.0.0.1 (WSL2 localhost forwarding), and the VM can always
    reach the Windows host at 192.168.56.1 (the VirtualBox host-only gateway,
    a fixed address).

    So we bridge the two with a portproxy:

        VM ──▶ 192.168.56.1:9200 ──▶ 127.0.0.1:9200 ──▶ WSL2 OpenSearch

    Must be run as Administrator. Re-running is safe and idempotent.

.PARAMETER Remove
    Tear down the portproxy and firewall rules instead of creating them.
#>
[CmdletBinding()]
param(
    [int]    $OpenSearchPort = 9200,
    [string] $HostOnlyAddress = "192.168.56.1",
    [switch] $Remove
)

$ErrorActionPreference = "Stop"
$FirewallRule = "LogIntel OpenSearch (host-only)"

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "This script must be run from an elevated PowerShell (Run as Administrator)."
    }
}

Assert-Admin

if ($Remove) {
    Write-Host "Removing LogIntel network configuration..." -ForegroundColor Cyan
    netsh interface portproxy delete v4tov4 `
        listenaddress=$HostOnlyAddress listenport=$OpenSearchPort 2>$null | Out-Null
    try { Remove-NetFirewallRule -DisplayName $FirewallRule -ErrorAction Stop }
    catch { Write-Host "  (no firewall rule to remove)" }
    Write-Host "Done." -ForegroundColor Green
    return
}

# --- 1. Verify the host-only adapter exists -------------------------------
$adapter = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
           Where-Object { $_.IPAddress -eq $HostOnlyAddress }
if (-not $adapter) {
    throw @"
No interface holds $HostOnlyAddress.

VirtualBox creates it with the first host-only network. Either run 'vagrant up'
once (it will be created automatically), or add it under
VirtualBox > Tools > Network > Host-only Networks.
"@
}
Write-Host "[ok] host-only adapter present: $HostOnlyAddress ($($adapter.InterfaceAlias))" -ForegroundColor Green

# --- 2. Verify OpenSearch is reachable from Windows -----------------------
# If this fails, OpenSearch is bound to loopback inside WSL only. It must listen
# on 0.0.0.0 (network.host: 0.0.0.0) for WSL2 localhost forwarding to pick it up.
try {
    $null = Invoke-WebRequest -Uri "http://127.0.0.1:$OpenSearchPort" -TimeoutSec 5 -UseBasicParsing
    Write-Host "[ok] OpenSearch answering on 127.0.0.1:$OpenSearchPort" -ForegroundColor Green
}
catch {
    Write-Warning @"
OpenSearch did not answer on 127.0.0.1:$OpenSearchPort.

The portproxy will still be created, but nothing will flow until OpenSearch is
running in WSL and bound to 0.0.0.0. Check inside WSL with:
    ss -ltn | grep $OpenSearchPort
"@
}

# --- 3. Portproxy --------------------------------------------------------
netsh interface portproxy delete v4tov4 `
    listenaddress=$HostOnlyAddress listenport=$OpenSearchPort 2>$null | Out-Null
netsh interface portproxy add v4tov4 `
    listenaddress=$HostOnlyAddress listenport=$OpenSearchPort `
    connectaddress=127.0.0.1 connectport=$OpenSearchPort | Out-Null
Write-Host "[ok] portproxy ${HostOnlyAddress}:${OpenSearchPort} -> 127.0.0.1:${OpenSearchPort}" -ForegroundColor Green

# --- 4. Firewall ---------------------------------------------------------
# Scoped to the host-only subnet so this does not expose OpenSearch to the LAN.
try { Remove-NetFirewallRule -DisplayName $FirewallRule -ErrorAction SilentlyContinue } catch {}
New-NetFirewallRule -DisplayName $FirewallRule `
    -Direction Inbound -Action Allow -Protocol TCP `
    -LocalPort $OpenSearchPort -LocalAddress $HostOnlyAddress `
    -RemoteAddress 192.168.56.0/24 | Out-Null
Write-Host "[ok] firewall rule '$FirewallRule' (192.168.56.0/24 only)" -ForegroundColor Green

Write-Host ""
Write-Host "Current portproxy table:" -ForegroundColor Cyan
netsh interface portproxy show v4tov4

Write-Host ""
Write-Host "Next: cd ..\testbed ; vagrant up" -ForegroundColor Cyan
