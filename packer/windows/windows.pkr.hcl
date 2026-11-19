# A Windows Server template for slice hosts.
#
# Built on the Proxmox node the slices run on. The template has the VirtIO drivers and guest agent,
# OpenSSH Server with key authentication (Ansible connects with ansible_shell_type=powershell),
# and cloudbase-init reading the config drive that terraform/slice/ gives Windows VMs: hostname,
# static addresses, resolver, and the Administrator's SSH key and password.
#
#   cd packer/windows
#   cp windows.auto.pkrvars.hcl.example windows.auto.pkrvars.hcl   # then edit
#   packer init . && packer build .
#
# Then register it as a local image (see README.md).

packer {
  required_plugins {
    proxmox = {
      version = ">= 1.2.2"
      source  = "github.com/hashicorp/proxmox"
    }
  }
}

variable "proxmox_url" {
  type        = string
  description = "e.g. https://pve.example.com:8006/api2/json"
}
variable "proxmox_username" {
  type        = string
  description = "API token ID (user@realm!token) or user@realm"
}
variable "proxmox_token" {
  type      = string
  sensitive = true
}
variable "proxmox_node" {
  type = string
}
variable "insecure_skip_tls_verify" {
  type    = bool
  default = true
}
variable "vm_id" {
  type        = number
  default     = 9000
  description = "The template's VM ID; register the image as template:<vm_id>"
}
variable "template_name" {
  type    = string
  default = "grayspace-windows-2022"
}
variable "iso_file" {
  type        = string
  description = "Windows Server 2022 ISO already on the node, e.g. local:iso/windows-server-2022-eval.iso"
}
variable "virtio_iso_file" {
  type        = string
  description = "VirtIO drivers ISO on the node, e.g. local:iso/virtio-win.iso"
}
variable "image_index" {
  type        = string
  default     = "2"
  description = "Install image in the ISO: 2 is Standard with Desktop Experience on the evaluation media"
}
variable "storage_pool" {
  type    = string
  default = "local-lvm"
}
variable "iso_storage_pool" {
  type    = string
  default = "local"
}
variable "bridge" {
  type        = string
  description = "A bridge with DHCP and internet access for the build (the management bridge)"
}
variable "admin_password" {
  type        = string
  sensitive   = true
  description = "Administrator's password during the build; slices set their own through cloud-init"
}
variable "cloudbase_init_url" {
  type    = string
  default = "https://www.cloudbase.it/downloads/CloudbaseInitSetup_x64.msi"
}

source "proxmox-iso" "windows" {
  proxmox_url              = var.proxmox_url
  username                 = var.proxmox_username
  token                    = var.proxmox_token
  node                     = var.proxmox_node
  insecure_skip_tls_verify = var.insecure_skip_tls_verify

  vm_id                = var.vm_id
  vm_name              = var.template_name
  template_description = "Windows Server 2022 for GraySpace slices: VirtIO, OpenSSH, cloudbase-init (packer/windows)"
  os                   = "win11"
  bios                 = "ovmf"
  machine              = "q35"
  cores                = 2
  memory               = 4096
  cpu_type             = "x86-64-v2-AES"
  scsi_controller      = "virtio-scsi-single"
  qemu_agent           = true

  efi_config {
    efi_storage_pool  = var.storage_pool
    efi_type          = "4m"
    pre_enrolled_keys = true
  }

  boot_iso {
    type     = "ide"
    iso_file = var.iso_file
    unmount  = true
  }

  additional_iso_files {
    type     = "ide"
    index    = 1
    iso_file = var.virtio_iso_file
    unmount  = true
  }

  # autounattend.xml and the first-boot script, on a generated CD
  additional_iso_files {
    type  = "ide"
    index = 3
    cd_content = {
      "autounattend.xml" = templatefile("${path.root}/autounattend.pkrtpl.xml", {
        admin_password = var.admin_password
        image_index    = var.image_index
      })
    }
    cd_files         = ["${path.root}/scripts/enable-winrm.ps1"]
    cd_label         = "UNATTEND"
    iso_storage_pool = var.iso_storage_pool
    unmount          = true
  }

  disks {
    type         = "scsi"
    disk_size    = "40G"
    storage_pool = var.storage_pool
    format       = "raw"
  }

  network_adapters {
    model  = "virtio"
    bridge = var.bridge
  }

  # Proxmox attaches a cloud-init (config drive) to clones; the template carries the drive.
  cloud_init              = true
  cloud_init_storage_pool = var.storage_pool

  boot      = "order=scsi0;ide2"
  boot_wait = "3s"
  boot_command = [
    "<spacebar><spacebar><spacebar>"
  ]

  communicator   = "winrm"
  winrm_username = "Administrator"
  winrm_password = var.admin_password
  winrm_timeout  = "2h"
  winrm_insecure = true
  winrm_use_ssl  = false
}

build {
  sources = ["source.proxmox-iso.windows"]

  provisioner "powershell" {
    script = "./scripts/install-guest-tools.ps1"
  }

  provisioner "powershell" {
    script = "./scripts/install-openssh.ps1"
  }

  provisioner "windows-restart" {
    restart_timeout = "30m"
  }

  provisioner "powershell" {
    environment_vars = ["CLOUDBASE_INIT_URL=${var.cloudbase_init_url}"]
    script           = "./scripts/install-cloudbase-init.ps1"
  }

  # Last: generalize with cloudbase-init's unattend and shut down; Packer then makes the template.
  provisioner "powershell" {
    script = "./scripts/sysprep.ps1"
  }
}
