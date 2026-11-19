"""Rendering allocations for Terraform and Ansible (services/slices/render.py).

The golden files in tests/golden/slices/ are each example rendered against the golden full-scope
tfvars. Like the base golden test, regenerate them only for intentional output changes:

    GOLDEN_REGENERATE=1 python -m unittest tests.test_slice_render
"""

import base64
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from models.capabilities import Image
from models.slices import SliceSpec
from services.slices import base, catalog, plan, render
from tests import REPO

GOLDEN = REPO / "tests" / "golden" / "slices"
TFVARS = json.loads((REPO / "tests" / "golden" / "tfvars-full.json").read_text())
CTX = base.from_tfvars(TFVARS)
ENV = render.RenderEnv(environment_id="abc", filestore="local")
WINDOWS = Image(name="windows-2022", type="vm", os_family="windows", source="template:9000", cores=2, memory=4096, disk=40, origin="local")
# A site's own VM image by URL: every shipped Linux image is GraySpace's (uploaded by default), so
# a local one is what the node downloads
SITE_VM = Image(name="site-vm", type="vm", os_family="ubuntu", source="https://images.example/site-vm.img", cores=2, memory=2048, disk=16, origin="local")
IMAGES = {**catalog.images(), "windows-2022": WINDOWS}


def allocate(example):
    spec = SliceSpec.model_validate(yaml.safe_load((REPO / "examples" / "slices" / f"{example}.yaml").read_text()))
    alloc, findings = plan.resolve(spec, CTX, IMAGES, catalog.roles(), plan.Taken())
    assert alloc is not None, findings
    return spec, alloc


def rendered(example):
    spec, alloc = allocate(example)
    mgmt = {h: f"10.0.9.{i + 10}" for i, h in enumerate(alloc.hostnames())}
    neighbors = {s.edge: [{"ip": w.gateway, "asn": CTX.routers[w.router].asn, "router": w.router} for w in s.wans] for s in alloc.sites if s.routed}
    certs = {c: {"cert": f"/data/instances/{alloc.prefix}/{c}.crt", "key": f"/data/instances/{alloc.prefix}/{c}.key"}
             for c in (h.hostname for h in alloc.hosts) if any(t.host == c.split("-", 1)[1] for t in spec.tls)}
    return {
        f"{example}.tfvars.json": render.dumps(render.instance_tfvars(alloc, IMAGES, ENV)),
        f"{example}.vars.json": render.dumps(render.ansible_vars(alloc, spec.role_vars, [d.domain for d in spec.dns.domains],
                                                                  f"/data/instances/{alloc.prefix}/ca-bundle.pem", certs, neighbors)),
        f"{example}.ini": render.inventory(alloc, mgmt, "/repo/.ssh/terraform_key"),
    }


EXAMPLES = sorted(p.stem for p in (REPO / "examples" / "slices").glob("*.yaml"))


class Golden(unittest.TestCase):
    def test_examples_render_as_recorded(self):
        regenerate = os.environ.get("GOLDEN_REGENERATE")
        if regenerate:
            GOLDEN.mkdir(parents=True, exist_ok=True)
        for example in EXAMPLES:
            for name, text in rendered(example).items():
                with self.subTest(file=name):
                    path = GOLDEN / name
                    if regenerate:
                        path.write_text(text)
                    self.assertTrue(path.exists(), f"{path} is missing; GOLDEN_REGENERATE=1 python -m unittest tests.test_slice_render")
                    self.assertEqual(path.read_text(), text, f"{name} differs from the golden file")

    def test_images_render_as_recorded(self):
        _, alloc = allocate("information-environment")
        text = render.dumps(render.images_tfvars(ENV, [IMAGES[n] for n in alloc.images]))
        path = GOLDEN / "images.tfvars.json"
        if os.environ.get("GOLDEN_REGENERATE"):
            path.write_text(text)
        self.assertEqual(path.read_text(), text)


