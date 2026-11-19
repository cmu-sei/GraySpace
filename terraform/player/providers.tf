# The deployment's Crucible Player view, apart from the base: its own state and provider lock, so
# a view changes without a base converge and the provider can move on its own. The backend applies
# it (services/player.py) with a refresh: 2.6+ manages a view's teams and applications as a whole.
terraform {
  required_version = ">= 1.6"
  required_providers {
    crucible = {
      source  = "cmu-sei/crucible"
      version = "~>2.7.1" # as the base (terraform/providers.tf)
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
