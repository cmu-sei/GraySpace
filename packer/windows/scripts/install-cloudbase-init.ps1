# cloudbase-init reads the config drive Proxmox attaches to clones (terraform/slice/ sets
# initialization.type = configdrive2): hostname, static addresses and resolver, and the
# Administrator's password and SSH key.
$ErrorActionPreference = "Stop"
$msi = "$env:TEMP\CloudbaseInitSetup.msi"
Invoke-WebRequest -Uri $env:CLOUDBASE_INIT_URL -OutFile $msi -UseBasicParsing
Start-Process msiexec.exe -ArgumentList "/i", $msi, "/qn", "/norestart", "RUN_SERVICE_AS_LOCAL_SYSTEM=1", "USERNAME=Administrator" -Wait

$dir = "$env:ProgramFiles\Cloudbase Solutions\Cloudbase-Init\conf"
$settings = @"
[DEFAULT]
username=Administrator
groups=Administrators
inject_user_password=true
first_logon_behaviour=no
config_drive_raw_hhd=true
config_drive_cdrom=true
config_drive_vfat=true
bsdtar_path=C:\Program Files\Cloudbase Solutions\Cloudbase-Init\bin\bsdtar.exe
mtools_path=C:\Program Files\Cloudbase Solutions\Cloudbase-Init\bin\
metadata_services=cloudbaseinit.metadata.services.configdrive.ConfigDriveService
plugins=cloudbaseinit.plugins.common.mtu.MTUPlugin,cloudbaseinit.plugins.common.sethostname.SetHostNamePlugin,cloudbaseinit.plugins.common.networkconfig.NetworkConfigPlugin,cloudbaseinit.plugins.windows.extendvolumes.ExtendVolumesPlugin,cloudbaseinit.plugins.common.setuserpassword.SetUserPasswordPlugin,cloudbaseinit.plugins.common.sshpublickeys.SetUserSSHPublicKeysPlugin,cloudbaseinit.plugins.common.userdata.UserDataPlugin
allow_reboot=true
stop_service_on_exit=false
logdir=C:\Program Files\Cloudbase Solutions\Cloudbase-Init\log\
logfile=cloudbase-init.log
"@
Set-Content -Path "$dir\cloudbase-init.conf" -Value $settings
Set-Content -Path "$dir\cloudbase-init-unattend.conf" -Value ($settings -replace 'plugins=.*', 'plugins=cloudbaseinit.plugins.common.mtu.MTUPlugin,cloudbaseinit.plugins.common.sethostname.SetHostNamePlugin')