class Instance(unittest.TestCase):
    def test_nat_edge(self):
        _, alloc = allocate("meridian-bank")
        out = render.instance_tfvars(alloc, IMAGES, ENV)
        edge = out["edges"]["mb-hq-edge"]
        self.assertEqual(edge["interfaces"][0], {"bridge": "v332", "address": "209.151.130.2/24", "gateway": "209.151.130.1"})
        self.assertEqual(edge["interfaces"][1], {"bridge": "s000", "address": "192.168.0.1/24", "gateway": None})
        # The base's copy of the GraySpace image (terraform/routers.tf), uploaded by default: no hook
        # script, and no download of the edges' own
        self.assertEqual(out["edge_template"], "local:vztmpl/abc-base-up-grayspace-alpine-3.24-1.tar.zst")
        self.assertNotIn("edge_hook_script", out)
        host = out["lxc_hosts"]["mb-web01"]
        self.assertEqual((host["bridge"], host["address"], host["gateway"], host["resolver"]), ("s001", "192.168.20.10/24", "192.168.20.1", "8.8.8.8"))
        self.assertEqual(host["template"], "local:vztmpl/abc-img-up-grayspace-ubuntu-lxc.tar.zst")
        self.assertEqual(out["vnets"], {"s000": "mb hq-lan", "s001": "mb hq-dmz", "s002": "mb br-lan"})

    def test_edges_use_the_bases_copy_of_the_image(self):
        """Named as terraform/routers.tf names it, for each TF_VAR_alpine_image_transfer and URL."""
        _, alloc = allocate("meridian-bank")

        def edge_template(**env):
            env = render.RenderEnv.from_env({"TF_VAR_environment_id": "abc", "TF_VAR_proxmox_filestore": "local", **env})
            return render.instance_tfvars(alloc, IMAGES, env)["edge_template"]

        self.assertEqual(edge_template(), "local:vztmpl/abc-base-up-grayspace-alpine-3.24-1.tar.zst")
        self.assertEqual(edge_template(TF_VAR_alpine_image_transfer="download"), "local:vztmpl/abc-base-grayspace-alpine-3.24-1.tar.zst")
        self.assertEqual(edge_template(TF_VAR_alpine_image_url="http://mirror.example/x/grayspace-alpine-3.24-2.tar.zst"),
                         "local:vztmpl/abc-base-up-grayspace-alpine-3.24-2.tar.zst")
        self.assertNotIn(catalog.EDGE_IMAGE, alloc.images)

    def test_the_transfer_default_is_the_bases(self):
        """render's default must match terraform/vars.tf's, or edges would name a file the base hasn't."""
        vars_tf = (REPO / "terraform" / "vars.tf").read_text()
        default = re.search(r'variable "alpine_image_transfer" \{[^}]*default\s*=\s*"([^"]+)"', vars_tf).group(1)
        self.assertEqual(render.RenderEnv("abc", "local").alpine_image_transfer, default)

    def test_routed_edges_have_no_static_default(self):
        _, alloc = allocate("routed-campus")
        edge = render.instance_tfvars(alloc, IMAGES, ENV)["edges"]["rc-campus-edge"]
        self.assertEqual([i["gateway"] for i in edge["interfaces"]], [None, None, None, None])
        self.assertEqual([i["bridge"] for i in edge["interfaces"]], ["v332", "v349", "s000", "s001"])

    def test_vm_hosts(self):
        _, alloc = allocate("windows-office")
        vms = render.instance_tfvars(alloc, IMAGES, ENV)["vm_hosts"]
        self.assertEqual(vms["wo-win"]["clone_vm_id"], 9000)
        self.assertIsNone(vms["wo-win"]["import_from"])
        self.assertEqual(vms["wo-dc"]["import_from"], "local:import/abc-img-up-grayspace-ubuntu-vm.qcow2")
        self.assertEqual(vms["wo-win"]["os_family"], "windows")

    def test_images_need_one_download_each(self):
        _, a = allocate("meridian-bank")
        _, b = allocate("windows-office")
        names = sorted(set(a.images) | set(b.images))
        out = render.images_tfvars(ENV, [IMAGES[n] for n in names])
        self.assertEqual(out["downloads"], {})
        # GraySpace's own images, uploaded by default; not the template-backed windows-2022, nor the edges' image
        self.assertEqual(sorted(out["uploads"]), ["grayspace-ubuntu-lxc", "grayspace-ubuntu-vm"])
        self.assertEqual(out["uploads"]["grayspace-ubuntu-vm"]["content_type"], "import")

    def test_vm_images_download_as_disk_imports(self):
        out = render.images_tfvars(ENV, [SITE_VM])
        self.assertEqual(out["downloads"]["site-vm"]["content_type"], "import")
        self.assertEqual(out["downloads"]["site-vm"]["file_name"], "abc-img-site-vm.qcow2")

    def test_grayspace_images_follow_the_bases_transfer(self):
        """Uploaded like the base's Alpine image, under a name of their own; other images download."""
        images = [IMAGES["grayspace-desktop"], SITE_VM]
        out = render.images_tfvars(ENV, images)
        self.assertEqual((sorted(out["uploads"]), sorted(out["downloads"])), (["grayspace-desktop"], ["site-vm"]))
        self.assertEqual(out["uploads"]["grayspace-desktop"]["file_name"], "abc-img-up-grayspace-desktop.tar.zst")
        self.assertEqual(render.image_volume(ENV, IMAGES["grayspace-desktop"])["volume_id"], "local:vztmpl/abc-img-up-grayspace-desktop.tar.zst")
        download = render.RenderEnv("abc", "local", alpine_image_transfer="download")
        out = render.images_tfvars(download, images)
        self.assertEqual((sorted(out["uploads"]), sorted(out["downloads"])), ([], ["grayspace-desktop", "site-vm"]))
        self.assertEqual(out["downloads"]["grayspace-desktop"]["file_name"], "abc-img-grayspace-desktop.tar.zst")
        local = IMAGES["grayspace-desktop"].model_copy(update={"origin": "local"})
        self.assertFalse(render.uploaded(ENV, local))  # a site's own image is served where its node reaches


