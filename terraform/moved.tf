# bpg/proxmox 0.100+ renamed these resources to short `proxmox_*` names; the old names
# are removed in v1.0. These blocks migrate existing state in place (Terraform >= 1.8).

moved {
  from = proxmox_virtual_environment_sdn_applier.finalizer
  to   = proxmox_sdn_applier.finalizer
}

moved {
  from = proxmox_virtual_environment_sdn_applier.vnet_applier
  to   = proxmox_sdn_applier.vnet_applier
}

moved {
  from = proxmox_virtual_environment_sdn_vnet.network_links
  to   = proxmox_sdn_vnet.network_links
}

moved {
  from = proxmox_virtual_environment_sdn_zone_simple.simple_zone
  to   = proxmox_sdn_zone_simple.simple_zone
}

moved {
  from = proxmox_virtual_environment_sdn_zone_vlan.vlan_zone
  to   = proxmox_sdn_zone_vlan.vlan_zone
}
