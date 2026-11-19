#!/bin/bash
# Provisions the GraySpace desktop template (desktop-lxc.pkr.hcl), inside a container made from
# Proxmox's Ubuntu template. The file provisioner put the files to install in /tmp/files.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y --no-install-recommends curl ca-certificates

# Mozilla's repository, preferred over Ubuntu's firefox (a snap)
install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://packages.mozilla.org/apt/repo-signing-key.gpg -o /etc/apt/keyrings/packages.mozilla.org.asc
cp -a /tmp/files/etc/apt/. /etc/apt/

apt-get update
apt-get -y dist-upgrade
apt-get install -y --no-install-recommends \
    xvfb x11vnc websockify dbus-x11 \
    fluxbox tint2 pcmanfm lxterminal mousepad firefox \
    xdg-utils p11-kit-modules adwaita-icon-theme fonts-dejavu fonts-liberation \
    iputils-ping traceroute mtr-tiny bind9-dnsutils curl wget netcat-openbsd iproute2 net-tools \
    tcpdump nmap whois openssh-client \
    htop lsof psmisc procps less man-db sudo \
    vim nano tmux zip unzip rsync jq tree file git bash-completion openssl ca-certificates

# The desktop's settings go to the user's home from /etc/skel
cp -a /tmp/files/etc/systemd/system/. /etc/systemd/system/
cp -a /tmp/files/etc/firefox /etc/
cp -a /tmp/files/etc/skel/. /etc/skel/
install -m 0440 /tmp/files/etc/sudoers.d/user /etc/sudoers.d/user
# Fluxbox's default key bindings, and Ctrl+Alt+T for a terminal
install -d /etc/skel/.fluxbox
{ cat /etc/X11/fluxbox/keys; printf '\n# GraySpace\nControl Mod1 t :Exec lxterminal\n'; } > /etc/skel/.fluxbox/keys

# Desktop icons: the applications' own launchers under plain names (pcmanfm runs them without
# asking, libfm.conf quick_exec)
install -d /etc/skel/Desktop
while read -r app name; do
    sed -e '/^Name\[/d' -e "0,/^Name=.*/s//Name=${name}/" "/usr/share/applications/${app}.desktop" > "/etc/skel/Desktop/${app}.desktop"
    chmod 0755 "/etc/skel/Desktop/${app}.desktop"
done <<'EOF'
firefox Firefox
lxterminal Terminal
org.xfce.mousepad Text Editor
pcmanfm Files
EOF

# The desktop user; no password (sudo needs none, and SSH is root's, with Terraform's key)
useradd --create-home --shell /bin/bash --groups sudo user

systemctl enable xvfb.service fluxbox.service x11vnc.service websockify.service

apt-get clean
rm -rf /var/lib/apt/lists/* /tmp/files /tmp/* /root/.cache
