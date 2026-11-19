# The Player view's registrations, with the Crucible provider mocked:
#   cd terraform/modules/crucible && terraform init -backend=false && terraform test

mock_provider "crucible" {}

variables {
  environment_id      = "abc"
  crucible_user_id    = "00000000-0000-0000-0000-000000000000"
  proxmox_node        = "pve"
  prometheus_hostname = "prometheus"
  prometheus_vm_id    = 102
  router_vm_ids       = { "lum-chi" = 201, "lum-nyc" = 202 }
}

run "core_services" {
  command = plan

  variables {
    core_service_vm_ids = { "dns-gtld" = 401, "dns-google" = 402, "ca-cyberlock" = 403 }
  }

  assert {
    condition     = length(crucible_player_virtual_machine.core_services) == 3 && tostring(crucible_player_virtual_machine.core_services["ca-cyberlock"].proxmox_vm_info[0].id) == "403"
    error_message = "Every core DNS server and CA is registered, by hostname"
  }

  assert {
    condition     = alltrue([for v in crucible_player_virtual_machine.core_services : v.proxmox_vm_info[0].type == "LXC"])
    error_message = "Core services are LXC"
  }
}

run "without_them" {
  command = plan

  assert {
    condition     = length(crucible_player_virtual_machine.core_services) == 0
    error_message = "No core services, no registrations"
  }

  assert {
    condition     = length(crucible_player_virtual_machine.routers) == 2
    error_message = "The existing registrations are unchanged"
  }
}
