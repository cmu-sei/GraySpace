"""terraform validate on the base, slice, images and player root modules, and terraform test (mocked
provider) of the base's GraySpace images, of the slice and images modules against the golden rendered
examples, of the Crucible module's Player view registrations, and of the Player workspace's view."""

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "golden" / "slices"


def terraform(module, *args):
    return subprocess.run(["terraform", *args], cwd=ROOT / "terraform" / module, capture_output=True, text=True, timeout=600)


@unittest.skipUnless(shutil.which("terraform"), "terraform is not installed")
class Modules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for module in ("", "slice", "images", "player", "modules/crucible"):
            result = terraform(module, "init", "-input=false", "-backend=false", "-no-color")
            if result.returncode != 0:
                raise unittest.SkipTest(f"terraform init failed in terraform/{module} (no network or provider cache?): {result.stderr[-500:]}")

    def test_validate(self):
        for module in ("", "slice", "images", "player"):
            with self.subTest(module=module or "base"):
                result = terraform(module, "validate", "-no-color")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_fmt(self):
        result = subprocess.run(["terraform", "fmt", "-check", "-recursive", "-diff"], cwd=ROOT / "terraform", capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_slice_module_plans_every_example(self):
        examples = sorted(GOLDEN.glob("*.tfvars.json"))
        self.assertTrue(examples)
        for path in examples:
            module = "images" if path.name == "images.tfvars.json" else "slice"
            with self.subTest(example=path.name):
                result = terraform(module, "test", "-no-color", f"-var-file={path}")
                self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-2000:])

    def test_base_images(self):
        result = terraform("", "test", "-no-color", f"-var-file={ROOT / 'tests' / 'golden' / 'tfvars-tier1.json'}")
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-2000:])

    def test_player_workspace(self):
        result = terraform("player", "test", "-no-color")
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-2000:])

    def test_crucible_module_registrations(self):
        result = terraform("modules/crucible", "test", "-no-color")
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
