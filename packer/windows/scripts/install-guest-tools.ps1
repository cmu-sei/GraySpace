# VirtIO drivers and the QEMU guest agent, which reports addresses to Proxmox (and so to Terraform).
$ErrorActionPreference = "Stop"
$virtio = Get-Volume | Where-Object { Test-Path ($_.DriveLetter + ":\virtio-win-guest-tools.exe") } | Select-Object -First 1
if (-not $virtio) { throw "VirtIO ISO not found" }
$drive = $virtio.DriveLetter + ":"
Start-Process -FilePath "$drive\virtio-win-guest-tools.exe" -ArgumentList "/install", "/quiet", "/norestart" -Wait
Get-Service QEMU-GA -ErrorAction SilentlyContinue | Set-Service -StartupType Automatic
