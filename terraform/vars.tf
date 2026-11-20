################################################################################
#
# Input Variables
# Loaded automatically from terraform.tfvars.json
#
################################################################################

variable "routers" {
  description = "A map of all router objects, including their interfaces."
  type = map(object({
    isp_name = string
    interfaces = list(object({
      bridge    = string
      ip        = string
      cidr      = string
      ospf_area = string
    }))
  }))
}
variable "core_dns" {
  description = "A map of core infrastructure services (DNS) to be created."
  type = map(object({
    image  = string
    role   = string
    zones  = list(string)
    cores  = number
    memory = number
    disk   = number
    interfaces = list(object({
      bridge = string
      ip     = string
    }))
  }))
}
variable "core_ca" {
  description = "A map of core infrastructure services (CA) to be created."
  type = map(object({
    image   = string
    role    = string
    ca_name = string
    cores   = number
    memory  = number
    disk    = number
    interfaces = list(object({
      bridge = string
      ip     = string
    }))
  }))
  default = {}
}
variable "networks" {
  description = "A map of all network bridges to be created."
  type = map(object({
    subnet      = string
    description = string
  }))
}
variable "locations" {
  type = map(object({
    latitude  = number
    longitude = number
  }))
}

# First DHCP lease offset on router customer networks, written by generate_tfvars.py. Terraform
# doesn't use it; dnsmasq and the slice allocator do.
variable "customer_dhcp_start" {
  type    = number
  default = 20
}

variable "base" {
  description = "The base version the tfvars were generated from. Not used by Terraform; read by grayspace-web."
  type = object({
    name       = string
    csv_sha256 = string
    scope      = string
  })
  default = null
}
variable "router_password" {
  type        = string
  description = "Password for the router"
}
variable "proxmox_node" {
  type        = string
  description = "Proxmox node name"
}
variable "proxmox_host_addr" {
  type        = string
  description = "Proxmox host IP address"
}
variable "proxmox_blockstore" {
  type        = string
  description = "Proxmox block storage"
}
variable "proxmox_filestore" {
  type        = string
  description = "Proxmox file storage"
}
variable "proxmox_mgmt_bridge" {
  type        = string
  description = "Proxmox management bridge"
}
variable "proxmox_vm_bridge" {
  type        = string
  description = "Proxmox VM bridge"
}
variable "environment_id" {
  type        = string
  description = "environment ID"
}
variable "write_home_ssh_key" {
  type        = bool
  default     = true
  description = "Also write the deployment's SSH key to ~/.ssh/terraform_key (off for control-plane deployments, which share a user)"
}
variable "alpine_image_url" {
  type        = string
  default     = null
  description = "Where the GraySpace Alpine image comes from, instead of images/images.csv's grayspace-alpine URL"
}
variable "ubuntu_lxc_image_url" {
  type        = string
  default     = null
  description = "Where the GraySpace Ubuntu LXC image (Prometheus) comes from, instead of images/images.csv's grayspace-ubuntu-lxc URL"
}
variable "alpine_image_transfer" {
  type        = string
  default     = "download"
  description = "Who fetches the GraySpace images the base uses (Alpine, and Ubuntu LXC for Prometheus): the node (download, the default, through the cluster's proxy) or the machine running Terraform, which uploads them (upload, for a server only that machine reaches; routers.tf)"
  validation {
    condition     = contains(["download", "upload"], var.alpine_image_transfer)
    error_message = "alpine_image_transfer is download or upload."
  }
}
variable "proxy" {
  type        = string
  description = "Proxy URL"
  default     = ""
}
variable "no_proxy" {
  type        = string
  description = "Any IP blocks or hostnames that should be ignored by the proxy"
  default     = ""
}
variable "grayspace_url" {
  description = "Where the GraySpace container serves the UI and API, reported by the grayspace_web_url output."
  type        = string
  default     = ""
}
variable "configure_crucible" {
  description = "Create a view in Crucible and add all vms/containers to it"
  type        = bool
  default     = false
}

# Crucible Provider Variables
variable "crucible_username" {
  type        = string
  description = "Crucible API username"
  default     = ""
}

variable "crucible_password" {
  type        = string
  description = "Crucible API password"
  sensitive   = true
  default     = ""
}

variable "crucible_auth_url" {
  type        = string
  description = "Crucible authentication URL"
  default     = ""
}

variable "crucible_token_url" {
  type        = string
  description = "Crucible token URL"
  default     = ""
}

variable "crucible_client_id" {
  type        = string
  description = "Crucible OAuth client ID"
  default     = ""
}

variable "crucible_client_scopes" {
  type        = list(string)
  description = "Crucible OAuth client scopes"
  default     = []
}

variable "crucible_vm_api_url" {
  type        = string
  description = "Crucible VM API URL"
  default     = ""
}

variable "crucible_player_api_url" {
  type        = string
  description = "Crucible Player API URL"
  default     = ""
}

variable "crucible_caster_api_url" {
  type        = string
  description = "Crucible Caster API URL"
  default     = ""
}

variable "crucible_user_id" {
  type        = string
  description = "Crucible user ID "
  default     = ""
}