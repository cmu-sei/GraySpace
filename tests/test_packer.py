"""packer validate on the Windows slice template (packer/windows/) and the GraySpace Alpine,
desktop and Ubuntu LXC and VM images (packer/alpine-lxc/, packer/desktop-lxc/, packer/ubuntu-lxc/,
packer/ubuntu-vm/)."""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WINDOWS = ROOT / "packer" / "windows"
ALPINE = ROOT / "packer" / "alpine-lxc"
DESKTOP = ROOT / "packer" / "desktop-lxc"
UBUNTU_LXC = ROOT / "packer" / "ubuntu-lxc"
UBUNTU_VM = ROOT / "packer" / "ubuntu-vm"


def packer_init(test: unittest.TestCase, directory: Path):
    init = subprocess.run(["packer", "init", "."], cwd=directory, capture_output=True, text=True, timeout=600)
    if init.returncode != 0:
        test.skipTest(f"packer init failed (no network?): {init.stderr[-300:]}")


def catalog_url(name: str) -> str:
    rows = [line.split(",") for line in (ROOT / "images" / "images.csv").read_text().splitlines()]
    return next(row[3] for row in rows if row[0] == name)


def variable_default(template: str, name: str) -> str:
    match = re.search(r'variable "%s" \{[^}]*default\s*=\s*"([^"]+)"' % name, template)
    assert match, name
    return match.group(1)


