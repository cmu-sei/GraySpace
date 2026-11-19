#!/bin/sh
# Provisions the GraySpace Alpine template (alpine-lxc.pkr.hcl), inside a container made from
# Proxmox's Alpine template. Runs under BusyBox sh: bash is one of the packages it installs.
set -eu

# What the hook script, the router, DNS and CA plays (ansible/main.yml) and the slice edge roles
# (grayspace-host, edge-nat, edge-bgp) install on Alpine
apk add --no-cache \
    openssh python3 \
    frr iproute2 tar dnsmasq \
    bind bind-tools \
    openssl py3-pip py3-flask bash curl \
    iptables ca-certificates

# The bind package made an rndc key; each DNS server makes its own (the DNS play), not one shared by
# every guest made from the image
rm -f /etc/bind/rndc.key

# Root logs in with the key Terraform gives the container; the hook script did this on every start
sed -i 's/^#\?PermitRootLogin .*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
rc-update add sshd default

# The exporters, as the router play installs them; the play starts them
cd /tmp
curl -fsSLO "https://github.com/tynany/frr_exporter/releases/download/v${FRR_EXPORTER_VERSION}/frr_exporter-${FRR_EXPORTER_VERSION}.linux-amd64.tar.gz"
curl -fsSLO "https://github.com/prometheus/node_exporter/releases/download/v${NODE_EXPORTER_VERSION}/node_exporter-${NODE_EXPORTER_VERSION}.linux-amd64.tar.gz"
for archive in frr_exporter-*.tar.gz node_exporter-*.tar.gz; do
    tar -xzf "$archive" -C /usr/local/bin --strip-components=1
done
# The archives' LICENSE and NOTICE files land next to the binaries
rm -f /usr/local/bin/LICENSE /usr/local/bin/NOTICE
install -m 0755 /tmp/frr_exporter.service /etc/init.d/frr_exporter
install -m 0755 /tmp/node_exporter.service /etc/init.d/node_exporter

rm -rf /tmp/* /var/cache/apk/*
