# First logon during the build: let Packer in over WinRM (HTTP, basic auth), for the build only.
# sysprep.ps1 turns WinRM off again before the template is made.
$ErrorActionPreference = "Stop"
Set-NetConnectionProfile -NetworkCategory Private -ErrorAction SilentlyContinue
winrm quickconfig -q
winrm set winrm/config/service '@{AllowUnencrypted="true"}'
winrm set winrm/config/service/auth '@{Basic="true"}'
netsh advfirewall firewall add rule name="WinRM 5985 (Packer)" dir=in action=allow protocol=TCP localport=5985
