terraform {
  required_providers {
    crucible = {
      source  = "cmu-sei/crucible"
      version = ">=2.7.1"
    }
  }
}

resource "crucible_player_view" "grayspace" {
  name        = "${var.environment_id}-grayspace"
  description = "This was created from terraform!"
  status      = "Active"

  application {
    name            = "VMs"
    app_template_id = "e4698c62-7322-445b-946d-cafe1cf510bc"
  }

  team {
    name = "Blue"
    role = "View Admin"
    user {
      user_id = var.crucible_user_id
    }
    app_instance {
      name          = "VMs"
      display_order = 0
    }
  }
}

resource "crucible_player_virtual_machine" "prometheus" {
  name     = var.prometheus_hostname
  team_ids = [crucible_player_view.grayspace.team[0].team_id]
  proxmox_vm_info {
    id   = var.prometheus_vm_id
    node = var.proxmox_node
    type = "LXC"
  }
  embeddable = true
}

resource "crucible_player_virtual_machine" "routers" {
  for_each = var.router_vm_ids
  name     = each.key
  team_ids = [crucible_player_view.grayspace.team[0].team_id]
  proxmox_vm_info {
    id   = each.value
    node = var.proxmox_node
    type = "LXC"
  }
  embeddable = true
}

resource "crucible_player_virtual_machine" "core_services" {
  for_each = var.core_service_vm_ids
  name     = each.key
  team_ids = [crucible_player_view.grayspace.team[0].team_id]
  proxmox_vm_info {
    id   = each.value
    node = var.proxmox_node
    type = "LXC"
  }
  embeddable = true
}
