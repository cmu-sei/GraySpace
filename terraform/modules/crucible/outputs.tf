# Slice instances register their guests in this team too (terraform/slice/crucible.tf), so the base
# publishes it (output "crucible" in terraform/main.tf).
output "view_id" {
  value = crucible_player_view.grayspace.id
}

output "team_id" {
  value = crucible_player_view.grayspace.team[0].team_id
}
