"""Golden-output test for tfvars generation.

The fixtures in tests/golden/ were produced by the Neo4j-backed parser before Neo4j was removed.
The parser must reproduce them byte for byte at both scopes, for the CSVs they were generated from.
Run with: python -m unittest tests.test_golden_tfvars
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "golden"
CSV_DIR = ROOT / "bases" / "grayspace" / "2026.11"
sys.path.insert(0, str(ROOT / "scripts"))

import topology

# The generator under test, pinned to the base version the fixtures were generated from.
GENERATOR = [sys.executable, str(ROOT / "scripts" / "generate_tfvars.py"), "--base=grayspace/2026.11"]


class GoldenTfvars(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = json.loads((GOLDEN / "source.json").read_text())
        if topology.csv_sha256(CSV_DIR) != source["csv_sha256"]:
            raise unittest.SkipTest(
                "the CSVs changed since the fixtures were generated; regenerate them "
                "(see tests/golden/README.md)"
            )

    def check(self, scope):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "tfvars.json"
            result = subprocess.run(
                [*GENERATOR, f"--scope={scope}", f"--output-file={out}"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                self.fail(f"generator failed:\n{result.stdout}{result.stderr}")
            self.assertTrue(
                out.read_bytes() == (GOLDEN / f"tfvars-{scope}.json").read_bytes(),
                f"{scope} output differs from tests/golden/tfvars-{scope}.json",
            )

    def test_full(self):
        self.check("full")

    def test_tier1(self):
        self.check("tier1")


if __name__ == "__main__":
    unittest.main()
