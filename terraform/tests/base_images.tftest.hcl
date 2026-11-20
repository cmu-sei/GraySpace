# The base's GraySpace images, with every provider mocked (tests/test_terraform.py runs it with the
# golden tier-1 tfvars):
#   cd terraform && terraform init -backend=false && terraform test -var-file=../tests/golden/tfvars-tier1.json

mock_provider "proxmox" {}
mock_provider "tls" {}
mock_provider "local" {}
mock_provider "null" {}
mock_provider "random" {}
mock_provider "crucible" {}

variables {
  proxmox_node        = "pve"
  proxmox_host_addr   = "10.0.0.2"
  proxmox_blockstore  = "local-lvm"
  proxmox_filestore   = "local"
  proxmox_mgmt_bridge = "vmbr0"
  proxmox_vm_bridge   = "vmbr1"
  environment_id      = "abc"
  router_password     = "x"
}

# Prometheus is made from the GraySpace Ubuntu LXC image, fetched as the Alpine image is
run "download_by_default" {
  command = plan
  assert {
    condition     = length(proxmox_download_file.ubuntu_lxc_image) == 1 && length(proxmox_virtual_environment_file.ubuntu_lxc_image) == 0
    error_message = "Downloaded by the node by default"
  }
  assert {
    condition     = proxmox_download_file.ubuntu_lxc_image[0].file_name == "abc-base-grayspace-ubuntu-lxc-24.04-1.tar.zst"
    error_message = "Named after the URL's file name"
  }
  assert {
    condition     = length(proxmox_download_file.alpine_image) == 1 && length(proxmox_virtual_environment_file.alpine_image) == 0
    error_message = "The Alpine image is downloaded by default too"
  }
}

run "upload_mode" {
  command = plan
  variables {
    alpine_image_transfer = "upload"
  }
  assert {
    condition     = length(proxmox_virtual_environment_file.ubuntu_lxc_image) == 1 && proxmox_virtual_environment_file.ubuntu_lxc_image[0].source_file[0].file_name == "abc-base-up-grayspace-ubuntu-lxc-24.04-1.tar.zst"
    error_message = "Uploaded by the Terraform host, under the upload name"
  }
}
