variable "environment_id" {
  type = string
}

variable "crucible_user_id" {
  type = string
}

variable "proxmox_node" {
  type = string
}

variable "prometheus_hostname" {
  type = string
}

variable "prometheus_vm_id" {
  type = number
}

variable "router_vm_ids" {
  type = map(number)
}

variable "core_service_vm_ids" {
  description = "Core DNS servers and CAs (LXC), by hostname"
  type        = map(number)
  default     = {}
}
