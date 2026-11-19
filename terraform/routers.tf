# The GraySpace Alpine image (packer/alpine-lxc): routers, core DNS and CAs, and slice edges, which
# use this copy (services/slices/render.py names it the same way). Its URL is the grayspace-alpine
# row of images/images.csv; TF_VAR_alpine_image_url overrides it. The file is named after the URL's
# file name, which carries the image version: a new build is a new file, and the same build from
# another server keeps its volume ID.
#
# alpine_image_transfer chooses who fetches it. "download": the node, through the cluster's proxy
# (datacenter.cfg http_proxy, which has no exceptions), so the URL must be reachable that way, as a
# public release is. "upload": the machine running Terraform, directly (the provider ignores proxy
# settings), and the provider uploads it to the node over the API, for a server only that machine
# reaches. The modes name their files differently, so switching creates one before deleting the
# other; guests keep their template either way (ignore_changes).
locals {
  alpine_image_url = coalesce(var.alpine_image_url, one([
    for i in csvdecode(file("${path.module}/../images/images.csv")) : i.source if i.name == "grayspace-alpine"
  ]))
  alpine_image_file = "${var.environment_id}-${var.alpine_image_transfer == "upload" ? "base-up" : "base"}-${basename(local.alpine_image_url)}"
  alpine_template_id = (var.alpine_image_transfer == "upload" ? proxmox_virtual_environment_file.alpine_image[0].id
  : proxmox_download_file.alpine_image[0].id)
}

resource "proxmox_download_file" "alpine_image" {
  count        = var.alpine_image_transfer == "download" ? 1 : 0
  content_type = "vztmpl"
  datastore_id = var.proxmox_filestore
  node_name    = var.proxmox_node
  file_name    = local.alpine_image_file
  url          = local.alpine_image_url
  depends_on   = [proxmox_sdn_applier.vnet_applier]
}

resource "proxmox_virtual_environment_file" "alpine_image" {
  count        = var.alpine_image_transfer == "upload" ? 1 : 0
  content_type = "vztmpl"
  datastore_id = var.proxmox_filestore
  node_name    = var.proxmox_node
  source_file {
    path      = local.alpine_image_url
    file_name = local.alpine_image_file
  }
  depends_on = [proxmox_sdn_applier.vnet_applier]
}

# The hook script that installed sshd and python3 on stock Alpine, which the image has. Guests made
# with it lose it in place on the next apply, but slice edges made with it name it until their
# instance is applied again, so the snippet is left on the node rather than destroyed.
removed {
  from = proxmox_virtual_environment_file.hook_script
  lifecycle {
    destroy = false
  }
}

# Create an LXC Container for every router.
resource "proxmox_virtual_environment_container" "routers" {
  for_each  = var.routers
  node_name = var.proxmox_node
  operating_system {
    template_file_id = local.alpine_template_id
    type             = "alpine"
  }
  dynamic "network_interface" {
    for_each = { for i, iface in var.routers[each.key].interfaces : i => iface }
    content {
      name   = "eth${network_interface.key}"
      bridge = "${var.environment_id}${network_interface.value.bridge}"
    }
  }
  # Create management interface for Ansible
  network_interface {
    name   = "eth${length(each.value.interfaces)}"
    bridge = var.proxmox_mgmt_bridge
  }
  initialization {
    hostname = each.key
    dynamic "ip_config" {
      for_each = { for i, iface in each.value.interfaces : i => iface }
      content {
        ipv4 {
          address = "${ip_config.value.ip}/${ip_config.value.cidr}"
        }
      }
    }
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
  cpu { cores = 1 }
  memory { dedicated = 256 }
  disk {
    datastore_id = var.proxmox_blockstore
  }
  unprivileged  = true
  start_on_boot = true
  depends_on    = [proxmox_sdn_applier.vnet_applier]
  tags          = [var.environment_id]
  # A new image (or a new URL for it) is for new guests: replacing every router is not
  lifecycle {
    ignore_changes = [operating_system[0].template_file_id]
  }
}

locals {
  routers = [
    for key, instance in proxmox_virtual_environment_container.routers : {
      name = key,
      ip   = length(values(instance.ipv4)) != 0 ? slice(values(instance.ipv4), length(values(instance.ipv4)) - 1, length(values(instance.ipv4)))[0] : ""
    }
  ]
}

resource "local_file" "ansible_router_inventory" {
  filename = "../ansible/inventory/router_inventory.ini"
  content = templatefile("${path.module}/templates/inventory.tpl", {
    group = "routers"
    hosts = local.routers
  })
}