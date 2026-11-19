# Language, Country, Locale
d-i debian-installer/language string en
d-i debian-installer/country string US
d-i debian-installer/locale string en_US.UTF-8
d-i keyboard-configuration/xkb-keymap select us

# Network 
d-i netcfg/enable boolean true
d-i netcfg/dhcp_timeout string 120
d-i netcfg/get_hostname string gray-appliance
d-i netcfg/get_domain string local.lab

# User Account 
d-i passwd/user-fullname string GrayAdmin User
d-i passwd/username string gray-admin
# Changed to cleartext password for simplicity
d-i passwd/user-password password grayspace
d-i passwd/user-password-again password grayspace
d-i passwd/user-default-groups string audio cdrom video sudo netdev plugdev
d-i user-setup/allow-password-weak boolean true

# Root Account
d-i passwd/root-login boolean true
d-i passwd/root-password password grayspace
d-i passwd/root-password-again password grayspace

# Partitioning (LVM)
d-i partman-auto/disk string /dev/sda
d-i partman-auto/method string lvm
d-i partman-auto/choose_recipe select atomic
d-i partman-auto-lvm/new_vg_name string pve
d-i partman-lvm/device_remove_lvm boolean true
d-i partman-md/device_remove_md boolean true
d-i partman-lvm/confirm boolean true
d-i partman-lvm/confirm_nooverwrite boolean true
d-i partman/confirm_write_new_label boolean true
d-i partman/choose_partition select finish
d-i partman/confirm boolean true
d-i partman/confirm_nooverwrite boolean true

# Package Selection
d-i mirror/protocol string http
d-i mirror/country string manual
d-i mirror/http/hostname string ftp.us.debian.org
d-i mirror/http/directory string /debian
d-i mirror/suite string trixie
tasksel tasksel/first multiselect standard, ssh-server
d-i pkgsel/include string sudo curl wget open-vm-tools

# Upgrade packages during install
d-i pkgsel/upgrade select full-upgrade

# Proxy Configuration
d-i mirror/http/proxy string ${http_proxy}
d-i mirror/https/proxy string ${http_proxy}

d-i grub-efi/force_removable boolean true
d-i grub-installer/only_debian boolean true
d-i grub-installer/bootdev string default

# Late Command
# Setup Sudo for packer user
# Persist Proxy settings from installer to final OS
d-i preseed/late_command string \
    echo "packer ALL=(ALL) NOPASSWD:ALL" > /target/etc/sudoers.d/packer; \
    chmod 0440 /target/etc/sudoers.d/packer; \
    if [ -n "${http_proxy}" ]; then \
        echo "Acquire::http::Proxy \"${http_proxy}\";" > /target/etc/apt/apt.conf.d/99proxy; \
        echo "Acquire::https::Proxy \"${http_proxy}\";" >> /target/etc/apt/apt.conf.d/99proxy; \
    fi; \
    sed -i 's/^#*PermitRootLogin.*/PermitRootLogin yes/' /target/etc/ssh/sshd_config

d-i finish-install/reboot_in_progress note