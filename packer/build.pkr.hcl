packer {
  required_plugins {
    vsphere = {
      version = ">= 2.0.0"
      source  = "github.com/hashicorp/vsphere"
    }
  }
}

# Infrastructure Variables
variable "vcenter_server"      { type = string }
variable "vcenter_user"    { type = string }
variable "vcenter_pass"    { type = string }
variable "vcenter_datacenter"  { type = string }
variable "vcenter_cluster"     { type = string }
variable "vcenter_datastore"   { type = string }
variable "vcenter_network"     { type = string }
# Optional
variable "http_proxy"          { type = string }

source "vsphere-iso" "gray_appliance" {
  # Connection
  vcenter_server       = var.vcenter_server
  username             = var.vcenter_user
  password             = var.vcenter_pass
  insecure_connection  = true

  # Location
  datacenter           = var.vcenter_datacenter
  cluster              = var.vcenter_cluster
  datastore            = var.vcenter_datastore
  folder               = "Packer-Builds"  
  vm_name              = "gray-pve-appliance"

  # Hardware
  CPUs                 = 16
  RAM                  = 32768
  video_ram            = 16384
  NestedHV             = true
  guest_os_type        = "debian10_64Guest"
  firmware             = "efi"


  # Disk Controllers
  disk_controller_type = ["pvscsi", "pvscsi"]

  # OS Disk
  storage {
    disk_size             = 102400
    disk_thin_provisioned = true
    disk_controller_index = 0
  }

  # Disk for LXCs and VMs
  storage {
    disk_size             = 1024000
    disk_thin_provisioned = false
    disk_controller_index = 1

  }

  # Networking
  network_adapters {
    network      = var.vcenter_network
    network_card = "vmxnet3"
  }

  # ISO
  iso_url        = "https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/debian-13.3.0-amd64-netinst.iso"
  iso_checksum   = "sha256:c9f09d24b7e834e6834f2ffa565b33d6f1f540d04bd25c79ad9953bc79a8ac02"

  cd_content = {
    "preseed.cfg" = templatefile("${path.root}/http/preseed.pkrtpl.hcl", {
      http_proxy = var.http_proxy
    })
  }
  cd_label = "cidata"

boot_command = [
  "<wait3s>",
  "c",  
  "<wait>",
  "linux /install.amd/vmlinuz auto=true priority=critical hostname=gray-appliance domain=local.lab file=/media/preseed.cfg preseed/early_command=\"mount -t iso9660 /dev/sr1 /media\"",  "<enter>",
  "<wait>",
  "initrd /install.amd/initrd.gz",
  "<enter>",
  "<wait>",
  "boot",
  "<enter>"
]

  ssh_username = "root"
  ssh_password = "grayspace"
  ssh_timeout  = "20m"
}

build {
  sources = ["source.vsphere-iso.gray_appliance"]

  # Upload Scripts
  provisioner "file" {
    source      = "scripts/"
    destination = "/root/"
  }

  provisioner "file" {
    source      = "assets/proxmox-logo.png"
    destination = "/usr/share/pixmaps/proxmox-logo.png"
  }

    provisioner "file" {
    source      = "assets/grayspace.png"
    destination = "/usr/share/pixmaps/grayspace.png"
  }

  # Install Kernel
  provisioner "shell" {
    inline            = ["chmod +x /root/install_pve_kernel.sh", "/root/install_pve_kernel.sh"]
    environment_vars = [
      "DEBIAN_FRONTEND=noninteractive",
      "http_proxy=${var.http_proxy}",    
      "https_proxy=${var.http_proxy}", 
      "no_proxy=localhost,127.0.0.1"    
    ]
    expect_disconnect = true
  }

  # Wait for System to come back
  provisioner "shell" {
    inline       = ["echo 'System rebooted. Installing PVE Software...'"]
    pause_before = "30s"
  }

  # Install PVE, Xfce, and other requirements
  provisioner "shell" {
    inline           = ["chmod +x /root/setup_appliance.sh", "/root/setup_appliance.sh"]
  environment_vars = [
      "DEBIAN_FRONTEND=noninteractive",
      "http_proxy=${var.http_proxy}",    
      "https_proxy=${var.http_proxy}", 
      "no_proxy=localhost,127.0.0.1"    
    ]
  }

  # Set up block storage with the second disk
  provisioner "shell" {
    inline           = ["chmod +x /root/setup_storage.sh", "/root/setup_storage.sh"]
  }

  # Configure XFCE to not display removeable volumes on the desktop
  # Prevents all LXC disks from showing up as icons on the desktop
  provisioner "shell" {
  inline = [
    "mkdir -p /root/.config/xfce4/xfconf/xfce-perchannel-xml/",
    "mkdir -p /etc/skel/.config/xfce4/xfconf/xfce-perchannel-xml/",

    "cat <<EOF > /tmp/xfce4-desktop.xml",
    "<?xml version='1.0' encoding='UTF-8'?>",
    "<channel name='xfce4-desktop' version='1.0'>",
    "  <property name='desktop-icons' type='empty'>",
    "    <property name='file-icons' type='empty'>",
    "      <property name='show-removable' type='bool' value='false'/>",
    "      <property name='show-filesystem' type='bool' value='false'/>",
    "    </property>",
    "  </property>",
    "</channel>",
    "EOF",

    "cp /tmp/xfce4-desktop.xml /root/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-desktop.xml",
    "cp /tmp/xfce4-desktop.xml /etc/skel/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-desktop.xml",
    
    "rm /tmp/xfce4-desktop.xml"
  ]
}

  # Export the ovf, patch the network names, and package as ova
  post-processor "shell-local" {
    environment_vars = [
      "GOVC_URL=${var.vcenter_server}",
      "GOVC_USERNAME=${var.vcenter_user}",
      "GOVC_PASSWORD=${var.vcenter_pass}",
      "GOVC_INSECURE=true",
      "GOVC_DATACENTER=${var.vcenter_datacenter}",
      "VC_NETWORK=${var.vcenter_network}"
    ]
    
    inline = [
      "rm -rf output_ovf",
      "mkdir -p output_ovf",

      # Add the Second NIC
      "govc vm.network.add -vm 'gray-pve-appliance' -net \"$VC_NETWORK\" -net.adapter vmxnet3",

      # Export the VM to OVF
      "govc export.ovf -vm 'gray-pve-appliance' output_ovf",

      # Rename networks to be descriptive
      "NET_ID=$(grep -oE 'ovf:name=\"[^\"]+\"' output_ovf/gray-pve-appliance/*.ovf | head -1 | cut -d'\"' -f2)",

      "sed -i \"s|<Network ovf:name=\\\"$NET_ID\\\">|<Network ovf:name=\\\"Management Interface\\\"></Network><Network ovf:name=\\\"GraySpace Ingress (optional)\\\">|g\" output_ovf/gray-pve-appliance/*.ovf",

      "sed -i \"0,/$NET_ID/s//$NET_ID-TEMP-MARKER/\" output_ovf/gray-pve-appliance/*.ovf",
      "sed -i \"s/$NET_ID-TEMP-MARKER/Management Interface/\" output_ovf/gray-pve-appliance/*.ovf",

      "sed -i \"s/$NET_ID/GraySpace Ingress (optional)/g\" output_ovf/gray-pve-appliance/*.ovf",

      # Package OVA
      "cd output_ovf/gray-pve-appliance && tar -cf ../gray-pve-appliance.ova *",
    ]
  }
}