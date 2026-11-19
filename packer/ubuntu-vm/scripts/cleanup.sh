#!/bin/sh
# Leaves the image as a new instance expects it (ubuntu-vm.pkr.hcl); the build user is removed at
# shutdown. cloud-init makes new SSH host keys, a machine ID and the instance's user on first boot.
set -eu

rm -f /etc/apt/apt.conf.d/90build-proxy
# The real resolvers once the stub is off
ln -sf /run/systemd/resolve/resolv.conf /etc/resolv.conf

apt-get -y autoremove --purge
apt-get clean
rm -rf /var/lib/apt/lists/*

cloud-init clean --logs --seed --machine-id
# ssh_pwauth wrote this for the build; instances log in with Terraform's key
rm -f /etc/ssh/sshd_config.d/50-cloud-init.conf /etc/ssh/ssh_host_*
rm -f /var/lib/dbus/machine-id
rm -rf /var/lib/grayspace /tmp/* /var/tmp/* /root/.cache /root/.bash_history
journalctl --rotate > /dev/null 2>&1 || true
journalctl --vacuum-time=1s > /dev/null 2>&1 || true
find /var/log -type f \( -name '*.gz' -o -name '*.[0-9]' \) -delete
find /var/log -type f -exec truncate -s 0 {} +

# Freed blocks reach the qcow2 (disk_discard), so the compressed image stays small
fstrim -av || true
sync
