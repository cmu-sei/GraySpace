################################################################################
#
# Slice instances
#
# A root module separate from the base: one Terraform workspace per instance, local backend
# (terraform.tfstate.d/<instance>/). Applying or destroying an instance never reads or writes the
# base state. Base resources are referenced by name only: the base simple zone, the vnets of
# router customer networks and the SSH key the base writes to .ssh/.
#
# Jobs run it (grayspace-web services/slices/runner.py, or task slice:up) with the instance's
# generated instances/<instance>.tfvars.json and the TF_VAR_* settings of the environment file.
#
################################################################################

terraform {
  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~>0.114.0"
    }
    crucible = {
      source  = "cmu-sei/crucible"
      version = "~>2.7.1" # as the base (terraform/providers.tf)
    }
  }
  required_version = ">= 1.7"
}

provider "proxmox" {
  insecure      = true
  random_vm_ids = true
  ssh {
    node {
      name    = var.proxmox_node
      address = var.proxmox_host_addr
    }
  }
}

# Configured from the same TF_VAR_crucible_* settings as the base. Used only when the base's
# Player view exists (crucible_team_id set; crucible.tf).
provider "crucible" {
  username       = var.crucible_username
  password       = var.crucible_password
  auth_url       = var.crucible_auth_url
  token_url      = var.crucible_token_url
  client_id      = var.crucible_client_id
  client_scopes  = var.crucible_client_scopes
  vm_api_url     = var.crucible_vm_api_url
  player_api_url = var.crucible_player_api_url
  caster_api_url = var.crucible_caster_api_url
}
