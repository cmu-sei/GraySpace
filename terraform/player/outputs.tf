output "view_id" {
  value = crucible_player_view.view.id
}

output "team_ids" {
  description = "Team name => Player team ID"
  value       = local.team_ids
}
