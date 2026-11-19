#!/bin/sh
# Provisions the GraySpace Ubuntu VM image (ubuntu-vm.pkr.hcl), as root in the build VM.
set -eu
export DEBIAN_FRONTEND=noninteractive

# The builder's proxy, as the VM reaches it: QEMU's user network puts the build host at 10.0.2.2
for v in http_proxy https_proxy; do
    eval "p=\${$v:-}"
    if [ -n "$p" ]; then
        export "$v=$(echo "$p" | sed -e 's#//localhost\b#//10.0.2.2#' -e 's#//127\.0\.0\.1\b#//10.0.2.2#')"
    fi
done
if [ -n "${http_proxy:-}" ]; then
    printf 'Acquire::http::Proxy "%s";\nAcquire::https::Proxy "%s";\n' \
        "$http_proxy" "${https_proxy:-$http_proxy}" > /etc/apt/apt.conf.d/90build-proxy
fi

cloud-init status --wait > /dev/null || true

# Nothing upgrades or refreshes on its own: first boots would race Ansible for the apt lock, and
# enclave hosts reach the real internet only while a job runs
systemctl disable --now apt-daily.timer apt-daily-upgrade.timer
# lxd-installer would install LXD's snap on first use
apt-get purge -y unattended-upgrades snapd lxd-installer lxd-agent-loader
rm -rf /snap /var/snap /var/lib/snapd /root/snap

# The roles' preseeds don't apply to packages already installed: neutral answers here, and the
# roles configure the rest (mail writes /etc/mailname; ad-dc replaces krb5.conf with Samba's)
debconf-set-selections <<'SEL'
postfix postfix/main_mailer_type select Internet Site
postfix postfix/mailname string localdomain
krb5-config krb5-config/default_realm string
krb5-config krb5-config/add_servers_realm string
SEL

apt-get update
apt-get -y dist-upgrade
# Base: the guest agent and what Ansible and an operator use
apt-get install -y --no-install-recommends \
    qemu-guest-agent python3 python3-apt chrony ca-certificates curl wget \
    iproute2 iputils-ping traceroute mtr-tiny bind9-dnsutils tcpdump netcat-openbsd nmap whois \
    htop lsof psmisc less vim nano tmux zip unzip tar rsync jq tree file acl attr
# The host roles' packages (ansible/roles/*/tasks): ad-dc, file, dns, mail, web, ghosts-client.
# Noble splits the DC out of samba: samba-ad-dc (the directory modules) and samba-ad-provision
# (provision's templates), which the roles get as recommends
apt-get install -y --no-install-recommends \
    samba samba-ad-dc samba-ad-provision winbind krb5-user smbclient \
    bind9 bind9-utils \
    postfix dovecot-imapd dovecot-pop3d \
    nginx

# Every role turns on its own service. samba-ad-dc (which its package enables) is masked too, so it
# can't start without a provisioned domain; the ad-dc role unmasks it
systemctl disable --now smbd nmbd winbind named nginx postfix dovecot samba-ad-dc
systemctl mask samba-ad-dc

# Samba's internal DNS needs port 53: systemd-resolved without its stub listener, and resolv.conf
# naming the real resolvers (the grayspace-host role points it at the simulated resolver later)
mkdir -p /etc/systemd/resolved.conf.d
printf '# GraySpace: a DC'"'"'s DNS (Samba or BIND) needs port 53\n[Resolve]\nDNSStubListener=no\n' \
    > /etc/systemd/resolved.conf.d/10-grayspace-no-stub.conf

# Proxmox attaches a NoCloud drive (configdrive2 for Windows only); don't probe clouds on boot
cat > /etc/cloud/cloud.cfg.d/90-grayspace.cfg <<'CFG'
# GraySpace: instances get their seed from Proxmox's cloud-init drive
datasource_list: [NoCloud, ConfigDrive, None]
CFG

mkdir -p /etc/grayspace
printf 'name=grayspace-ubuntu-vm\nversion=%s\n' "$IMAGE_VERSION" > /etc/grayspace/image
