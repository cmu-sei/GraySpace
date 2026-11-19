# Gray Space Appliance

!!! warning "Being reworked"
    The appliance no longer installs the web app on its Proxmox host (that mode had no sign-in and was
    removed). It is to run the GraySpace container instead, as any Docker host does; until it does,
    use the appliance as a Proxmox host and run GraySpace from a container
    ([Run GraySpace in a container](install.md#run-grayspace-in-a-container)).

The GraySpace Appliance is a pre-built virtual machine that contains all the packages and source code needed to deploy and use GraySpace.

The appliance can be used as a standalone simulation tool, or it can be configured to connect to external networks.

## Architecture

The GraySpace Appliance is built on Debian 13 "Trixie" with Proxmox Virtual Environment (PVE) and Xfce desktop installed.

### PVE configuration

The Proxmox node running on the appliance is pre-configured to support the GraySpace automation code.

- Network devices
    - vmbr0 - mapped to eth0
    - vmbr1 - Private bridge. Used for GraySpace backplane management. DHCP enabled via dnsmasq. Outbound NAT to vmbr0
    - vmbr2 - mapped to eth1
- Storage
    - local - Filestore. Used to store LXC templates, snippets, and other files
    - gray-lvm - lvm-thin blockstore. Used to store LXC and VM virtual disks

- Proxy
    - If operating in an environment with an HTTP/S proxy, the proxy value will need to be set in the nested PVE directly (Datacenter->Options)

## Importing the OVA

<details><summary>Resource Requirements</summary>

<strong>CPU</strong><br>
We suggest assigning at least 4 cores to the Appliance, more if you can spare it. The nested PVE API can struggle to keep up with Terraform as it creates resources, resulting in dependency locks and other errors. A way to guard against this is to set the Terraform parallelism to a lower value (2 or 3; default is 10).<br><br>

<strong>Memory</strong><br>

We recommend at least 16G of memory.<br><br>

<strong>Storage</strong><br>

The OVA contains two disks - a 100G disk for the OS and a 500G disk for block storage.<br><br>

<strong>Networking</strong><br>

The OVA expects 2 NICs: A primary interface and an optional interface

</details>

### vCenter

Right-click your host or cluster and select "Deploy OVF Template". Select Local File and upload the OVA.

Set the VM's name, host, and resources.

The OVA import wizard will ask you to assign two network interfaces:

- Managment Network (required) - This interface should be assigned to an internet connected network so that the LXCs and VMs can access the internet to pull down required packages.

- GraySpace Ingress (optional) - This interface provides connection to external networks if desired. This should be connected to the VLAN you want to connect into GraySpace, or if you want to connect multiple networks, it should be set to a VLAN trunk.

If you do not plan on connecting external networks to GraySpace, you can assign the second interface to any network and ignore it (you can delete the interface later if desired).

## Using the Appliance

After the appliance boots, log in with the credentials `root/grayspace`

PVE is available at `localhost:8006`. Credentials are `root/grayspace`

### Connecting to external networks

Establishing a connection from GraySpace to an external network requires using PVE's Software Defined Networking (SDN) stack.

After the model is running:

- From the PVE interface, click Datacenter, then navigate to SDN/VNets
- Find the VNet for the network link you want to connect externally
    - Because the network name field is limited to 8 characters, we use the "Alias" field for a full description of the network link
- Select the VNet and click "Edit"
- Change the "Zone" from the simple zone to the VLAN zone.
- A "Tag" field will appear. Set the VLAN tag for the external network to which you are connecting and click "OK"
- Click on "SDN" and click "Apply"
