#!/bin/bash
set -e

# Setup Hostname & Hosts file
hostnamectl set-hostname gray-appliance
IP=$(hostname -I | awk '{print $1}')
sed -i "/gray-appliance/d" /etc/hosts
echo "$IP gray-appliance" >> /etc/hosts

# Add Proxmox Repo
echo "deb [arch=amd64] http://download.proxmox.com/debian/pve trixie pve-no-subscription" > /etc/apt/sources.list.d/pve-install-repo.list
wget https://enterprise.proxmox.com/debian/proxmox-release-trixie.gpg -O /etc/apt/trusted.gpg.d/proxmox-release-trixie.gpg

# Install Kernel
apt-get update
apt-get install -y proxmox-default-kernel

# Reboot to activate kernel
/sbin/reboot