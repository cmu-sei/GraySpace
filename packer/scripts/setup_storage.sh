#!/bin/bash
set -e

# Identify the second data disk

DATA_DISK=""

for disk in $(lsblk -d -n -o NAME | grep -v "sr0" | grep -v "fd0"); do
    # Check for root filesystem and skip disk
    if lsblk "/dev/$disk" -n -o MOUNTPOINT | grep -q "^/$"; then
        echo "    Skipping /dev/$disk "
    else
        echo "    Found Candidate: /dev/$disk"
        DATA_DISK="$disk"
        break # Stop at the first valid disk found
    fi
done

if [ -z "$DATA_DISK" ]; then
  echo "ERROR: No data disk found! setup-storage failed."
  lsblk
  exit 1
fi

# Create LVM architecture
pvcreate "/dev/$DATA_DISK"

vgcreate local-lvm "/dev/$DATA_DISK"

lvcreate -l 99%FREE --thinpool local-lvm local-lvm

# Add storage to PVE
pvesm add lvmthin local-lvm --vgname local-lvm --thinpool local-lvm
