# The Player workspace with a mocked provider: teams and their applications, the Admin team, and
# VMs registered in the teams named for them.

mock_provider "crucible" {
  mock_resource "crucible_player_view" {
    defaults = {
      id = "view-1"
    }
  }
}

variables {
  view         = { name = "abc-grayspace", description = "Exercise" }
  applications = [{ name = "VMs", template_id = "e4698c62-7322-445b-946d-cafe1cf510bc" }, { name = "Chat", url = "https://chat.example" }]
  teams        = [{ name = "Blue", role = "View Member", applications = ["VMs", "Chat"] }, { name = "Red", applications = ["VMs"] }]
  vms = {
    "lum-chi"  = { id = 101, node = "pve", type = "LXC", teams = ["Blue", "Red"] }
    "mb-web01" = { id = 205, node = "pve2", type = "QEMU", teams = ["Red"] }
  }
}

run "teams_applications_and_vms" {
  command = plan

  variables {
    crucible_user_id = "" # not the environment's TF_VAR_crucible_user_id
  }

  assert {
    condition     = [for a in crucible_player_view.view.application : a.name] == ["Chat", "VMs"] && crucible_player_view.view.application[0].url == "https://chat.example"
    error_message = "Both applications, by name (the provider matches the blocks by position)"
  }
  assert {
    condition     = [for t in crucible_player_view.view.team : t.name] == ["Blue", "Red"]
    error_message = "No Admin team without a service user"
  }
  assert {
    condition     = crucible_player_view.view.team[0].permissions == null
    error_message = "No permissions is null, as the provider reads it back"
  }
  assert {
    condition     = [for a in crucible_player_view.view.team[0].app_instance : "${a.name}:${a.display_order}"] == ["Chat:1", "VMs:0"]
    error_message = "Each team's applications by name, with the team's display order"
  }
  assert {
    condition     = length(crucible_player_virtual_machine.vm) == 2 && crucible_player_virtual_machine.vm["mb-web01"].proxmox_vm_info[0].type == "QEMU"
    error_message = "Every VM registered with its type"
  }
}

run "the_service_user_admins_the_view" {
  command = plan

  variables {
    crucible_user_id = "u-1"
  }

  assert {
    condition     = [for t in crucible_player_view.view.team : t.name] == ["Admin", "Blue", "Red"] && crucible_player_view.view.team[0].role == "View Admin"
    error_message = "An Admin team, with the others by name"
  }
  assert {
    condition     = crucible_player_view.view.team[0].user[0].user_id == "u-1" && length(crucible_player_view.view.team[0].app_instance) == 2
    error_message = "With the service user and every application"
  }
}

run "instances_only_of_the_applications_the_view_has" {
  command = plan

  variables {
    crucible_user_id      = "u-1"
    instance_applications = ["Chat"]
  }

  assert {
    condition     = [for a in crucible_player_view.view.application : a.name] == ["Chat", "VMs"]
    error_message = "Every application is made"
  }
  assert {
    condition     = [for t in crucible_player_view.view.team : [for a in t.app_instance : a.name]] == [["Chat"], ["Chat"], []]
    error_message = "Teams (Admin too) get instances only of the applications named, Blue's Chat keeping its order"
  }
  assert {
    condition     = crucible_player_view.view.team[1].app_instance[0].display_order == 1
    error_message = "An instance keeps its display order in the team"
  }
}

run "a_vm_type_is_lxc_or_qemu" {
  command = plan

  variables {
    vms = { "x" = { id = 1, node = "pve", type = "vm", teams = [] } }
  }

  expect_failures = [var.vms]
}
