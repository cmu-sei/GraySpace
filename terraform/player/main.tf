locals {
  admin     = var.crucible_user_id == "" ? [] : [{ name = "Admin", role = "View Admin", permissions = [], applications = [for a in var.applications : a.name], user = var.crucible_user_id }]
  all_teams = concat([for t in var.teams : merge(t, { user = "" })], local.admin)
  # The provider (2.6+) matches inline team and application blocks by position against the view as
  # the API returns it, by name: out of order, a plan "updates" each team into its neighbour
  # (swapping names, roles and members). So both go in name order.
  teams        = [for n in sort([for t in local.all_teams : t.name]) : one([for t in local.all_teams : t if t.name == n])]
  applications = [for n in sort([for a in var.applications : a.name]) : one([for a in var.applications : a if a.name == n])]
  # Team name => its Player ID, once the view exists
  team_ids = { for t in crucible_player_view.view.team : t.name => t.team_id }
}

resource "crucible_player_view" "view" {
  name              = var.view.name
  description       = var.view.description
  status            = "Active"
  create_admin_team = false

  dynamic "application" {
    for_each = local.applications
    content {
      name               = application.value.name
      app_template_id    = application.value.template_id
      url                = application.value.url
      icon               = application.value.icon
      embeddable         = tostring(application.value.embeddable)
      load_in_background = tostring(application.value.load_in_background)
    }
  }

  dynamic "team" {
    for_each = local.teams
    # Permissions null when there are none: the provider (2.6+) reads them back as null, so [] never matches
    content {
      name        = team.value.name
      role        = team.value.role
      permissions = length(team.value.permissions) == 0 ? null : team.value.permissions

      # By name too (a map's keys iterate in order); display_order keeps the team's own order.
      # Only instances of instance_applications when it's set: see the variable.
      dynamic "app_instance" {
        for_each = { for i, name in team.value.applications : name => i if var.instance_applications == null || contains(coalesce(var.instance_applications, []), name) }
        content {
          name          = app_instance.key
          display_order = app_instance.value
        }
      }

      dynamic "user" {
        for_each = team.value.user == "" ? [] : [team.value.user]
        content {
          user_id = user.value
        }
      }
    }
  }
}

resource "crucible_player_virtual_machine" "vm" {
  for_each = var.vms
  name     = each.key
  team_ids = [for t in each.value.teams : local.team_ids[t]]
  proxmox_vm_info {
    id   = each.value.id
    node = each.value.node
    type = each.value.type
  }
  embeddable = true
}
