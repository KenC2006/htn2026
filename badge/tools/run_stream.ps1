# One-shot: join the badge hotspot, stream the screen for a fixed window,
# then ALWAYS restore your Wi-Fi. Safe to run detached: it does not depend on
# the network staying up mid-run.
# Usage:  powershell -ExecutionPolicy Bypass -File run_stream.ps1 [seconds]

param([int]$Seconds = 20)

$HomeSsid = "HackTheNorth"   # network to return to
$Badge    = "BadgeCraft"
$Scratch  = "C:\Users\kench\AppData\Local\Temp\claude\C--workspace-vscodeProjects-HTN\53c0df9d-4293-4522-9e11-08719b599f0c\scratchpad"
$LogFile  = "$Scratch\stream_result.txt"
$BadgeLog = "$Scratch\badge_log.txt"
Remove-Item $LogFile, $BadgeLog -ErrorAction SilentlyContinue

# Start capturing the badge's USB serial timing log (COM5, independent of Wi-Fi).
$serial = Start-Process python `
    -ArgumentList "C:\workspace\vscodeProjects\HTN\badge\tools\serial_log.py","COM5",($Seconds + 6),$BadgeLog `
    -PassThru -WindowStyle Hidden

Write-Host "Joining $Badge ..."
netsh wlan connect name=$Badge | Out-Null

# wait for the badge's DHCP address (192.168.4.x)
$ok = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    if (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -like "192.168.4.*" }) { $ok = $true; break }
}

try {
    if (-not $ok) {
        "FAILED: never got a 192.168.4.x address - badge hotspot not reachable" | Out-File $LogFile
    } else {
        python "C:\workspace\vscodeProjects\HTN\badge\tools\stream.py" --seconds $Seconds --log $LogFile
    }
} finally {
    netsh wlan connect name=$HomeSsid | Out-Null
    # give the home network a moment to re-associate before the session resumes
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-Connection -ComputerName 1.1.1.1 -Count 1 -Quiet) { break }
    }
}
