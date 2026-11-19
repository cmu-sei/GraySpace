# The guests the enclave spec marks for participants (crucible: true on a host or site), registered
# in the base's Crucible Player view, in the same team as the base's routers. Scenario
# Builder decides what participants see. The job passes crucible_team_id from the
# base state's "crucible" output; without it (Crucible off) nothing is registered. Destroying the
# instance removes the registrations.

variable "crucible_guests" {
  description = "Hostnames of the guests to register: the edges and hosts the spec marks crucible: true"
  type        = list(string)
  default     = []
}

variable "crucible_team_id" {
  description = "The base's Player view team (terraform output crucible.team_id); null when Crucible is off"
  type        = string
  default     = null
}

variable "crucible_username" {
  type    = string
  default = ""
}
variable "crucible_password" {
  type      = string
  sensitive = true
  default   = ""
}
variable "crucible_auth_url" {
  type    = string
  default = ""
}
variable "crucible_token_url" {
  type    = string
  default = ""
}
variable "crucible_client_id" {
  type    = string
  default = ""
}
variable "crucible_client_scopes" {
  type    = list(string)
  default = []
}
variable "crucible_vm_api_url" {
  type    = string
  default = ""
}
variable "crucible_player_api_url" {
  type    = string
  default = ""
}
variable "crucible_caster_api_url" {
  type    = string
  default = ""
}

locals {
  crucible_vms = var.crucible_team_id == null ? {} : merge(
    { for k, c in proxmox_virtual_environment_container.edge : k => { vm_id = c.vm_id, type = "LXC" } },
    { for k, c in proxmox_virtual_environment_container.lxc : k => { vm_id = c.vm_id, type = "LXC" } },
    { for k, c in proxmox_virtual_environment_container.oci : k => { vm_id = c.vm_id, type = "LXC" } },
    { for k, v in proxmox_virtual_environment_vm.vm : k => { vm_id = v.vm_id, type = "QEMU" } },
  )
}

resource "crucible_player_virtual_machine" "guest" {
  # Keys are known at plan time (hostnames); VM IDs are not, so iterate the hostnames.
  for_each = var.crucible_team_id == null ? toset([]) : toset(var.crucible_guests)
  name     = each.key
  team_ids = [var.crucible_team_id]
  proxmox_vm_info {
    id   = local.crucible_vms[each.key].vm_id
    node = var.proxmox_node
    type = local.crucible_vms[each.key].type
  }
  embeddable = true
}
