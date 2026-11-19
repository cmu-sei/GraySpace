resource "proxmox_virtual_environment_container" "core_dns" {
  for_each      = var.core_dns
  node_name     = var.proxmox_node
  unprivileged  = true
  start_on_boot = true
  depends_on    = [proxmox_sdn_applier.vnet_applier]
  tags          = [var.environment_id]
  # A new image (or a new URL for it) is for new guests (routers.tf)
  lifecycle {
    ignore_changes = [operating_system[0].template_file_id]
  }

  cpu { cores = each.value.cores }
  memory { dedicated = each.value.memory }
  disk {
    datastore_id = var.proxmox_blockstore
    size         = each.value.disk
  }
  operating_system {
    template_file_id = local.alpine_template_id
    type             = "alpine"
  }
  dynamic "network_interface" {
    for_each = { for i, iface in each.value.interfaces : i => iface }
    content {
      name   = "eth${network_interface.key}"
      bridge = "${var.environment_id}${network_interface.value.bridge}"
    }
  }
  network_interface {
    name   = "eth${length(each.value.interfaces)}"
    bridge = var.proxmox_mgmt_bridge
  }
  initialization {
    hostname = each.key
    dynamic "ip_config" {
      for_each = { for i, iface in each.value.interfaces : i => iface }
      content {
        ipv4 { address = ip_config.value.ip }
      }
    }
    ip_config {
      ipv4 { address = "dhcp" }
    }
    user_account {
      keys     = [trimspace(tls_private_key.container_key.public_key_openssh)]
      password = var.router_password
    }
  }
}

locals {
  core_dns = [
    for key, instance in proxmox_virtual_environment_container.core_dns : {
      name = key,
      ip   = length(values(instance.ipv4)) != 0 ? slice(values(instance.ipv4), length(values(instance.ipv4)) - 1, length(values(instance.ipv4)))[0] : ""
    }
  ]
}

resource "local_file" "ansible_core_dns_inventory" {
  filename = "../ansible/inventory/core_dns_inventory.ini"
  content = templatefile("${path.module}/templates/inventory.tpl", {
    group = "core_dns"
    hosts = local.core_dns
  })
}
