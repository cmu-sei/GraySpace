#!/bin/bash
set -e

# Force eth0 as the interface name
sed -i 's/GRUB_CMDLINE_LINUX_DEFAULT="/GRUB_CMDLINE_LINUX_DEFAULT="net.ifnames=0 biosdevname=0 /' /etc/default/grub
update-grub

# Install gpg
apt-get update
apt-get install -y gpg

# Add Terraform key and repo
wget -O- https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg > /dev/null

echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] \
https://apt.releases.hashicorp.com bookworm main" | \
tee /etc/apt/sources.list.d/hashicorp.list > /dev/null

# Add

apt-get update

# Install PVE
apt-get install -y proxmox-ve postfix open-iscsi chrony

# Remove PVE enterprise repo
rm -f /etc/apt/sources.list.d/pve-enterprise.sources

apt-get update

# Set content types on local
pvesh set /storage/local --content="images,rootdir,backup,vztmpl,iso,snippets,import"

# Install all other requirements
apt-get install -y xfce4 xfce4-terminal firefox-esr dbus-x11
apt-get install -y git curl vim frr-pythontools libpve-network-perl sshpass ansible terraform

apt-get install -y dnsmasq || true

# Install Claude Code
curl -fsSL https://claude.ai/install.sh | bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
export PATH="$HOME/.local/bin:$PATH"

# Write the interfaces file
cat <<EOF > /etc/network/interfaces.new
source /etc/network/interfaces.d/*

auto lo
iface lo inet loopback

auto eth0
iface eth0 inet manual

auto eth1
iface eth1 inet manual

# WAN Bridge (vmbr0)
auto vmbr0
iface vmbr0 inet dhcp
    bridge-ports eth0
    bridge-stp off
    bridge-fd 0
    bridge-vlan-aware yes

# Management Bridge (vmbr1)
auto vmbr1
iface vmbr1 inet static
    address 10.0.0.1/16
    bridge-ports none
    bridge-stp off
    bridge-fd 0
    post-up   echo 1 > /proc/sys/net/ipv4/ip_forward
    post-up   iptables -t nat -A POSTROUTING -s '10.0.0.0/16' -o vmbr0 -j MASQUERADE
    post-down iptables -t nat -D POSTROUTING -s '10.0.0.0/16' -o vmbr0 -j MASQUERADE

# VLAN trunk bridge
auto vmbr2
iface vmbr2 inet manual
    bridge-ports eth1
    bridge-stp off
    bridge-fd 0
EOF

# Configure DHCP for the management network
cat <<EOF > /etc/dnsmasq.d/mgmt-net.conf
interface=vmbr1
bind-interfaces
listen-address=10.0.0.1
dhcp-range=10.0.0.100,10.0.4.255,12h
dhcp-option=15,grayspace.local
EOF

# Enable dnsmasq for next boot
systemctl enable dnsmasq || true

# Add hosts file entries for local apps
echo "127.0.0.1 pve.grayspace.local" >> /etc/hosts
echo "127.0.0.1 grayspace.local" >> /etc/hosts

# Create desktop shortcuts
mkdir -p /etc/skel/Desktop

# Make shortcut available to any user created in the future

#Proxmox
cat <<EOF > /etc/skel/Desktop/pve-gui.desktop
[Desktop Entry]
Version=1.0
Type=Application
Name=Proxmox VE
Comment=Access the Proxmox Web Interface
Exec=firefox-esr https://pve.grayspace.local:8006
Icon=/usr/share/pixmaps/proxmox-logo.png
Terminal=false
StartupNotify=false
EOF

# GraySpace GUI
cat <<EOF > /etc/skel/Desktop/gray-gui.desktop
[Desktop Entry]
Version=1.0
Type=Application
Name=GraySpace Web Interface
Comment=Access the GraySpace Web Interface
Exec=firefox-esr http://localhost
Icon=/usr/share/pixmaps/grayspace.png
Terminal=false
StartupNotify=false
EOF

# Make shortcuts available to root user
mkdir -p /root/Desktop

cp /etc/skel/Desktop/pve-gui.desktop /root/Desktop/pve-gui.desktop
chmod +x /root/Desktop/pve-gui.desktop

cp /etc/skel/Desktop/gray-gui.desktop /root/Desktop/gray-gui.desktop
chmod +x /root/Desktop/gray-gui.desktop

# Ensure the shortcuts are trusted

PVEHASH=$(sha256sum /root/Desktop/pve-gui.desktop | awk '{print $1}')
dbus-launch gio set /root/Desktop/pve-gui.desktop metadata::trusted true
dbus-launch gio set /root/Desktop/pve-gui.desktop metadata::xfce-exe-checksum $PVEHASH

GRAYHASH=$(sha256sum /root/Desktop/gray-gui.desktop | awk '{print $1}')
dbus-launch gio set /root/Desktop/gray-gui.desktop metadata::trusted true
dbus-launch gio set /root/Desktop/gray-gui.desktop metadata::xfce-exe-checksum $GRAYHASH
