resource "tls_private_key" "container_key" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "local_file" "public_key" {
  content         = tls_private_key.container_key.public_key_openssh
  file_permission = 0644
  filename        = "../.ssh/terraform_key.pub"
}

resource "local_file" "private_key" {
  content         = tls_private_key.container_key.private_key_openssh
  file_permission = 0600
  filename        = "../.ssh/terraform_key"
}

# A copy in the user's ~/.ssh, for ssh -i ~/.ssh/terraform_key. Deployments run by the control plane
# turn it off: their work trees share one user, and each would overwrite the others' key.
resource "local_file" "private_key_app" {
  count           = var.write_home_ssh_key ? 1 : 0
  content         = tls_private_key.container_key.private_key_openssh
  file_permission = 0600
  filename        = pathexpand("~/.ssh/terraform_key")
}

resource "local_file" "public_key_app" {
  count           = var.write_home_ssh_key ? 1 : 0
  content         = tls_private_key.container_key.public_key_openssh
  file_permission = 0644
  filename        = pathexpand("~/.ssh/terraform_key.pub")
}

moved {
  from = local_file.private_key_app
  to   = local_file.private_key_app[0]
}

moved {
  from = local_file.public_key_app
  to   = local_file.public_key_app[0]
}

# The GraySpace Ubuntu LXC image (packer/ubuntu-lxc) for Prometheus, fetched as routers.tf fetches
# the Alpine image: the grayspace-ubuntu-lxc row of images/images.csv (TF_VAR_ubuntu_lxc_image_url
# overrides it), named after the URL's file name, uploaded or downloaded as alpine_image_transfer
# says. Enclave hosts get their own copy from terraform/images/.
locals {
  ubuntu_lxc_image_url = coalesce(var.ubuntu_lxc_image_url, one([
    for i in csvdecode(file("${path.module}/../images/images.csv")) : i.source if i.name == "grayspace-ubuntu-lxc"
  ]))
  ubuntu_lxc_image_file = "${var.environment_id}-${var.alpine_image_transfer == "upload" ? "base-up" : "base"}-${basename(local.ubuntu_lxc_image_url)}"
  ubuntu_lxc_template_id = (var.alpine_image_transfer == "upload" ? proxmox_virtual_environment_file.ubuntu_lxc_image[0].id
  : proxmox_download_file.ubuntu_lxc_image[0].id)
}

resource "proxmox_download_file" "ubuntu_lxc_image" {
  count        = var.alpine_image_transfer == "download" ? 1 : 0
  content_type = "vztmpl"
  datastore_id = var.proxmox_filestore
  node_name    = var.proxmox_node
  file_name    = local.ubuntu_lxc_image_file
  url          = local.ubuntu_lxc_image_url
  depends_on   = [proxmox_sdn_applier.vnet_applier]
}

resource "proxmox_virtual_environment_file" "ubuntu_lxc_image" {
  count        = var.alpine_image_transfer == "upload" ? 1 : 0
  content_type = "vztmpl"
  datastore_id = var.proxmox_filestore
  node_name    = var.proxmox_node
  source_file {
    path      = local.ubuntu_lxc_image_url
    file_name = local.ubuntu_lxc_image_file
  }
  depends_on = [proxmox_sdn_applier.vnet_applier]
}

resource "proxmox_virtual_environment_container" "prometheus" {
  node_name = var.proxmox_node
  operating_system {
    template_file_id = local.ubuntu_lxc_template_id
    type             = "ubuntu"
  }
  initialization {
    hostname = "prometheus"
    ip_config {
      ipv4 {
        address = "dhcp"
      }
    }
    user_account {
      keys     = [trimspace(tls_private_key.container_key.public_key_openssh)]
      password = var.router_password
    }
  }
  features {
    nesting = true
  }
  cpu { cores = 2 }
  # Prometheus holds every active series in memory and replays its WAL at startup. 2 GB ran out
  # when node_exporter reported the host's disks and CPUs from every router (1.2M series); with
  # that trimmed it needs far less, and GOMEMLIMIT in prometheus.service keeps it under this.
  memory { dedicated = 4096 }
  disk {
    datastore_id = var.proxmox_blockstore
    size         = 16
  }
  network_interface {
    name   = "veth0"
    bridge = var.proxmox_mgmt_bridge
  }
  unprivileged  = true
  start_on_boot = true
  tags          = [var.environment_id]
  # A new image is for a new Prometheus: replacing it would lose its metrics. Made from Proxmox's
  # Ubuntu 22.04 before the GraySpace image, it keeps that until replaced (-replace=...prometheus)
  lifecycle {
    ignore_changes = [operating_system[0].template_file_id]
  }
}

locals {
  # The management guests: Prometheus (GraySpace itself runs in its container, docs/install.md)
  ungrouped = [
    {
      "name" = "prometheus",
      "ip"   = values(proxmox_virtual_environment_container.prometheus.ipv4)[0]
    },
  ]
}

resource "local_file" "ansible_ungrouped_inventory" {
  filename = "../ansible/inventory/ungrouped_inventory.ini"
  content = templatefile("${path.module}/templates/inventory.tpl", {
    group = "ungrouped"
    hosts = local.ungrouped
  })
}

output "grayspace_web_url" {
  description = "Where the GraySpace container serves the UI and API (TF_VAR_grayspace_url)"
  value       = var.grayspace_url
}

output "management_services" {
  description = "Management service URLs"
  value = {
    prometheus    = "http://${values(proxmox_virtual_environment_container.prometheus.ipv4)[0]}:9090"
    grayspace_web = var.grayspace_url
  }
}
# The Player view and team slice instances register their guests in; null with Crucible off. Slice
# jobs read it from this state (services/slices/stages.py, crucible_team_id).
output "crucible" {
  value = var.configure_crucible ? { view_id = module.crucible[0].view_id, team_id = module.crucible[0].team_id } : null
}

module "crucible" {
  count  = var.configure_crucible ? 1 : 0
  source = "./modules/crucible"

  environment_id   = var.environment_id
  crucible_user_id = var.crucible_user_id
  proxmox_node     = var.proxmox_node

  prometheus_hostname = proxmox_virtual_environment_container.prometheus.initialization[0].hostname
  prometheus_vm_id    = proxmox_virtual_environment_container.prometheus.vm_id

  router_vm_ids = { for k, v in proxmox_virtual_environment_container.routers : k => v.vm_id }
  core_service_vm_ids = merge(
    { for k, v in proxmox_virtual_environment_container.core_dns : k => v.vm_id },
    { for k, v in proxmox_virtual_environment_container.core_ca : k => v.vm_id },
  )
}