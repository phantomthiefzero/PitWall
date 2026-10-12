<#
  Runs the entire Pitwall stack on this one PC, in three windows.

      .\run-local.ps1                 # fake 24-car field, no iRacing needed
      .\run-local.ps1 -Source live    # real telemetry; iRacing must be in a session

  Tokens are throwaway local values. Nothing listens outside this machine
  except the relay, which binds 127.0.0.1 here (the README's 0.0.0.0 default
  is for a real VPS).
#>
param(
  [ValidateSet('fake','live')] [string]$Source = 'fake',
  [int]$RelayPort  = 8765,
  [int]$ClientPort = 8080,
  [switch]$AllowCommands
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$py   = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw "venv missing. Run:  py -3 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt pytest" }

$B = 'localbridge'
$V = 'localview'

foreach ($port in @($RelayPort, $ClientPort)) {
  $busy = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  if ($busy) { throw "Port $port is already in use (PID $($busy[0].OwningProcess)). Pass -RelayPort/-ClientPort to change." }
}

if ($Source -eq 'live') {
  if (-not (Get-Process -Name iRacingSim64DX11, iRacingSim64DX12, iRacingUI -ErrorAction SilentlyContinue)) {
    Write-Warning "iRacing does not appear to be running. The bridge will wait and connect once you are in a session."
  }
}

function Start-Pane([string]$title, [string]$cmd) {
  Start-Process powershell -ArgumentList @(
    '-NoExit','-Command', "`$Host.UI.RawUI.WindowTitle='$title'; Set-Location '$root'; $cmd"
  )
}

Start-Pane 'pitwall relay'  "& '$py' relay\server.py --host 127.0.0.1 --port $RelayPort --bridge-token $B --view-token $V"
Start-Sleep -Milliseconds 1200

$streamArgs = "stream --source $Source --relay ws://127.0.0.1:$RelayPort --token $B"
if ($AllowCommands) { $streamArgs += ' --allow-commands' }
Start-Pane 'pitwall bridge' "& '$py' -m pitwall $streamArgs"

Start-Pane 'pitwall client' "& '$py' -m http.server $ClientPort --bind 127.0.0.1 --directory '$root\client'"
Start-Sleep -Milliseconds 1200

$url = "http://127.0.0.1:$ClientPort/index.html?relay=ws://127.0.0.1:$RelayPort&token=$V"
Write-Host ""
Write-Host "  relay   ws://127.0.0.1:$RelayPort   (health: http://127.0.0.1:$RelayPort/health)"
Write-Host "  bridge  --source $Source"
Write-Host "  client  $url"
Write-Host ""
Write-Host "  Close the three windows to stop." 
Start-Process $url
