# Plans the slice module against the golden rendered examples (tests/golden/slices/), with the
# Proxmox provider mocked, so the module's shape is checked without a Proxmox node:
#
#   cd terraform/slice && terraform init -backend=false && terraform test -var-file=../../tests/golden/slices/meridian-bank.tfvars.json
#
# The run blocks below hold for every example; tests/test_terraform.py runs them all.

mock_provider "proxmox" {}
mock_provider "crucible" {}

variables {
  environment_id      = "abc"
  proxmox_node        = "pve"
  proxmox_host_addr   = "pve.invalid"
  proxmox_blockstore  = "local-lvm"
  proxmox_mgmt_bridge = "vmbr0"
  router_password     = "test"
  ssh_public_key_file = "tests/fixtures/terraform_key.pub"
}

run "plan" {
  command = plan

  assert {
    condition     = length(proxmox_sdn_vnet.slice) == length(var.vnets)
    error_message = "One vnet per slice segment"
  }

  assert {
    condition     = alltrue([for k, v in proxmox_sdn_vnet.slice : v.id == "abc${k}" && v.zone == "abc1" && length(v.id) <= 8])
    error_message = "Slice vnets are <environment_id>sNNN in the base simple zone, at most 8 characters"
  }

  assert {
    condition = alltrue([
      for c in concat(values(proxmox_virtual_environment_container.edge), values(proxmox_virtual_environment_container.lxc), values(proxmox_virtual_environment_container.oci)) :
      contains(c.tags, "abc") && contains(c.tags, "slice-${var.instance}")
    ])
    error_message = "Every container is tagged with environment_id and slice-<instance>"
  }

  assert {
    condition     = alltrue([for v in proxmox_virtual_environment_vm.vm : contains(v.tags, "slice-${var.instance}")])
    error_message = "Every VM is tagged slice-<instance>"
  }

  assert {
    condition = alltrue([
      for k, c in proxmox_virtual_environment_container.edge :
      c.network_interface[length(var.edges[k].interfaces)].bridge == "vmbr0" && length(c.network_interface) == length(var.edges[k].interfaces) + 1
    ])
    error_message = "Edges have their WANs and segments, then the management NIC last"
  }

  assert {
    condition = alltrue([
      for k, c in proxmox_virtual_environment_container.lxc :
      c.network_interface[0].bridge == "abc${var.lxc_hosts[k].bridge}" && c.network_interface[1].bridge == "vmbr0"
      && c.initialization[0].ip_config[0].ipv4[0].gateway == var.lxc_hosts[k].gateway
      && c.initialization[0].dns[0].servers[0] == var.lxc_hosts[k].resolver
    ])
    error_message = "Hosts get their simulated address, gateway and resolver at creation, and a management NIC"
  }

  assert {
    condition     = alltrue([for k, c in proxmox_virtual_environment_container.oci : length(c.network_interface) == 1 && c.environment_variables == var.oci_hosts[k].env])
    error_message = "OCI hosts have no management NIC and take their env at creation"
  }

  assert {
    condition     = length(proxmox_virtual_environment_container.edge) + length(proxmox_virtual_environment_container.lxc) + length(proxmox_virtual_environment_container.oci) + length(proxmox_virtual_environment_vm.vm) == length(var.edges) + length(var.lxc_hosts) + length(var.oci_hosts) + length(var.vm_hosts)
    error_message = "One guest per edge and host"
  }
}

run "long_environment_ids_are_refused" {
  command = plan

  variables {
    environment_id = "abcde"
  }

  expect_failures = [var.environment_id]
}

run "crucible_off" {
  command = plan

  assert {
    condition     = length(crucible_player_virtual_machine.guest) == 0
    error_message = "Without the base's Player view team, no guest is registered in Crucible"
  }
}

run "crucible_on_nothing_marked" {
  command = plan

  variables {
    crucible_team_id = "11111111-2222-3333-4444-555555555555"
  }

  assert {
    condition     = length(crucible_player_virtual_machine.guest) == 0
    error_message = "With a Player view but nothing marked, no guest is registered: the spec decides"
  }
}

run "crucible_on_edges_marked" {
  command = plan

  variables {
    crucible_team_id = "11111111-2222-3333-4444-555555555555"
    crucible_guests  = keys(var.edges)
  }

  assert {
    condition     = length(crucible_player_virtual_machine.guest) == length(var.edges) && alltrue([for k, v in crucible_player_virtual_machine.guest : contains(keys(var.edges), k)])
    error_message = "Only the marked guests are registered"
  }
}

run "crucible_on_all_marked" {
  command = plan

  variables {
    crucible_team_id = "11111111-2222-3333-4444-555555555555"
    crucible_guests  = concat(keys(var.edges), keys(var.lxc_hosts), keys(var.oci_hosts), keys(var.vm_hosts))
  }

  assert {
    condition     = length(crucible_player_virtual_machine.guest) == length(var.edges) + length(var.lxc_hosts) + length(var.oci_hosts) + length(var.vm_hosts)
    error_message = "Every marked guest is registered"
  }

  assert {
    condition = alltrue([
      for k, v in crucible_player_virtual_machine.guest :
      v.team_ids == tolist(["11111111-2222-3333-4444-555555555555"]) && v.proxmox_vm_info[0].type == (contains(keys(var.vm_hosts), k) ? "QEMU" : "LXC")
    ])
    error_message = "Registrations join the base's team; VM hosts are QEMU, containers LXC"
  }
}
