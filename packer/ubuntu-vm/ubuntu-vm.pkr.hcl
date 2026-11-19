# The GraySpace Ubuntu VM image: a multi-purpose Ubuntu 24.04 server for enclave hosts that need a
# VM (the ad-dc role: Samba's sysvol needs extended attributes and ACLs a container lacks).
#
# Ubuntu's minimal Noble cloud image (about 250 MB, against the standard image's 600) with the QEMU
# guest agent (Terraform and Proxmox read the VM's
# addresses from it), cloud-init for Proxmox's NoCloud drive (static address, the grayspace user and
# Terraform's key), and the packages of the common host roles (ad-dc, dns, file, mail, web,
# ghosts-client) with their services off: each role turns on its own, and skips the install (and the
# package index download) when the image has them. Nothing is provisioned at build time: a domain's
# realm, password and SIDs belong to each instance.
#
# Built with QEMU in a container (packer/ubuntu-vm/builder), with KVM when the host has /dev/kvm and
# software emulation (much slower) otherwise:
#
#   task image:build:ubuntu-vm
#
# Output: output/grayspace-ubuntu-vm-<image_version>.qcow2 and its .sha256. Publish it at the URL in
# images/images.csv (the grayspace-ubuntu-vm row); bump image_version for every published build.

packer {
  required_plugins {
    qemu = {
      version = ">= 1.1.0"
      source  = "github.com/hashicorp/qemu"
    }
  }
}

variable "image_version" {
  type        = string
  default     = "24.04-1"
  description = "<Ubuntu release>-<build>; part of the file name, so a new build is a new file"
}
# The minimal image: no man pages, kernel headers or most of the standard tools; the build adds
# what the roles and operators need
variable "cloud_image_url" {
  type    = string
  default = "https://cloud-images.ubuntu.com/minimal/releases/noble/release/ubuntu-24.04-minimal-cloudimg-amd64.img"
}
variable "cloud_image_checksum" {
  type    = string
  default = "file:https://cloud-images.ubuntu.com/minimal/releases/noble/release/SHA256SUMS"
}
# kvm, or tcg without /dev/kvm (the task picks)
variable "accelerator" {
  type    = string
  default = "kvm"
}
# The image's virtual size; instances grow it to their disk size (cloud-init's growpart)
variable "disk_size" {
  type    = string
  default = "8G"
}
# The build VM's downloads go through the builder's proxy, if it has one. The VM reaches the build
# host as 10.0.2.2 (QEMU's user network), not as localhost
variable "http_proxy" {
  type    = string
  default = env("http_proxy")
}
variable "https_proxy" {
  type    = string
  default = env("https_proxy")
}
variable "no_proxy" {
  type    = string
  default = env("no_proxy")
}

locals {
  name      = "grayspace-ubuntu-vm-${var.image_version}"
  build_dir = "${path.root}/output/build-${var.image_version}"
  proxy_env = ["http_proxy=${var.http_proxy}", "https_proxy=${var.https_proxy}", "no_proxy=${var.no_proxy}"]
}

source "qemu" "ubuntu" {
  iso_url        = var.cloud_image_url
  iso_checksum   = var.cloud_image_checksum
  disk_image     = true
  disk_size      = var.disk_size
  format         = "qcow2"
  disk_interface = "virtio"
  # Freed blocks reach the image (fstrim at the end), and the conversion compresses it
  disk_discard       = "unmap"
  disk_detect_zeroes = "unmap"
  disk_compression   = true
  net_device         = "virtio-net"
  accelerator        = var.accelerator
  cpus               = 2
  memory             = 2048
  headless           = true
  output_directory   = local.build_dir
  vm_name            = "${local.name}.qcow2"

  # The build's cloud-init seed: a build user removed at shutdown (ISO made with xorriso)
  cd_files = ["${path.root}/seed/meta-data", "${path.root}/seed/user-data"]
  cd_label = "cidata"

  communicator = "ssh"
  ssh_username = "packer"
  ssh_password = "packer"
  # Software emulation boots in minutes, not seconds
  ssh_timeout = "45m"
  # Everything but the build user is cleaned by the last provisioner; the user goes here, in one
  # root shell (sudo refuses a user it can't find)
  shutdown_command = "sudo sh -c 'userdel -rf packer; rm -f /etc/sudoers.d/90-cloud-init-users; shutdown -P now'"
  shutdown_timeout = "15m"
}

build {
  sources = ["source.qemu.ubuntu"]

  provisioner "shell" {
    script           = "${path.root}/scripts/provision.sh"
    environment_vars = concat(local.proxy_env, ["IMAGE_VERSION=${var.image_version}"])
    execute_command  = "sudo -E sh -c '{{ .Vars }} {{ .Path }}'"
    # cloud-init in the build VM may still hold apt when SSH comes up
    start_retry_timeout = "10m"
  }

  provisioner "shell" {
    script          = "${path.root}/scripts/cleanup.sh"
    execute_command = "sudo -E sh -c '{{ .Vars }} {{ .Path }}'"
  }

  post-processor "shell-local" {
    inline = [
      "set -e",
      "mv '${local.build_dir}/${local.name}.qcow2' '${path.root}/output/${local.name}.qcow2'",
      "rm -rf '${local.build_dir}'",
      "cd '${path.root}/output' && sha256sum '${local.name}.qcow2' > '${local.name}.qcow2.sha256'",
    ]
  }
}
