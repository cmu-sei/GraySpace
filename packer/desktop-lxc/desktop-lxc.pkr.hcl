# The GraySpace desktop LXC template: an Ubuntu workstation reached in the browser (noVNC).
#
# Proxmox's Ubuntu 24.04 template with a desktop for the user `user` (passwordless sudo): Xvfb,
# Fluxbox with desktop icons (pcmanfm) and a panel (tint2), Firefox from Mozilla's repository (the
# Ubuntu package is a snap, which doesn't run in a container), a terminal, a text editor, a file
# manager and the usual admin tools. x11vnc serves the display on localhost:5900 without a password
# and websockify bridges it to :6080, where the web UI's desktop proxy connects over management.
#
# Built with Docker, not on a Proxmox node: the template is only a root filesystem tarball.
#
#   task image:build:desktop         # imports the Proxmox template, then packer init + build
#
# Output: output/grayspace-desktop-<image_version>.tar.zst and its .sha256. Publish it at the URL
# in images/images.csv (the grayspace-desktop row); bump image_version for every published build.

packer {
  required_plugins {
    docker = {
      version = ">= 1.1.0"
      source  = "github.com/hashicorp/docker"
    }
  }
}

variable "image_version" {
  type        = string
  default     = "24.04-1"
  description = "<Ubuntu release>-<build>; part of the file name, so a new build is a new file"
}
variable "base_image" {
  type        = string
  default     = "grayspace/pve-ubuntu:24.04"
  description = "Proxmox's Ubuntu template, docker import-ed (task image:build:desktop)"
}

# The build containers' network: the host's, so they resolve names as the builder does (Docker's
# bridge network falls back to public resolvers a site's network may block)
variable "network" {
  type    = string
  default = "host"
}
# The build container's downloads go through the builder's proxy, if it has one
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
  proxy_env = ["http_proxy=${var.http_proxy}", "https_proxy=${var.https_proxy}", "no_proxy=${var.no_proxy}"]
  output    = "${path.root}/output/grayspace-desktop-${var.image_version}"
}

source "docker" "ubuntu" {
  image       = var.base_image
  pull        = false
  export_path = "${local.output}.tar"
  run_command = ["-d", "-i", "-t", "--network=${var.network}", "--entrypoint=/bin/sh", "--", "{{.Image}}"]
}

build {
  sources = ["source.docker.ubuntu"]

  provisioner "file" {
    source      = "${path.root}/files"
    destination = "/tmp/"
  }

  provisioner "shell" {
    script           = "${path.root}/scripts/provision.sh"
    environment_vars = local.proxy_env
  }

  # Docker leaves /.dockerenv in the export; zstd decompresses fastest, inside the create slot
  post-processor "shell-local" {
    inline = [
      "set -e",
      "tar --delete -f '${local.output}.tar' .dockerenv",
      "docker run --rm -i --network=${var.network} ${join(" ", [for e in local.proxy_env : "-e '${e}'"])} alpine:3.24 sh -c 'apk add -q zstd >/dev/null && zstd -19 -T0 -c' < '${local.output}.tar' > '${local.output}.tar.zst'",
      "rm '${local.output}.tar'",
      "cd '${path.root}/output' && sha256sum 'grayspace-desktop-${var.image_version}.tar.zst' > 'grayspace-desktop-${var.image_version}.tar.zst.sha256'",
    ]
  }
}
