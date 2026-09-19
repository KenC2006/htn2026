# Join the badge hotspot and stream your screen to it FOREVER (full-res, sharp).
# Does NOT restore your Wi-Fi - you reconnect yourself when you're done.
#   Stop streaming:  Ctrl-C in this window
#   Get internet back afterwards:  netsh wlan connect name="HackTheNorth"
#                                  (or just pick HackTheNorth from the Wi-Fi menu)
#
# Run it:  powershell -ExecutionPolicy Bypass -File badge\tools\play.ps1
# Optional: add  --scale 2  for smoother-but-blockier, or  --quality 70  for sharper.

$Badge = "BadgeCraft"
$Tools = "C:\workspace\vscodeProjects\HTN\badge\tools"

Write-Host "Joining $Badge ..." -ForegroundColor Cyan
netsh wlan connect name=$Badge | Out-Null

# wait for the badge's DHCP address
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    if (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -like "192.168.4.*" }) { break }
}

# Full-quality defaults (fresh batteries): sharp full-res, uncapped fps, controls on.
# Override with your own --quality / --fps / --scale on the command line.
$defaults = @("--quality","60","--controls")
if ($args -match "--quality") { $defaults = $defaults | Where-Object { $_ -ne "--quality" -and $_ -ne "60" } }

Write-Host "Streaming (full-res, q60, controls ON)." -ForegroundColor Green
Write-Host "D-pad = move | HOME + D-pad = look | START = jump | A = attack/break | B = place | A+B = inventory." -ForegroundColor Green
Write-Host "Ctrl-C to stop." -ForegroundColor Green
python "$Tools\stream.py" @defaults @args
