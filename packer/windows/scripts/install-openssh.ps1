# OpenSSH Server with PowerShell as the shell: Ansible manages slice Windows hosts over SSH.
# Administrators use their own authorized_keys (the key cloudbase-init writes from the config drive)
# rather than the shared administrators_authorized_keys.
$ErrorActionPreference = "Stop"
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force | Out-Null
$config = "$env:ProgramData\ssh\sshd_config"
(Get-Content $config) |
  ForEach-Object { $_ -replace '^(Match Group administrators)', '#$1' -replace '^(\s*AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys)', '#$1' } |
  Set-Content $config
if (-not (Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -DisplayName "OpenSSH Server (sshd)" -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
}
# ICMP, for GraySpace's reachability checks
New-NetFirewallRule -Name "GraySpace-ICMPv4" -DisplayName "ICMPv4 echo" -Protocol ICMPv4 -IcmpType 8 -Direction Inbound -Action Allow -ErrorAction SilentlyContinue | Out-Null
Restart-Service sshd
