# Core Services

GraySpace deploys two categories of core internet services alongside the router infrastructure: DNS servers and Certificate Authorities. These run as Alpine LXC containers hosted on routers throughout the topology.

## DNS Infrastructure

The DNS infrastructure models a realistic delegation hierarchy with 18 servers across 4 roles, distributed globally.

### Architecture

```
Root Servers (root-a, root-k, root-m)
  └── TLD Servers (dns-gtld, dns-gov, dns-ru, dns-cn, dns-apnic, dns-lacnic)
        └── Hosting Providers (dns-aws, dns-namecheap, dns-ovh, dns-cloudflare, dns-alibaba)

Resolvers (dns-google, dns-quad9, dns-krnic, dns-yandex)
```

### Roles

| Role | Count | Purpose |
|------|-------|---------|
| `root-dns` | 3 | Authoritative for `.` zone, delegates TLDs |
| `tld` | 6 | Authoritative for top-level domains (com, net, org, gov, ru, etc.) |
| `hosting` | 5 | Host zone files for second-level domains (like Cloudflare, AWS Route53) |
| `resolver` | 4 | Recursive resolvers that walk the delegation chain (like Google DNS, Quad9) |

### How It Works

All DNS servers run BIND on Alpine Linux. The delegation chain works like the real internet:

1. **Root servers** contain NS records pointing TLDs to the appropriate TLD server.
2. **TLD servers** contain stub zones with NS records delegating second-level domains to hosting providers.
3. **Hosting providers** start with empty zone directories. Domains are created dynamically through the GraySpace web app or directly via SSH.
4. **Resolvers** use root hints to recursively resolve queries through the full chain.

### Creating Domains

Domains can be created through the GraySpace web application (DNS tab → Create Domain). This:

1. Selects a hosting provider (random if not specified)
2. Adds NS delegation records to the appropriate TLD server
3. Creates a zone file on the hosting provider with SOA, NS, and optional A/MX/CNAME records
4. Reloads BIND on both servers

Domains are also manageable via the REST API at `POST /api/v1/dns/domains`.

### DNS Server Locations

DNS servers are distributed across the topology on various ISP routers. Each server has a dedicated subnet advertised via BGP, making it reachable from anywhere in the simulation. See `bases/grayspace/2026.11/hosted_by.csv` for the full mapping.

---

## Certificate Authorities

GraySpace includes 4 independent Certificate Authorities running [step-ca](https://smallstep.com/docs/step-ca/), distributed globally. These allow exercise participants to request SSL/TLS certificates for domains they create.

### CA Servers

| CA Name | Hostname | Location | ISP |
|---------|----------|----------|-----|
| GrayTrust | ca-graytrust | Ashburn, US | Comcast |
| SecureSign | ca-securesign | Copenhagen, EU | TDC |
| NetCert | ca-netcert | Tokyo, APAC | Singtel |
| CyberLock | ca-cyberlock | Frankfurt, EU | Lumen |

Each CA is fully independent with its own root certificate, intermediate CA, and signing endpoint — modeling how real-world CAs (DigiCert, Let's Encrypt, Sectigo) operate as separate trust anchors.

### Requesting Certificates

Certificates can be requested through two interfaces:

**In-simulation web UI** (accessible from within the topology):

- Browse to the CA's service IP on port 80 (e.g., `http://63.140.38.10`)
- Enter a domain name and click "Issue Certificate"
- Download the `.crt` and `.key` files

**GraySpace management web app** (CAs tab):

- Click a CA card to open the detail modal
- Enter a domain name in the "Issue Certificate" form
- Download the certificate and key from the result

**REST API**:

```
POST /api/v1/ca/certificates/issue
{
  "domain": "example.com",
  "ca_hostname": "ca-graytrust"  // optional, random if omitted
}
```

### Certificate Properties

- Issued without domain validation (no DNS-01 challenge in current implementation)
- Default duration: 24 hours
- Format: PEM (ECDSA P-256)
- Private keys are returned at issuance time only and never stored server-side

### Revoking Certificates

Certificates can be revoked through the GraySpace web app (click the "Revoke" button in the CA detail modal) or via the API:

```
POST /api/v1/ca/{hostname}/certificates/revoke
{
  "serial": "certificate-serial-number"
}
```

---

## Desktops

A desktop in the simulated internet is an enclave host made from the GraySpace desktop image
(`grayspace-desktop`, built by `packer/desktop-lxc/`): an Ubuntu 24.04 container with a Fluxbox
desktop reachable in the browser over noVNC. `examples/slices/residential-customer.yaml` is a
household with one, behind a home router on Comcast.

### What's Installed

- Fluxbox, with desktop icons (pcmanfm) and a panel (tint2) for the applications below
- Firefox (Mozilla's package), set to use the simulated DNS and to trust the GraySpace CAs
- A terminal (LXTerminal), a text editor (Mousepad) and a file manager (PCManFM)
- Admin and network tools: ping, traceroute, mtr, dig, curl, nc, tcpdump, nmap, ssh, vim, tmux and more
- x11vnc and websockify for the browser's VNC session (no password)

The desktop runs as the user `user`, who has passwordless sudo.

### Accessing Desktops

Desktop hosts have a **Desktop** button next to Console in the web app's Enclaves page. It opens an
in-browser noVNC session connected to the host's VNC server.

Base versions up to `grayspace/2026.10` had a desktop client (`lum-fluxbox`); base clients were
retired with that version.
