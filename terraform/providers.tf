################################################################################
#
# Terraform and Provider Configuration
#
################################################################################

terraform {
  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~>0.123.1"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
    local = {
      source  = "hashicorp/local"
      version = ">= 2.1.0"
    }
    null = {
      source  = "hashicorp/null"
      version = ">= 3.0.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.0.0"
    }
    crucible = {
      source  = "cmu-sei/crucible"
      version = "~>2.8.0" # 2.6.0+ reuses its Keycloak token (2.5 logged in on every API call)
    }
  }
  required_version = ">= 1.0"
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