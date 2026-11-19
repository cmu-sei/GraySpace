# The connection's settings come from the environment file (TF_VAR_crucible_*, control/platform.py);
# the view from player.tfvars.json, which the backend writes from the draft's player section.

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
variable "crucible_user_id" {
  description = "The service user, admin of the view through its Admin team; none when empty"
  type        = string
  default     = ""
}

variable "view" {
  type = object({
    name        = string
    description = optional(string, "")
  })
}

variable "applications" {
  description = "The view's applications: a Player application template, or a URL of its own"
  type = list(object({
    name               = string
    template_id        = optional(string)
    url                = optional(string)
    icon               = optional(string)
    embeddable         = optional(bool, true)
    load_in_background = optional(bool, false)
  }))
  default = []
}

variable "instance_applications" {
  description = <<-EOT
    The applications teams get instances of; all of theirs when null. The provider (2.7, 2.8) adds a
    team's instance of an application created in the same apply with the plan's application ID, which
    is still unknown ("invalid UUID length: 0"), so the backend first applies with the applications
    the view already has, then again with all.
  EOT
  type        = list(string)
  default     = null
}

variable "teams" {
  description = "The participant teams, each with its applications in display order"
  type = list(object({
    name         = string
    role         = optional(string)
    permissions  = optional(list(string), [])
    applications = optional(list(string), [])
  }))
}

variable "vms" {
  description = "Hostname => its Proxmox VM ID, node and type (LXC or QEMU), and the teams that see it"
  type = map(object({
    id    = number
    node  = string
    type  = string
    teams = list(string)
  }))
  default = {}

  validation {
    condition     = alltrue([for v in values(var.vms) : contains(["LXC", "QEMU"], v.type)])
    error_message = "A VM's type is LXC or QEMU."
  }
}
