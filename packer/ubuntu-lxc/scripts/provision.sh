#!/bin/bash
# Provisions the GraySpace Ubuntu LXC template (ubuntu-lxc.pkr.hcl), inside a container made from
# Proxmox's Ubuntu template. No systemd runs here: units are enabled and disabled, not started.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

# Packages' maintainer scripts start nothing in the build
printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
chmod 0755 /usr/sbin/policy-rc.d

# Nothing upgrades or refreshes on its own: first boots would race Ansible for the apt lock, and
# enclave hosts reach the real internet only while a job runs
apt-get purge -y unattended-upgrades
systemctl disable apt-daily.timer apt-daily-upgrade.timer

# The roles' preseeds don't apply to packages already installed: neutral answers here, and the
# roles configure the rest (mail writes /etc/mailname)
debconf-set-selections <<'SEL'
postfix postfix/main_mailer_type select Internet Site
postfix postfix/mailname string localdomain
SEL

apt-get update
apt-get -y dist-upgrade
# Base: what Ansible and an operator use
apt-get install -y --no-install-recommends \
    python3 python3-apt ca-certificates curl wget \
    iproute2 iputils-ping traceroute mtr-tiny bind9-dnsutils tcpdump netcat-openbsd nmap whois \
    htop lsof psmisc less vim nano tmux zip unzip tar rsync jq tree file acl attr
# The host roles' packages that run in a container (ansible/roles/*/tasks): file, dns, mail, web,
# ghosts-client. ad-dc needs a VM (packer/ubuntu-vm)
apt-get install -y --no-install-recommends \
    samba \
    bind9 bind9-utils \
    postfix dovecot-imapd dovecot-pop3d \
    nginx

# Every role turns on its own service
systemctl disable smbd nmbd named nginx postfix dovecot

mkdir -p /etc/grayspace
printf 'name=grayspace-ubuntu-lxc\nversion=%s\n' "$IMAGE_VERSION" > /etc/grayspace/image

rm -f /usr/sbin/policy-rc.d
apt-get -y autoremove --purge
apt-get clean
rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* /root/.cache /root/.bash_history
find /var/log -type f -exec truncate -s 0 {} +