@unittest.skipUnless(shutil.which("packer"), "packer is not installed")
class WindowsTemplate(unittest.TestCase):
    def test_validate(self):
        packer_init(self, WINDOWS)
        with tempfile.NamedTemporaryFile("w", suffix=".pkrvars.hcl") as f:
            f.write((WINDOWS / "windows.auto.pkrvars.hcl.example").read_text())
            f.flush()
            result = subprocess.run(["packer", "validate", f"-var-file={f.name}", "."], cwd=WINDOWS, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_fmt(self):
        result = subprocess.run(["packer", "fmt", "-check", "."], cwd=WINDOWS, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)


class AlpineImage(unittest.TestCase):
    @unittest.skipUnless(shutil.which("packer"), "packer is not installed")
    def test_validate(self):
        packer_init(self, ALPINE)
        result = subprocess.run(["packer", "validate", "."], cwd=ALPINE, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("packer"), "packer is not installed")
    def test_fmt(self):
        result = subprocess.run(["packer", "fmt", "-check", "."], cwd=ALPINE, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_the_exporters_are_the_plays_versions(self):
        """The router play skips an exporter the image has, so the image must bake in its version."""
        versions = yaml.safe_load((ROOT / "ansible" / "versions.yml").read_text())
        template = (ALPINE / "alpine-lxc.pkr.hcl").read_text()
        for name in ("frr_exporter_version", "node_exporter_version"):
            self.assertEqual(variable_default(template, name), str(versions[name]), name)

    def test_the_catalog_url_names_the_build(self):
        """The base and slices name the file after the URL, so it must carry the image version."""
        version = variable_default((ALPINE / "alpine-lxc.pkr.hcl").read_text(), "image_version")
        url = catalog_url("grayspace-alpine")
        self.assertTrue(url.endswith(f"/grayspace-alpine-{version}.tar.zst"), url)


class DesktopImage(unittest.TestCase):
    @unittest.skipUnless(shutil.which("packer"), "packer is not installed")
    def test_validate(self):
        packer_init(self, DESKTOP)
        result = subprocess.run(["packer", "validate", "."], cwd=DESKTOP, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("packer"), "packer is not installed")
    def test_fmt(self):
        result = subprocess.run(["packer", "fmt", "-check", "."], cwd=DESKTOP, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_the_catalog_url_names_the_build(self):
        """Enclaves name the file after the image, so a new build needs a new URL to replace it."""
        version = variable_default((DESKTOP / "desktop-lxc.pkr.hcl").read_text(), "image_version")
        url = catalog_url("grayspace-desktop")
        self.assertTrue(url.endswith(f"/grayspace-desktop-{version}.tar.zst"), url)

    def test_the_launchers_are_installed(self):
        """The panel and the desktop icons name the same applications, which the build installs."""
        tint2 = (DESKTOP / "files" / "etc" / "skel" / ".config" / "tint2" / "tint2rc").read_text()
        provision = (DESKTOP / "scripts" / "provision.sh").read_text()
        icons = [line.split()[0] for line in re.search(r"<<'EOF'\n(.*?)\nEOF", provision, re.S).group(1).splitlines()]
        launchers = re.findall(r"launcher_item_app = /usr/share/applications/(\S+)\.desktop", tint2)
        self.assertEqual(launchers, icons)


def role_packages(image_type: str) -> dict:
    """gs_packages of each host role's installs (grayspace-host's packages.yml), by role, for the roles
    that run on an image type (a role's image_types, every type when it has none)."""
    out = {}
    for tasks in sorted((ROOT / "ansible" / "roles").glob("*/tasks/*.yml")):
        role = tasks.parts[-3]
        meta = ROOT / "ansible" / "roles" / role / "meta" / "grayspace.yml"
        types = (yaml.safe_load(meta.read_text()) or {}).get("image_types") if meta.exists() else None
        if types and image_type not in types:
            continue
        for task in yaml.safe_load(tasks.read_text()) or []:
            if (task.get("ansible.builtin.include_role") or {}).get("tasks_from") == "packages":
                out.setdefault(role, []).extend(task["vars"]["gs_packages"])
    return out


def provisioned_packages(script: Path) -> set:
    """Every package an apt-get install line of a provision script names."""
    installed = set()
    for args in re.findall(r"apt-get install ((?:[^\n]*\\\n)*[^\n]*)", script.read_text()):
        installed |= {w for w in args.replace("\\\n", " ").split() if not w.startswith("-")}
    return installed


class UbuntuLxcImage(unittest.TestCase):
    @unittest.skipUnless(shutil.which("packer"), "packer is not installed")
    def test_validate(self):
        packer_init(self, UBUNTU_LXC)
        result = subprocess.run(["packer", "validate", "."], cwd=UBUNTU_LXC, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("packer"), "packer is not installed")
    def test_fmt(self):
        result = subprocess.run(["packer", "fmt", "-check", "."], cwd=UBUNTU_LXC, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_the_catalog_url_names_the_build(self):
        """Enclaves name the file after the image, so a new build needs a new URL to replace it."""
        version = variable_default((UBUNTU_LXC / "ubuntu-lxc.pkr.hcl").read_text(), "image_version")
        url = catalog_url("grayspace-ubuntu-lxc")
        self.assertTrue(url.endswith(f"/grayspace-ubuntu-lxc-{version}.tar.zst"), url)

    def test_the_image_has_the_roles_packages(self):
        """A role skips its install (and the package index download) only when the image has all of it."""
        installed = provisioned_packages(UBUNTU_LXC / "scripts" / "provision.sh")
        packages = role_packages("lxc")
        self.assertIn("dns", packages)
        self.assertNotIn("ad-dc", packages)  # VMs only
        for role, names in packages.items():
            self.assertEqual(sorted(set(names) - installed), [], role)


class UbuntuVmImage(unittest.TestCase):
    @unittest.skipUnless(shutil.which("packer"), "packer is not installed")
    def test_validate(self):
        packer_init(self, UBUNTU_VM)
        result = subprocess.run(["packer", "validate", "."], cwd=UBUNTU_VM, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("packer"), "packer is not installed")
    def test_fmt(self):
        result = subprocess.run(["packer", "fmt", "-check", "."], cwd=UBUNTU_VM, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_the_catalog_url_names_the_build(self):
        """Enclaves name the file after the image, so a new build needs a new URL to replace it."""
        version = variable_default((UBUNTU_VM / "ubuntu-vm.pkr.hcl").read_text(), "image_version")
        url = catalog_url("grayspace-ubuntu-vm")
        self.assertTrue(url.endswith(f"/grayspace-ubuntu-vm-{version}.qcow2"), url)

    def test_the_image_has_the_roles_packages(self):
        """A role skips its install (and the package index download) only when the image has all of it."""
        installed = provisioned_packages(UBUNTU_VM / "scripts" / "provision.sh")
        packages = role_packages("vm")
        self.assertIn("ad-dc", packages)
        for role, names in packages.items():
            self.assertEqual(sorted(set(names) - installed), [], role)


if __name__ == "__main__":
    unittest.main()
