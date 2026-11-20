################################################################################
#
# Slice images
#
# One download per image across all slice instances. Jobs apply this module (one local state,
# terraform.tfstate here) with the union of the images live instances use before applying an
# instance, and again after destroying one, so unused downloads are removed. Images whose source
# is a Proxmox template (template:<vmid>, volume:<id>) need no download and don't appear here.
#
# Downloads are fetched by the node, through the cluster's proxy. Uploads are GraySpace's own images
# (packer/) when alpine_image_transfer is "upload" (terraform/routers.tf), for a server the node may
# not reach: the machine running Terraform fetches them directly and uploads them over the API.
#
################################################################################

terraform {
  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~>0.114.0"
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

variable "proxmox_node" {
  type = string
}
variable "proxmox_host_addr" {
  type = string
}
variable "proxmox_filestore" {
  type = string
}

variable "downloads" {
  description = "Images downloaded by URL: LXC templates (vztmpl) and VM disk images (import)"
  type = map(object({
    url          = string
    content_type = string
    file_name    = string
  }))
  default = {}
}

variable "uploads" {
  description = "Images fetched by the machine running Terraform and uploaded to the node"
  type = map(object({
    url          = string
    content_type = string
    file_name    = string
  }))
  default = {}
}

variable "oci_images" {
  description = "OCI images pulled by reference"
  type = map(object({
    reference = string
    file_name = string
  }))
  default = {}
}

resource "proxmox_download_file" "image" {
  for_each            = var.downloads
  node_name           = var.proxmox_node
  datastore_id        = var.proxmox_filestore
  content_type        = each.value.content_type
  url                 = each.value.url
  file_name           = each.value.file_name
  overwrite_unmanaged = true
}

resource "proxmox_virtual_environment_file" "image" {
  for_each     = var.uploads
  node_name    = var.proxmox_node
  datastore_id = var.proxmox_filestore
  content_type = each.value.content_type
  source_file {
    path      = each.value.url
    file_name = each.value.file_name
  }
}

resource "proxmox_oci_image" "image" {
  for_each            = var.oci_images
  node_name           = var.proxmox_node
  datastore_id        = var.proxmox_filestore
  reference           = each.value.reference
  file_name           = each.value.file_name
  overwrite_unmanaged = true
}

output "file_ids" {
  description = "Volume ID of each image, as the slice module references it"
  value = merge(
    { for k, v in proxmox_download_file.image : k => v.id },
    { for k, v in proxmox_virtual_environment_file.image : k => v.id },
    { for k, v in proxmox_oci_image.image : k => v.id },
  )
}
