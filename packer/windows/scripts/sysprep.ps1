# Close the build's WinRM door, then generalize with cloudbase-init's unattend file and shut down.
$ErrorActionPreference = "Stop"
netsh advfirewall firewall delete rule name="WinRM 5985 (Packer)"
winrm set winrm/config/service/auth '@{Basic="false"}'
winrm set winrm/config/service '@{AllowUnencrypted="false"}'
Set-Location "$env:ProgramFiles\Cloudbase Solutions\Cloudbase-Init\conf"
& "$env:SystemRoot\System32\Sysprep\Sysprep.exe" /generalize /oobe /mode:vm /quiet /shutdown "/unattend:Unattend.xml"
