"""The file store for slices, instances and jobs (services/slices/store.py)."""

import copy
import threading
import unittest

from models.instances import InstanceRecord, SliceRef
from models.slices import SliceSpec
from services.slices import store
from tests.isolation import isolate
from tests.test_slice_plan import MINIMAL


def record(instance_id="tb", state="pending"):
    return InstanceRecord(id=instance_id, slice=SliceRef(slug="test-bank", version="sha256:" + "a" * 64), state=state, created="t", updated="t")


class Slices(unittest.TestCase):
    def setUp(self):
        isolate(self)

    def test_versions(self):
        spec = SliceSpec.model_validate(MINIMAL)
        v1, created = store.put_slice(spec)
        self.assertTrue(created)
        self.assertEqual(store.put_slice(spec), (v1, False))
        changed = SliceSpec.model_validate({**copy.deepcopy(MINIMAL), "description": "v2"})
        v2, _ = store.put_slice(changed)
        self.assertNotEqual(v1, v2)
        self.assertEqual(store.get_slice("test-bank")[0], v2)
        self.assertEqual(store.get_slice("test-bank", v1)[1], spec)
        # Storing v1 again makes it the latest
        store.put_slice(spec)
        summary = store.list_slices()[0]
        self.assertEqual(summary.latest, v1)
        self.assertEqual([v.version for v in summary.versions], [v2, v1])
        self.assertIsNone(store.get_slice("test-bank", "sha256:" + "f" * 64))
        self.assertIsNone(store.get_slice("nope"))

    def test_names_are_checked(self):
        self.assertIsNone(store.get_slice("../etc"))
        store.put_slice(SliceSpec.model_validate(MINIMAL))
        self.assertIsNone(store.get_slice("test-bank", "sha256:../../x"))  # not in the index, never read
        self.assertIsNone(store.get_instance("../x"))

    def test_delete(self):
        store.put_slice(SliceSpec.model_validate(MINIMAL))
        self.assertTrue(store.delete_slice("test-bank"))
        self.assertFalse(store.delete_slice("test-bank"))
        self.assertEqual(store.list_slices(), [])


class Instances(unittest.TestCase):
    def setUp(self):
        self.data = isolate(self)

    def test_claim_is_exclusive(self):
        self.assertTrue(store.claim_instance(record()))
        self.assertFalse(store.claim_instance(record()))

    def test_destroyed_ids_are_reused_and_archived(self):
        store.claim_instance(record())
        with store.update_instance("tb") as r:
            r.state = "destroyed"
        self.assertTrue(store.claim_instance(record()))
        self.assertEqual(len(list((self.data / "instances" / "archive").glob("tb-*.json"))), 1)
        self.assertEqual(store.get_instance("tb").state, "pending")

    def test_live(self):
        store.claim_instance(record("a"))
        store.claim_instance(record("b"))
        with store.update_instance("b") as r:
            r.state = "destroyed"
        self.assertEqual([i.id for i in store.live_instances()], ["a"])

    def test_lock_is_reentrant_and_serializes_threads(self):
        store.claim_instance(record())
        with store.locked():
            with store.update_instance("tb") as r:  # locks again inside
                r.jobs.append("j1")
        results = []

        def add(n):
            for i in range(20):
                with store.update_instance("tb") as r:
                    r.jobs.append(f"{n}-{i}")
            results.append(n)

        threads = [threading.Thread(target=add, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(store.get_instance("tb").jobs), 81)

    def test_writes_are_atomic_files(self):
        store.claim_instance(record())
        store.save_instance(store.get_instance("tb"))
        leftovers = [p for p in (self.data / "instances").iterdir() if p.name.startswith(".")]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
