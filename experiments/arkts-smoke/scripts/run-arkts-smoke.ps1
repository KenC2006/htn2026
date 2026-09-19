param(
  [string] $DevEcoHome = 'C:\Program Files\Huawei\DevEco Studio',
  [string] $Target,
  [switch] $BuildOnly
)
$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $projectDir '.smoke-logs'
New-Item -ItemType Directory -Force $logDir | Out-Null
& (Join-Path $PSScriptRoot 'setup-arkts.ps1') -DevEcoHome $DevEcoHome
$hdc = Join-Path $DevEcoHome 'sdk\default\openharmony\toolchains\hdc.exe'
$node = Join-Path $DevEcoHome 'tools\node\node.exe'
$hvigor = Join-Path $DevEcoHome 'tools\hvigor\hvigor\bin\hvigor.js'
function Invoke-Hdc([string[]] $Arguments, [string] $LogName) {
  $oldPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = 'Continue'
    $response = & $hdc @Arguments 2>&1
    $code = $LASTEXITCODE
  } finally { $ErrorActionPreference = $oldPreference }
  $text = $response | Out-String
  if ($LogName) { Set-Content -LiteralPath (Join-Path $logDir $LogName) -Value $text }
  if ($code -ne 0 -or $text -match '\[Fail\]|\[Empty\]|error:|failed to execute') {
    throw "Official HDC command failed: $($Arguments -join ' ')`n$text`nLogs: $logDir"
  }
  return $text
}
if (-not $BuildOnly) {
  $null = Invoke-Hdc @('-v') 'hdc-version.log'
  $targets = @( ((Invoke-Hdc @('list','targets') 'targets.log') -split '\r?\n') | ForEach-Object { $_.Trim() } | Where-Object { $_ -and $_ -notmatch '^\[' } )
  if ($Target) {
    if ($targets -notcontains $Target) { throw "Target '$Target' is not connected. Official HDC targets: $($targets -join ', ')" }
  } elseif ($targets.Count -eq 1) { $Target = $targets[0] }
  else { throw 'Start the official DevEco emulator; if multiple devices are connected, pass -Target <hdc-target>.' }
  Write-Output "Official DevEco HDC target: $Target"
}
Push-Location $projectDir
try {
  & $node $hvigor --mode module -p product=default assembleHap --no-daemon
  if ($LASTEXITCODE -ne 0) { throw "Official DevEco build failed (exit $LASTEXITCODE)." }
} finally { Pop-Location }
$hap = Join-Path $projectDir 'entry\build\default\outputs\default\entry-default-signed.hap'
if (-not (Test-Path $hap)) { throw 'No signed HAP was produced. Open experiments/arkts-smoke in DevEco and configure signing under File > Project Structure > Signing Configs.' }
if ($BuildOnly) { Write-Output $hap; return }
$appConfig = Get-Content (Join-Path $projectDir 'AppScope\app.json5') -Raw | ConvertFrom-Json
$moduleConfig = Get-Content (Join-Path $projectDir 'entry\src\main\module.json5') -Raw | ConvertFrom-Json
$bundle = $appConfig.app.bundleName
$ability = $moduleConfig.module.mainElement

$null = Invoke-Hdc @('-t',$Target,'shell','hilog','-r') 'clear-hilog.log'
$hilogPath = Join-Path $logDir 'hilog.log'
$hilog = Start-Process -WindowStyle Hidden -FilePath $hdc -ArgumentList @('-t',$Target,'hilog') -RedirectStandardOutput $hilogPath -RedirectStandardError (Join-Path $logDir 'hilog.stderr.log') -PassThru
try {
  $install = Invoke-Hdc @('-t',$Target,'install','-r',$hap) 'install.log'
  Write-Output $install.Trim()
  if ($install -notmatch 'install.*success') { throw "HDC did not confirm installation. See $logDir\install.log" }
  $null = Invoke-Hdc @('-t',$Target,'shell','aa','force-stop',$bundle) 'stop.log'
  $null = Invoke-Hdc @('-t',$Target,'shell','power-shell','wakeup') 'wake.log'
  $launch = Invoke-Hdc @('-t',$Target,'shell','aa','start','-a',$ability,'-b',$bundle) 'launch.log'
  Write-Output $launch.Trim()
  if ($launch -notmatch 'start ability successfully') { throw "Ability launch was not confirmed. See $logDir\launch.log" }
  $end = (Get-Date).AddSeconds(90)
  do {
    $matches = @(Select-String -LiteralPath $hilogPath -Pattern 'ArkTSSmoke:.*ARKTS_SMOKE_PASS:10' -ErrorAction SilentlyContinue)
    if ($matches.Count) { $matches | ForEach-Object { Write-Output $_.Line }; Write-Output "Runtime log: $hilogPath"; return }
    if ($hilog.HasExited) { throw "HiLog capture exited. See $logDir\hilog.stderr.log" }
    Start-Sleep -Milliseconds 500
  } while ((Get-Date) -lt $end)
  throw "No ARKTS_SMOKE_PASS:10 was emitted by the app. See $hilogPath"
} finally {
  if (-not $hilog.HasExited) { Stop-Process -Id $hilog.Id -Force }
}
