# Windows slice template

A Proxmox VM template for Windows slice hosts. GraySpace ships
no Windows images; each site builds its own from its licensed or evaluation media.

The template has:

- the VirtIO drivers and the QEMU guest agent (Proxmox and Terraform read its addresses);
- OpenSSH Server with PowerShell as its shell, for Ansible (`ansible_shell_type=powershell`);
- cloudbase-init reading the config drive Proxmox attaches to clones: the hostname, the simulated
  static address, gateway and resolver, and the Administrator's password and SSH key.

## Build

On the Proxmox node, upload a Windows Server 2022 ISO and the
[VirtIO drivers ISO](https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso)
to the ISO storage. Packer's `cd_content` needs `xorriso` (or `mkisofs`) where Packer runs.

```bash
cd packer/windows
cp windows.auto.pkrvars.hcl.example windows.auto.pkrvars.hcl   # Proxmox URL, token, ISOs, bridge
packer init .
packer build .
```

The build takes 30 to 60 minutes and leaves a template with VM ID `vm_id` (9000 by default).

## Register it

```bash
curl -X POST -H "Authorization: Bearer $GRAYSPACE_API_TOKEN" -H "Content-Type: application/json" \
  http://<grayspace>/api/v1/images \
  -d '{"name": "windows-2022", "type": "vm", "os_family": "windows", "source": "template:9000",
       "cores": 2, "memory": 4096, "disk": 40, "description": "Windows Server 2022 (packer/windows)"}'
```

Slices can then use `image: windows-2022`, with roles that support Windows (`ghosts-client`), as in
`examples/slices/windows-office.yaml`. Instances clone the template; nothing is downloaded.
