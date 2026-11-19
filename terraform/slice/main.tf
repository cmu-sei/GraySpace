locals {
  # The base's simple zone (terraform/networks.tf); slice vnets join it.
  zone    = "${var.environment_id}1"
  tags    = [var.environment_id, "slice-${var.instance}"]
  ssh_key = trimspace(file("${path.module}/${var.ssh_public_key_file}"))
}

# --- Networks ---
#
# Vnet IDs are <environment_id>s000 … s999, allocated across all instances by GraySpace, so they
# never enter the base's positional v100 … sequence.

resource "proxmox_sdn_vnet" "slice" {
  for_each = var.vnets
  id       = "${var.environment_id}${each.key}"
  alias    = each.value
  zone     = local.zone

  depends_on = [proxmox_sdn_applier.finalizer]
}

# Applies pending SDN changes once the vnets exist, and again whenever they change. SDN applies
# are cluster-wide: they also apply the base's pending changes, which is why jobs hold the node
# lock.
resource "proxmox_sdn_applier" "applier" {
  depends_on = [proxmox_sdn_vnet.slice]
  lifecycle {
    replace_triggered_by = [proxmox_sdn_vnet.slice]
  }
}

# Destroyed last, so the apply it triggers removes the deleted vnets from the running config.
resource "proxmox_sdn_applier" "finalizer" {
}

# --- Edge routers (Alpine) ---

resource "proxmox_virtual_environment_container" "edge" {
  for_each      = var.edges
  node_name     = var.proxmox_node
  unprivileged  = true
  start_on_boot = true
  tags          = local.tags
  depends_on    = [proxmox_sdn_applier.applier]
  # Edges made before the GraySpace image keep theirs: replacing them would rebuild the site
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
    template_file_id = var.edge_template
    type             = "alpine"
  }
  dynamic "network_interface" {
    for_each = { for i, iface in each.value.interfaces : i => iface }
    content {
      name   = "eth${network_interface.key}"
      bridge = "${var.environment_id}${network_interface.value.bridge}"
    }
  }
  # The management NIC is last and uses DHCP, as in the base.
  network_interface {
    name   = "eth${length(each.value.interfaces)}"
    bridge = var.proxmox_mgmt_bridge
  }
  initialization {
    hostname = each.key
    dns {
      servers = [each.value.resolver]
    }
    dynamic "ip_config" {
      for_each = each.value.interfaces
      content {
        ipv4 {
          address = ip_config.value.address
          gateway = ip_config.value.gateway
        }
      }
    }
    ip_config {
      ipv4 { address = "dhcp" }
    }
    user_account {
      keys     = [local.ssh_key]
      password = var.router_password
    }
  }
}

# --- Hosts ---

resource "proxmox_virtual_environment_container" "lxc" {
  for_each      = var.lxc_hosts
  node_name     = var.proxmox_node
  unprivileged  = true
  start_on_boot = true
  tags          = local.tags
  depends_on    = [proxmox_sdn_applier.applier]

  cpu { cores = each.value.cores }
  memory { dedicated = each.value.memory }
  disk {
    datastore_id = var.proxmox_blockstore
    size         = each.value.disk
  }
  operating_system {
    template_file_id = each.value.template
    type             = each.value.os_type
  }
  network_interface {
    name   = "eth0"
    bridge = "${var.environment_id}${each.value.bridge}"
  }
  network_interface {
    name   = "eth1"
    bridge = var.proxmox_mgmt_bridge
  }
  features {
    nesting = true
  }
  # Address, gateway and resolver at creation, so the default route doesn't depend on Ansible.
  initialization {
    hostname = each.key
    dns {
      servers = [each.value.resolver]
    }
    ip_config {
      ipv4 {
        address = each.value.address
        gateway = each.value.gateway
      }
    }
    ip_config {
      ipv4 { address = "dhcp" }
    }
    user_account {
      keys     = [local.ssh_key]
      password = var.router_password
    }
  }
}

resource "proxmox_virtual_environment_container" "oci" {
  for_each      = var.oci_hosts
  node_name     = var.proxmox_node
  unprivileged  = true
  start_on_boot = true
  tags          = local.tags
  depends_on    = [proxmox_sdn_applier.applier]

  cpu { cores = each.value.cores }
  memory { dedicated = each.value.memory }
  disk {
    datastore_id = var.proxmox_blockstore
    size         = each.value.disk
  }
  operating_system { template_file_id = each.value.template }
  network_interface {
    name   = "eth0"
    bridge = "${var.environment_id}${each.value.bridge}"
    # OCI images usually lack network tools; the PVE host configures the interface.
    host_managed = true
  }
  console {
    enabled   = true
    tty_count = 2
    type      = "console"
  }
  environment_variables = each.value.env
  initialization {
    hostname   = each.key
    entrypoint = each.value.entrypoint
    dns {
      servers = [each.value.resolver]
    }
    ip_config {
      ipv4 {
        address = each.value.address
        gateway = each.value.gateway
      }
    }
  }
  lifecycle {
    # Settings taken at creation.
    ignore_changes = [initialization[0].entrypoint, environment_variables]
  }
}

resource "proxmox_virtual_environment_vm" "vm" {
  for_each   = var.vm_hosts
  node_name  = var.proxmox_node
  name       = each.key
  on_boot    = true
  tags       = local.tags
  depends_on = [proxmox_sdn_applier.applier]

  dynamic "clone" {
    for_each = each.value.clone_vm_id == null ? [] : [each.value.clone_vm_id]
    content {
      vm_id        = clone.value
      full         = true
      datastore_id = var.proxmox_blockstore
    }
  }
  cpu {
    cores = each.value.cores
    type  = "x86-64-v2-AES"
  }
  memory { dedicated = each.value.memory }
  disk {
    datastore_id = var.proxmox_blockstore
    import_from  = each.value.import_from
    size         = each.value.disk
    interface    = "scsi0"
  }
  network_device {
    bridge = "${var.environment_id}${each.value.bridge}"
  }
  network_device {
    bridge = var.proxmox_mgmt_bridge
  }
  operating_system {
    type = each.value.os_family == "windows" ? "win11" : "l26"
  }
  agent {
    enabled = true
  }
  initialization {
    datastore_id = var.proxmox_blockstore
    # cloudbase-init on Windows templates reads a config drive (packer/windows/).
    type = each.value.os_family == "windows" ? "configdrive2" : "nocloud"
    dns {
      servers = [each.value.resolver]
    }
    ip_config {
      ipv4 {
        address = each.value.address
        gateway = each.value.gateway
      }
    }
    ip_config {
      ipv4 { address = "dhcp" }
    }
    user_account {
      username = each.value.os_family == "windows" ? "Administrator" : "grayspace"
      keys     = [local.ssh_key]
      password = var.router_password
    }
  }
}