class OciTls(unittest.TestCase):
    def test_pandora_serves_http_on_80(self):
        _, alloc = allocate("information-environment")
        news = render.instance_tfvars(alloc, IMAGES, ENV)["oci_hosts"]["ie-news"]
        self.assertEqual(news["env"]["ASPNETCORE_URLS"], "http://+:80")
        self.assertEqual(news["env"]["SITE_NAME"], "The Daily Chronicle")
        self.assertIsNone(news["entrypoint"])

    def test_certificate_through_the_entrypoint(self):
        _, alloc = allocate("information-environment")
        cert, key = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n", "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----\n"
        news = render.instance_tfvars(alloc, IMAGES, ENV, certs={"ie-news": (cert, key)})["oci_hosts"]["ie-news"]
        env = news["env"]
        self.assertEqual(env["ASPNETCORE_URLS"], "http://+:80;https://+:443")
        self.assertEqual(env["ASPNETCORE_Kestrel__Certificates__Default__Path"], render.TLS_CERT)
        self.assertEqual(news["entrypoint"], "/bin/sh -c 'eval \"$(echo \"$GRAYSPACE_INIT\" | base64 -d)\"'")
        # Run the entrypoint as the container would, with the image's command replaced by a check
        # and the file paths moved to a temporary directory.
        with tempfile.TemporaryDirectory() as d:
            script = base64.b64decode(env["GRAYSPACE_INIT"]).decode()
            self.assertTrue(script.rstrip().endswith("cd /app && exec dotnet Ghosts.Pandora.dll"))
            script = script.replace("/tmp/", f"{d}/").replace("cd /app && exec dotnet Ghosts.Pandora.dll", "echo started")
            run_env = {**env, "GRAYSPACE_INIT": base64.b64encode(script.encode()).decode(), "PATH": os.environ["PATH"]}
            entry = news["entrypoint"].split(" ", 2)
            out = subprocess.run([entry[0], entry[1], entry[2][1:-1]], env=run_env, capture_output=True, text=True)
            self.assertEqual(out.stdout.strip(), "started", out.stderr)
            self.assertEqual(Path(d, "grayspace-tls.crt").read_text(), cert)
            self.assertEqual(Path(d, "grayspace-tls.key").read_text(), key)
            self.assertEqual(oct(Path(d, "grayspace-tls.key").stat().st_mode & 0o777), "0o600")


