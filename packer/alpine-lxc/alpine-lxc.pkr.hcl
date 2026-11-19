# The GraySpace Alpine LXC template: routers, core DNS and CAs, and slice edges.
#
# Proxmox's own Alpine template with everything the plays and roles install already in it: sshd
# (key login for root) and python3 for Ansible, FRR, iproute2, dnsmasq, BIND, the step-ca
# dependencies, iptables, and the frr_exporter and node_exporter binaries. A guest made from it
# needs no hook script and no package downloads.
#
# Built with Docker, not on a Proxmox node: the template is only a root filesystem tarball.
#
#   task image:build                 # imports the Proxmox template, then packer init + build
#
# Output: output/grayspace-alpine-<image_version>.tar.zst and its .sha256. Publish it at the URL
# in images/images.csv (the grayspace-alpine row); bump image_version for every published build.

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
  default     = "3.24-1"
  description = "<Alpine release>-<build>; part of the file name, so a new build is a new file"
}
variable "base_image" {
  type        = string
  default     = "grayspace/pve-alpine:3.24"
  description = "Proxmox's Alpine template, docker import-ed (task image:build)"
}
# The same as ansible/versions.yml (tests/test_packer.py checks)
variable "frr_exporter_version" {
  type    = string
  default = "1.8.1"
}
variable "node_exporter_version" {
  type    = string
  default = "1.9.0"
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
  output    = "${path.root}/output/grayspace-alpine-${var.image_version}"
}

source "docker" "alpine" {
  image       = var.base_image
  pull        = false
  export_path = "${local.output}.tar"
  run_command = ["-d", "-i", "-t", "--network=${var.network}", "--entrypoint=/bin/sh", "--", "{{.Image}}"]
}

build {
  sources = ["source.docker.alpine"]

  provisioner "file" {
    sources     = ["${path.root}/../../ansible/templates/frr_exporter.service", "${path.root}/../../ansible/templates/node_exporter.service"]
    destination = "/tmp/"
  }

  provisioner "shell" {
    script = "${path.root}/scripts/provision.sh"
    environment_vars = concat(local.proxy_env, [
      "FRR_EXPORTER_VERSION=${var.frr_exporter_version}",
      "NODE_EXPORTER_VERSION=${var.node_exporter_version}",
    ])
  }

  # Docker leaves /.dockerenv in the export; zstd decompresses fastest, inside the create slot
  post-processor "shell-local" {
    inline = [
      "set -e",
      "tar --delete -f '${local.output}.tar' .dockerenv",
      "docker run --rm -i --network=${var.network} ${join(" ", [for e in local.proxy_env : "-e '${e}'"])} ${var.base_image} sh -c 'apk add -q zstd >/dev/null && zstd -19 -T0 -c' < '${local.output}.tar' > '${local.output}.tar.zst'",
      "rm '${local.output}.tar'",
      "cd '${path.root}/output' && sha256sum 'grayspace-alpine-${var.image_version}.tar.zst' > 'grayspace-alpine-${var.image_version}.tar.zst.sha256'",
    ]
  }
}
