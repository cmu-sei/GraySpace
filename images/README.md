# Slice image catalog

The images slice hosts can use. It is separate from
the base versions under `bases/`, which keep their own `os_images.csv` for the core services.

`images.csv` holds the shipped images. Sites add their own, such as Windows templates built with
`packer/windows/`, through `POST /api/v1/images`; those are stored in the API's data directory.

| Column | Meaning |
|---|---|
| `name` | What a slice's `image:` names |
| `type` | `lxc`, `oci` or `vm` |
| `os_family` | `alpine`, `ubuntu`, `debian`, `windows`, or `oci` for application containers. Roles declare the families they support |
| `source` | A download URL (LXC template or VM disk image), an OCI reference, `template:<vmid>` for a Proxmox VM template to clone, or `volume:<storage>:<path>` for a template already on the node |
| `cores`, `memory`, `disk` | The default size (MiB, GiB) of a host made from the image |
| `features` | `;`-separated. `tls-kestrel`: an ASP.NET Core image that serves a slice certificate through Kestrel |
| `description` | Free text |
| `entrypoint` | OCI images: the image's command, which GraySpace wraps to install a certificate before starting it |

`grayspace-alpine` is the GraySpace Alpine image (`packer/alpine-lxc/`, `task image:build`): the
image of the base's routers, core DNS and CAs, which read its URL from this file, and of every
slice edge, which uses the base's copy rather than downloading it. `TF_VAR_alpine_image_url`
overrides the URL, and `TF_VAR_alpine_image_transfer` whether the node downloads it or Terraform
uploads it (`terraform/routers.tf`). Its file name carries the
build (`grayspace-alpine-3.24-1.tar.zst`): publish each build under a new name. GraySpace's images
are assets of the repository's GitHub releases (`images-<date>`, e.g. `images-2026.11`).

Each image a live instance uses is downloaded once (`terraform/images/`), however many instances
and hosts use it.