class Inventory(unittest.TestCase):
    def test_groups_and_connections(self):
        _, alloc = allocate("windows-office")
        text = render.inventory(alloc, {h: "10.0.0.5" for h in alloc.hostnames()}, "/k")
        self.assertIn("[edges]\nwo-office-edge ansible_host=10.0.0.5 ansible_user=root", text)
        self.assertIn("wo-win ansible_host=10.0.0.5 ansible_user=Administrator ansible_shell_type=powershell", text)
        self.assertIn("wo-dc ansible_host=10.0.0.5 ansible_user=grayspace ansible_become=true", text)
        self.assertIn("[windows]\nwo-win", text)
        self.assertIn("ansible_ssh_private_key_file=/k", text)

    def test_hosts_without_a_management_address_are_left_out(self):
        _, alloc = allocate("meridian-bank")
        text = render.inventory(alloc, {"mb-hq-edge": "10.0.0.5"}, "/k")
        self.assertNotIn("mb-ws-01", text)

    def test_oci_hosts_are_not_managed(self):
        _, alloc = allocate("information-environment")
        variables = render.ansible_vars(alloc, {}, [], None, {})
        self.assertNotIn("ie-news", variables["slice_hosts"])
        self.assertIn("ie-corp", variables["slice_hosts"])

    def test_nat_and_published(self):
        _, alloc = allocate("meridian-bank")
        edge = render.ansible_vars(alloc, {}, [], None, {})["slice_edges"]["mb-hq-edge"]
        self.assertEqual(edge["published"], [{"public_ip": "209.151.130.3", "private_ip": "192.168.20.10", "hostname": "mb-web01"}])
        self.assertEqual(edge["wans"][0]["interface"], "eth0")
        self.assertEqual([s["interface"] for s in edge["segments"]], ["eth1", "eth2"])
        self.assertEqual(edge["mgmt_interface"], "eth3")


class UnsafeVars(unittest.TestCase):
    """Spec values reach Ansible as text, never as templates: role_vars come from API callers."""

    def test_yaml_round_trip(self):
        data = {"slice_role_vars": {"web": {"site_title": "{{ lookup('pipe', 'id') }}"}}, "n": 1, "l": ["a"]}
        text = render.ansible_vars_yaml(data)
        self.assertIn("!unsafe", text)

        class Loader(yaml.SafeLoader):
            pass

        Loader.add_constructor("!unsafe", lambda loader, node: loader.construct_scalar(node))
        self.assertEqual(yaml.load(text, Loader=Loader), data)

    @unittest.skipUnless(__import__("shutil").which("ansible-playbook"), "ansible is not installed")
    def test_ansible_does_not_template_spec_values(self):
        with tempfile.TemporaryDirectory() as d:
            marker = Path(d, "pwned")
            variables = {"slice_role_vars": {"web": {"site_title": f"{{{{ lookup('pipe', 'touch {marker}') }}}}"}}}
            Path(d, "vars.yml").write_text(render.ansible_vars_yaml(variables))
            Path(d, "play.yml").write_text(
                "- hosts: localhost\n  gather_facts: false\n  connection: local\n  tasks:\n"
                "    - ansible.builtin.debug:\n        msg: \"{{ slice_role_vars.web.site_title }}\"\n"
            )
            out = subprocess.run(["ansible-playbook", "-i", "localhost,", "-e", f"@{d}/vars.yml", f"{d}/play.yml"],
                                 capture_output=True, text=True, env={**os.environ, "ANSIBLE_LOG_PATH": f"{d}/a.log"})
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertFalse(marker.exists(), "Ansible ran a lookup from a spec value")
            self.assertIn("lookup('pipe'", out.stdout)


class RecordData(unittest.TestCase):
    def test_zone_file_lines_stay_lines(self):
        from pydantic import ValidationError

        from models.slices import DnsRecord

        DnsRecord(name="mx.a.com", type="MX", data="10 mail.a.com.")
        DnsRecord(name="t.a.com", type="TXT", data="v=spf1 -all")
        for bad in ({"type": "A", "data": "1.2.3.4\nevil IN A 6.6.6.6"}, {"type": "A", "data": "not-an-ip"},
                    {"type": "CNAME", "data": "x y"}, {"type": "MX", "data": "mail.a.com"}, {"type": "TXT", "data": 'a"b'}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                DnsRecord(name="w.a.com", **bad)


if __name__ == "__main__":
    unittest.main()
