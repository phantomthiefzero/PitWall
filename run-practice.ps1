<#
  Phase-1 capture for a session you are actually driving.

      .\run-practice.ps1                        wait for the car, record 15 min
      .\run-practice.ps1 -Minutes 20 -Label quali
      .\run-practice.ps1 -SkipPreflight         start recording immediately

  Waits until you are in the car (see scripts/preflight.py), records every raw
  variable, then runs analyze and tries to build the track map. Recording a
  replay or a spectator slot produces no player-car data, so the preflight
  refuses to start until IsOnTrack is true and fuel is non-zero.

  Do one of these per session type the README lists: practice, qualifying, a
  race with a pit stop, a rain session, a replay, a spectator join. Then fill in
  the Observed column of docs/VARIABLES_TO_VERIFY.md.
#>
param(
  [double]$Minutes = 15,
  [double]$Hz = 10,
  [string]$Label = 'practice',
  [double]$WaitSeconds = 600,
  [switch]$SkipPreflight,
  [switch]$AllowReplay
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw "venv missing. See README setup." }

New-Item -ItemType Directory -Force (Join-Path $root 'recordings') | Out-Null

if (-not $SkipPreflight) {
  Write-Host "Waiting for you to be in the car (up to $WaitSeconds s). Ctrl-C to abort." -ForegroundColor Cyan
  $pfArgs = @((Join-Path $root 'scripts\preflight.py'), '--timeout', $WaitSeconds)
  if ($AllowReplay) { $pfArgs += '--allow-replay' }
  & $py @pfArgs
  switch ($LASTEXITCODE) {
    0 { }
    2 { throw "iRacing is not running, or you are not in a session yet." }
    default { throw "Preflight timed out. Use -SkipPreflight to record anyway, or -AllowReplay for a replay." }
  }
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$rec = Join-Path $root "recordings\$Label-$stamp.jsonl.gz"
$md  = Join-Path $root "docs\VARIABLES_$Label-$stamp.md"

Write-Host ""
Write-Host "Recording $Minutes min at $Hz Hz -> $rec" -ForegroundColor Green
Write-Host "Drive normally. Include at least one clean lap and one pit stop if you can." -ForegroundColor Green
Write-Host ""
& $py -m pitwall record --source live --out $rec --hz $Hz --minutes $Minutes
if ($LASTEXITCODE -ne 0) { throw "record failed ($LASTEXITCODE)" }

Write-Host ""
Write-Host "Analyzing..." -ForegroundColor Cyan
& $py -m pitwall analyze $rec --md $md
if ($LASTEXITCODE -ne 0) { throw "analyze failed ($LASTEXITCODE)" }

Write-Host ""
Write-Host "Building track map..." -ForegroundColor Cyan
& $py -m pitwall map $rec
if ($LASTEXITCODE -ne 0) {
  Write-Warning "Track map could not be built - it needs one clean lap with no pit visit. The recording is still fine."
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "  recording : $rec"
Write-Host "  report    : $md"
Write-Host "  maps      : $env:USERPROFILE\.pitwall\maps"
Write-Host ""
Write-Host "Next: open the report and copy its labels into docs\VARIABLES_TO_VERIFY.md."
