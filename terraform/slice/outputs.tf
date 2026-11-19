# Per guest: its Proxmox VM ID and the management address Ansible and the manifest use (null for
# OCI hosts, which have no management NIC). The job writes the inventory from this.
output "guests" {
  value = merge(
    {
      for k, c in proxmox_virtual_environment_container.edge : k => {
        vm_id   = c.vm_id
        kind    = "edge"
        mgmt_ip = try(c.ipv4["eth${length(var.edges[k].interfaces)}"], null)
      }
    },
    {
      for k, c in proxmox_virtual_environment_container.lxc : k => {
        vm_id   = c.vm_id
        kind    = "lxc"
        mgmt_ip = try(c.ipv4["eth1"], null)
      }
    },
    {
      for k, c in proxmox_virtual_environment_container.oci : k => {
        vm_id   = c.vm_id
        kind    = "oci"
        mgmt_ip = null
      }
    },
    {
      for k, v in proxmox_virtual_environment_vm.vm : k => {
        vm_id = v.vm_id
        kind  = "vm"
        # The guest agent reports every interface; the management address is the one that is
        # neither loopback nor the simulated address.
        mgmt_ip = try([
          for a in flatten(v.ipv4_addresses) : a
          if a != split("/", var.vm_hosts[k].address)[0] && !startswith(a, "127.")
        ][0], null)
      }
    },
  )
}

output "vnets" {
  value = { for k, v in proxmox_sdn_vnet.slice : k => v.id }
}
