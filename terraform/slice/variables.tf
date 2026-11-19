# --- From the environment file (TF_VAR_*), shared with the base ---

variable "environment_id" {
  type = string
  validation {
    # Proxmox limits SDN IDs to 8 characters; slice vnets are <environment_id>s<3 digits>.
    condition     = length(var.environment_id) <= 4
    error_message = "environment_id must be at most 4 characters for slice vnet IDs to fit Proxmox's 8."
  }
}
variable "proxmox_node" {
  type = string
}
variable "proxmox_host_addr" {
  type = string
}
variable "proxmox_blockstore" {
  type = string
}
variable "proxmox_mgmt_bridge" {
  type = string
}
variable "router_password" {
  type      = string
  sensitive = true
}

# --- From instances/<instance>.tfvars.json, fully resolved by GraySpace (no allocation here) ---

variable "instance" {
  description = "The instance ID; also the hostname prefix and this workspace's name"
  type        = string
}

variable "vnets" {
  description = "Slice vnet IDs (without environment_id), e.g. s000, to their alias"
  type        = map(string)
  default     = {}
}

variable "ssh_public_key_file" {
  description = "The base's SSH public key, relative to this module"
  type        = string
  default     = "../../.ssh/terraform_key.pub"
}

variable "edge_template" {
  description = "Template volume ID of edge routers (the GraySpace Alpine image)"
  type        = string
  default     = ""
}

variable "edges" {
  description = "Edge routers. Interfaces in order: WANs on base customer networks, then segments"
  type = map(object({
    cores    = number
    memory   = number
    disk     = number
    resolver = string
    interfaces = list(object({
      bridge  = string
      address = string
      gateway = optional(string)
    }))
  }))
  default = {}
}

variable "lxc_hosts" {
  type = map(object({
    cores    = number
    memory   = number
    disk     = number
    template = string
    os_type  = string
    resolver = string
    bridge   = string
    address  = string
    gateway  = string
  }))
  default = {}
}

variable "oci_hosts" {
  description = "Application containers (e.g. Pandora). No management NIC: nothing would remove its default route"
  type = map(object({
    cores      = number
    memory     = number
    disk       = number
    template   = string
    resolver   = string
    bridge     = string
    address    = string
    gateway    = string
    env        = map(string)
    entrypoint = optional(string)
  }))
  default = {}
}

variable "vm_hosts" {
  description = "VMs, from a downloaded cloud image (import_from) or by cloning a template (clone_vm_id)"
  type = map(object({
    cores       = number
    memory      = number
    disk        = number
    os_family   = string
    import_from = optional(string)
    clone_vm_id = optional(number)
    resolver    = string
    bridge      = string
    address     = string
    gateway     = string
  }))
  default = {}
}
