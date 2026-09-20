param([string] $DevEcoHome = 'C:\Program Files\Huawei\DevEco Studio')
$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
$env:DEVECO_SDK_HOME = Join-Path $DevEcoHome 'sdk'
$env:JAVA_HOME = Join-Path $DevEcoHome 'jbr'
$env:NODE_HOME = Join-Path $DevEcoHome 'tools\node'
$env:PATH = "$env:NODE_HOME;$env:JAVA_HOME\bin;$env:PATH"
foreach ($relative in @('tools\node\node.exe', 'jbr\bin\java.exe', 'tools\hvigor\hvigor\bin\hvigor.js', 'sdk\default\openharmony\toolchains\hdc.exe')) {
  if (-not (Test-Path (Join-Path $DevEcoHome $relative))) { throw "Official DevEco component missing: $DevEcoHome\$relative" }
}
# Avoid inherited settings from the retired standalone SDK.
Remove-Item Env:OHOS_BASE_SDK_HOME,Env:OHOS_SDK_HOME -ErrorAction SilentlyContinue
Set-Content -LiteralPath (Join-Path $projectDir 'local.properties') -Value ('sdk.dir=' + $env:DEVECO_SDK_HOME.Replace('\', '/'))
Write-Output "Official DevEco SDK: $env:DEVECO_SDK_HOME"
# Resolve build imports directly to DevEco's bundled modules, without npm.
$moduleDir = Join-Path $projectDir 'node_modules\@ohos'
New-Item -ItemType Directory -Force -Path $moduleDir | Out-Null
foreach ($name in @('hvigor','hvigor-ohos-plugin')) {
  $link = Join-Path $moduleDir $name
  $official = Join-Path $DevEcoHome "tools\hvigor\$name"
  if (-not (Test-Path $link)) { New-Item -ItemType Junction -Path $link -Target $official | Out-Null }
  elseif ((Get-Item $link).Target -notcontains $official) { throw "Build module $link is not linked to official DevEco tools. Move the old node_modules directory aside first." }
}
$env:NODE_PATH = Join-Path $projectDir 'node_modules'
