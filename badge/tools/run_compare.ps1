# Join the badge once, stream at scale=1 then scale=2 back to back, capture the
# badge's fps log for each, then restore Wi-Fi. One connectivity gap total.
$HomeSsid = "HackTheNorth"
$Badge    = "BadgeCraft"
$Tools    = "C:\workspace\vscodeProjects\HTN\badge\tools"
$Scratch  = "C:\Users\kench\AppData\Local\Temp\claude\C--workspace-vscodeProjects-HTN\53c0df9d-4293-4522-9e11-08719b599f0c\scratchpad"
$BadgeLog = "$Scratch\compare_badge_log.txt"
Remove-Item $BadgeLog -ErrorAction SilentlyContinue

# Capture the badge serial log across the whole comparison (~30s).
$serial = Start-Process python -ArgumentList "$Tools\serial_log.py","COM5","32",$BadgeLog -PassThru -WindowStyle Hidden

netsh wlan connect name=$Badge | Out-Null
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    if (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -like "192.168.4.*" }) { break }
}

try {
    python "$Tools\stream.py" --seconds 11 --scale 1 --quality 50
    Start-Sleep -Seconds 1
    python "$Tools\stream.py" --seconds 11 --scale 2 --quality 50
} finally {
    netsh wlan connect name=$HomeSsid | Out-Null
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-Connection -ComputerName 1.1.1.1 -Count 1 -Quiet) { break }
    }
    Wait-Process -Id $serial.Id -Timeout 10 -ErrorAction SilentlyContinue
}
