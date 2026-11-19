# One download per image, with the Proxmox provider mocked:
#   cd terraform/images && terraform test -var-file=../../tests/golden/slices/images.tfvars.json

mock_provider "proxmox" {}

variables {
  proxmox_node      = "pve"
  proxmox_host_addr = "pve.invalid"
  proxmox_filestore = "local"
}

run "plan" {
  command = plan

  assert {
    condition     = length(proxmox_download_file.image) == length(var.downloads) && length(proxmox_virtual_environment_file.image) == length(var.uploads) && length(proxmox_oci_image.image) == length(var.oci_images)
    error_message = "One download per image"
  }

  assert {
    condition     = alltrue([for k, v in proxmox_download_file.image : v.file_name == var.downloads[k].file_name && v.datastore_id == "local"])
    error_message = "Downloads land on the file store under their rendered names"
  }
}

run "uploads" {
  command = plan

  variables {
    uploads = {
      grayspace-desktop = { url = "http://images.invalid/grayspace-desktop-24.04-1.tar.zst", content_type = "vztmpl", file_name = "gs-img-up-grayspace-desktop.tar.zst" }
    }
  }

  assert {
    condition     = proxmox_virtual_environment_file.image["grayspace-desktop"].source_file[0].file_name == "gs-img-up-grayspace-desktop.tar.zst" && proxmox_virtual_environment_file.image["grayspace-desktop"].datastore_id == "local"
    error_message = "Uploads land on the file store under their rendered names"
  }
}
