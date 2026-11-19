"""Validation in scripts/topology.py: bad CSVs fail the load with every problem listed."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
BASE = ROOT / "bases" / "grayspace" / "2026.11"

import topology


class LoadValidation(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        for f in (BASE).glob("*.csv"):
            shutil.copy(f, self.dir)
        self.addCleanup(shutil.rmtree, self.dir)

    def append(self, filename, line):
        path = self.dir / filename
        text = path.read_text(encoding="utf-8-sig")
        path.write_text(text.rstrip("\n") + "\n" + line + "\n", encoding="utf-8")

    def problems(self):
        with self.assertRaises(topology.LoadError) as ctx:
            topology.load(self.dir)
        return ctx.exception.problems

    def test_current_csvs_load(self):
        topo = topology.load(BASE)
        self.assertEqual(len(topo.of("Router")), 134)

    def test_dangling_reference(self):
        self.append("located_in.csv", "lum-sfr,Atlantis,LOCATED_IN")
        self.assertEqual(self.problems(), ["located_in.csv:136: unknown Location 'Atlantis'"])

    def test_duplicate_id(self):
        self.append("isps.csv", "Lumen,9.0.0.0/16,1")
        self.assertEqual(self.problems(), ["isps.csv:17: duplicate ISP ID 'Lumen'"])

    def test_unparseable_value(self):
        self.append("isps.csv", "Acme,9.0.0.0/16,not-a-number")
        self.assertEqual(len(self.problems()), 1)
        self.assertIn("isps.csv:17: column 'asn'", self.problems()[0])

    def test_dump_round_trips(self):
        """The shipped CSVs are canonical: loading and dumping them changes no bytes."""
        out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out)
        topology.dump(topology.load(BASE), out)
        for f in sorted((BASE).glob("*.csv")):
            with self.subTest(file=f.name):
                self.assertEqual((out / f.name).read_bytes(), f.read_bytes())

    def test_dump_writes_edits_in_place(self):
        topo = topology.load(BASE)
        topo.nodes["ISP"]["Lumen"].props["asn"] = 1
        out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out)
        topology.dump(topo, out)
        before = (BASE / "isps.csv").read_text().splitlines()
        after = (out / "isps.csv").read_text().splitlines()
        self.assertEqual(after[1], "Lumen,6.0.0.0/16,1")
        self.assertEqual(after[2:], before[2:])

    def test_missing_file(self):
        (self.dir / "hosted_by.csv").unlink()
        self.assertEqual(self.problems(), [f"{self.dir}: missing hosted_by.csv"])

    def test_clients_are_retired(self):
        """A CSV directory that still has base clients names them rather than ignoring them."""
        (self.dir / "clients.csv").write_text("hostname:ID(Client),image:string\n")
        problems = self.problems()
        self.assertEqual(len(problems), 1)
        self.assertIn("clients.csv is no longer read", problems[0])

    def test_reports_every_problem(self):
        self.append("located_in.csv", "lum-sfr,Atlantis,LOCATED_IN")
        self.append("isps.csv", "Lumen,9.0.0.0/16,1")
        self.assertEqual(len(self.problems()), 2)


if __name__ == "__main__":
    unittest.main()
