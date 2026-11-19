resource "proxmox_sdn_zone_simple" "simple_zone" {
  id    = "${var.environment_id}1"
  nodes = [var.proxmox_node]
  mtu   = 1500

  depends_on = [
    proxmox_sdn_applier.finalizer
  ]
}

resource "proxmox_sdn_vnet" "network_links" {
  for_each = var.networks
  id       = "${var.environment_id}${each.key}"
  alias    = each.value.description
  zone     = proxmox_sdn_zone_simple.simple_zone.id

  depends_on = [
    proxmox_sdn_applier.finalizer
  ]
}

resource "proxmox_sdn_zone_vlan" "vlan_zone" {
  id     = "${var.environment_id}2"
  nodes  = [var.proxmox_node]
  bridge = var.proxmox_vm_bridge
  mtu    = 1500

  depends_on = [
    proxmox_sdn_applier.finalizer
  ]
}

resource "proxmox_sdn_applier" "vnet_applier" {
  depends_on = [
    proxmox_sdn_zone_simple.simple_zone,
    proxmox_sdn_vnet.network_links,
    proxmox_sdn_zone_vlan.vlan_zone
  ]
}

resource "proxmox_sdn_applier" "finalizer" {
}