"""Jobs end to end (services/slices/runner.py, stages.py): create → ready → destroy, failures and
resume, cancel, the node lock and plans, with fake terraform and ansible-playbook (fake_tools.py),
the real DNS code against fake hosts, and fake SSH and CA."""

import asyncio
import datetime
import fcntl
import json
import os
import threading
import unittest
from unittest.mock import AsyncMock, patch

import yaml

from config import settings
from models.instances import InstanceRecord, SliceRef
from models.slices import SliceSpec
from services import dns_service as dns_module
from services.mutation_tracker import mutation_tracker
from services.slices import environment, plan, runner, service, stages, store
from tests import REPO
from tests.fake_hosts import FakeHosts
from tests.fake_tools import FakeCheckout
from tests.isolation import isolate
from tests.test_dns_delegations import tld_zone

TFVARS = json.loads((REPO / "tests" / "golden" / "tfvars-full.json").read_text())


def self_signed(name):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(4242).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1)).sign(key, hashes.SHA256()))
    return (cert.public_bytes(serialization.Encoding.PEM).decode(),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode())


class FakeCa:
    def __init__(self):
        self.issued, self.revoked = [], []

    async def issue_certificate(self, domain, ca_hostname=None, sans=None, not_after=None):
        self.issued.append((domain, ca_hostname, sans, not_after))
        cert, key = self_signed(domain)
        return {"success": True, "certificate": cert, "private_key": key, "ca_hostname": ca_hostname}

    async def get_ca_bundle(self):
        return {"ca-graytrust": self_signed("GrayTrust Root")[0], "ca-netcert": None}

    async def revoke_certificate(self, hostname, serial, reason=None):
        self.revoked.append((hostname, serial))
        return {"success": True, "message": f"Certificate {serial} revoked on {hostname}"}


class Jobs(unittest.TestCase):
    def setUp(self):
        self.data = isolate(self)
        self.checkout = FakeCheckout()
        self.addCleanup(self.checkout.cleanup)
        self.hosts = FakeHosts()
        self.addCleanup(self.hosts.cleanup)
        self.hosts.write("dns-gtld", "/var/bind/db.com", tld_zone("com", "216.239.34.10"))
        self.ca = FakeCa()
        self.ssh_calls = []
        self.ssh_ok = True

        async def ssh(ip, command, timeout=30):
            self.ssh_calls.append((ip, command))
            return (0, "200") if self.ssh_ok else (1, "Network unreachable")

        tfvars = {**TFVARS, "base": {"name": "grayspace/2026.11", "csv_sha256": "x" * 64, "scope": "full"}}
        patches = [
            patch.object(settings, "codebase_path", str(self.checkout.root)),
            patch.object(settings, "environment_file", None),
            patch.object(settings, "slice_ssh_timeout", 5),
            patch.dict(os.environ, {"PATH": self.checkout.path, "FAKE_ROOT": str(self.checkout.root),
                                    "GRAYSPACE_LOCK_FILE": str(self.checkout.root / "terraform" / ".grayspace.lock")}),
            patch.object(service, "tfvars", return_value=tfvars),
            patch.object(dns_module.topology_service, "tfvars_data", tfvars),
            patch.object(stages, "ssh", side_effect=ssh),
            patch.object(stages, "_port_open", AsyncMock(return_value=True)),
            patch.object(stages, "base_mgmt_ip", AsyncMock(return_value="10.0.8.1")),
            patch.object(stages, "statuses", AsyncMock(return_value={})),
            patch("services.ca_service.ca_service", self.ca),
            patch("services.capabilities.capacity", AsyncMock(return_value=None)),
            patch.object(mutation_tracker, "save"),
        ]
        for name, fake in (("_run_command", self.hosts.run), ("_read_file", self.hosts.read_file), ("_write_file", self.hosts.write_file)):
            patches.append(patch.object(dns_module.dns_service, name, side_effect=fake))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        stages.environment._load_env.cache_clear()

    def put(self, example, edit=None):
        raw = yaml.safe_load((REPO / "examples" / "slices" / f"{example}.yaml").read_text())
        if edit:
            edit(raw)
        spec = SliceSpec.model_validate(raw)
        version, _ = store.put_slice(spec)
        return spec, version

    def create(self, example, prefix=None, edit=None):
        spec, version = self.put(example, edit)
        iid = prefix or spec.prefix
        record = InstanceRecord(id=iid, slice=SliceRef(slug=spec.slug, version=version), state="pending", created="t", updated="t")
        self.assertTrue(store.claim_instance(record))
        job = runner.new_job("create_instance", iid, stages.CREATE_STAGES, slice_ref=record.slice)
        return iid, job

    def run_job(self, job):
        return asyncio.run(runner.execute(job.id))

    def destroy(self, iid):
        job = runner.new_job("destroy_instance", iid, stages.DESTROY_STAGES)
        return self.run_job(job)

    def log(self, job):
        return store.job_log(job.id).read_text()

    def test_create_and_destroy(self):
        iid, job = self.create("information-environment")
        done = self.run_job(job)
        self.assertEqual(done.state, "succeeded", self.log(job))
        record = store.get_instance(iid)
        self.assertEqual(record.state, "ready")
        self.assertEqual(record.stages_done, stages.CREATE_STAGES)

        # Terraform: images applied before the instance, in the instance's workspace, converged by plan
        tf = self.checkout.calls("terraform")
        self.assertEqual(self.checkout.workspaces(), ["ie"])
        applied = self.checkout.state("slice", "ie")
        self.assertEqual(sorted(applied["oci_hosts"]), ["ie-news"])
        self.assertIn("GRAYSPACE_TLS_CERT", applied["oci_hosts"]["ie-news"]["env"])  # cert issued before apply
        images = self.checkout.state("images", "default")
        self.assertEqual(sorted(images["uploads"]), ["grayspace-ubuntu-lxc"])  # GraySpace's own: uploaded by default
        self.assertEqual(sorted(images["oci_images"]), ["ghosts-pandora"])
        first_slice_apply = next(i for i, c in enumerate(tf) if c["module"] == "slice" and c["args"][0] == "apply")
        first_images_apply = next(i for i, c in enumerate(tf) if c["module"] == "images" and c["args"][0] == "apply")
        self.assertLess(first_images_apply, first_slice_apply)
        self.assertTrue(any(c["args"][:2] == ["plan", "-detailed-exitcode"] and c["workspace"] == "ie" for c in tf))

        # Ansible: the per-instance inventory, outside ansible/inventory/
        play = self.checkout.calls("ansible-playbook")[-1]
        self.assertEqual(play["args"][-1], "slice.yml")
        self.assertIn(str(self.checkout.root / "ansible" / "slice-inventory" / "ie.ini"), play["args"])
        self.assertIn(str(self.checkout.root / "ansible" / "inventory" / "proxy.ini"), play["inventories"])  # the base's proxy variables
        self.assertIn("ie-readers-edge ansible_host=10.0.9.", play["inventory"])
        self.assertNotIn("ie-news", play["inventory"])  # OCI: not managed
        self.assertEqual(play["vars"]["slice_hosts"]["ie-corp"]["certificate"]["cert"], str(self.data / "instances" / "ie" / "ie-corp.crt"))
        self.assertTrue(play["vars"]["slice_ca_bundle"].endswith("ca-bundle.pem"))

        # DNS in the instance, not in the overlay (C14)
        self.assertEqual([d.domain for d in record.domains], ["dailychronicle.com", "northwind-trading.com"])
        self.assertEqual({(r.fqdn, r.data) for r in record.records}, {("www.dailychronicle.com", "198.41.0.2"), ("www.northwind-trading.com", "216.239.34.2")})
        self.assertEqual(mutation_tracker.dns_domains, [])
        self.assertEqual(mutation_tracker.dns_records, [])
        self.assertIn("www\t3600\tIN\tA\t198.41.0.2", self.hosts.read("dns-cloudflare", "/var/bind/db.dailychronicle.com"))
        self.assertIn("ns1.dailychronicle.com.", self.hosts.read("dns-gtld", "/var/bind/delegations.com"))

        # Certificates with SANs and the slice validity
        self.assertEqual(sorted(i[0] for i in self.ca.issued), ["www.dailychronicle.com", "www.northwind-trading.com"])
        self.assertTrue(all(i[3] == settings.slice_certificate_validity and i[2] for i in self.ca.issued))
        self.assertEqual({c.serial for c in record.certificates}, {"4242"})

        # Checks ran over SSH: edge to its router, resolution, reachability, TLS by name, published answers
        commands = [c for _, c in self.ssh_calls]
        self.assertIn("ping -c 2 -W 2 50.232.0.1", commands)
        self.assertTrue(any(c.startswith("getent hosts ns1.") for c in commands))
        self.assertTrue(any("https://www.dailychronicle.com/" in c for c in commands))
        self.assertIn("ping -c 2 -W 2 198.41.0.2", commands)

        # Manifest
        m = record.manifest
        self.assertEqual(m.id, "ie")
        self.assertEqual(m.sites[0].router, "com-chi")
        news = next(h for h in m.hosts if h.name == "news")
        self.assertEqual((news.ip, news.public_ip, news.fqdns, news.mgmt_ip), ("198.41.0.2", "198.41.0.2", ["www.dailychronicle.com"], None))
        self.assertTrue(next(h for h in m.hosts if h.name == "reader-01").mgmt_ip.startswith("10.0.9."))

        # The allocations are held while the instance lives
        self.assertIn("com-chi", service.taken().static)

        done = self.destroy(iid)
        self.assertEqual(done.state, "succeeded", self.log(done))
        record = store.get_instance(iid)
        self.assertEqual(record.state, "destroyed")
        self.assertEqual(self.checkout.workspaces(), [])
        self.assertEqual(self.checkout.selected(), "default")
        self.assertIsNone(self.checkout.state("slice", "ie"))
        self.assertEqual(self.checkout.state("images", "default")["downloads"], {})  # no live instance uses an image
        self.assertEqual(record.domains, [])
        self.assertEqual(record.records, [])
        self.assertIsNone(self.hosts.read("dns-cloudflare", "/var/bind/db.dailychronicle.com"))
        self.assertNotIn("dailychronicle", self.hosts.read("dns-gtld", "/var/bind/delegations.com"))
        self.assertEqual(sorted(s for _, s in self.ca.revoked), ["4242", "4242"])
        self.assertFalse((self.checkout.root / "terraform" / "slice" / "instances" / "ie.tfvars.json").exists())
        self.assertFalse((self.checkout.root / "ansible" / "slice-inventory" / "ie.ini").exists())
        self.assertEqual(service.taken().static, {})
        self.assertEqual(service.taken().prefixes, set())

    def base_state(self, crucible):
        state = {"version": 4, "outputs": {"crucible": {"value": crucible, "type": "object"}}}
        (self.checkout.root / "terraform" / "terraform.tfstate").write_text(json.dumps(state))

    @staticmethod
    def mark_first_site_and_counted_host(raw):
        raw["sites"][0]["crucible"] = True
        next(h for h in raw["hosts"] if h.get("count"))["crucible"] = True

    def test_marked_guests_join_the_bases_crucible_team(self):
        """An adopted tree whose base module still makes the view (TF_VAR_configure_crucible)."""
        env = self.checkout.root / ".environment"
        env.write_text(env.read_text() + 'export TF_VAR_configure_crucible=true\n')
        environment._load_env.cache_clear()
        self.base_state({"view_id": "view-1", "team_id": "team-1"})
        iid, job = self.create("meridian-bank", edit=self.mark_first_site_and_counted_host)
        done = self.run_job(job)
        self.assertEqual(done.state, "succeeded", self.log(job))
        tfvars = self.checkout.state("slice", iid)
        self.assertEqual(tfvars["crucible_team_id"], "team-1")
        record = store.get_instance(iid)
        counted = [h.hostname for h in record.allocation.hosts if h.crucible]
        self.assertGreater(len(counted), 1)  # every copy of a counted host
        self.assertEqual(tfvars["crucible_guests"], [record.allocation.sites[0].edge] + counted)
        self.assertNotIn(record.allocation.sites[1].edge, tfvars["crucible_guests"])
        self.assertIn("guests join the base's Crucible team team-1", self.log(job))
        destroy = self.destroy(iid)
        self.assertEqual(destroy.state, "succeeded", self.log(destroy))
        destroys = [c["args"] for c in self.checkout.calls("terraform") if c["module"] == "slice" and c["args"][0] == "destroy"]
        self.assertIn("-refresh=false", destroys[-1])

    def test_nothing_marked_registers_nothing(self):
        self.base_state({"view_id": "view-1", "team_id": "team-1"})
        iid, job = self.create("meridian-bank")
        self.run_job(job)
        self.assertNotIn("crucible_guests", self.checkout.state("slice", iid))
        self.destroy(iid)
        destroys = [c["args"] for c in self.checkout.calls("terraform") if c["module"] == "slice" and c["args"][0] == "destroy"]
        self.assertIn("-refresh=false", destroys[0])  # every first attempt

    def test_marks_without_a_player_view_warn(self):
        self.base_state(None)
        spec, _ = self.put("meridian-bank", edit=self.mark_first_site_and_counted_host)
        _, findings = asyncio.run(service.validate(spec, check_capacity=False))
        self.assertIn("CRUCIBLE_OFF", [f.code for f in findings if f.severity == "warning"])

    def test_without_crucible_nothing_is_registered(self):
        self.base_state(None)  # configure_crucible off: the output is null
        iid, job = self.create("meridian-bank")
        self.run_job(job)
        self.assertNotIn("crucible_team_id", self.checkout.state("slice", iid))
        self.destroy(iid)
        destroys = [c["args"] for c in self.checkout.calls("terraform") if c["module"] == "slice" and c["args"][0] == "destroy"]
        self.assertIn("-refresh=false", destroys[0])  # every first attempt

    def test_images_are_shared_and_kept_while_used(self):
        a, job = self.create("information-environment")
        self.assertEqual(self.run_job(job).state, "succeeded", self.log(job))
        b, job = self.create("meridian-bank")
        self.assertEqual(self.run_job(job).state, "succeeded", self.log(job))
        self.assertEqual(self.checkout.workspaces(), ["ie", "mb"])
        self.destroy(a)
        images = self.checkout.state("images", "default")
        self.assertEqual(sorted(images["uploads"]), ["grayspace-ubuntu-lxc"])  # GraySpace's own: uploaded by default
        self.assertEqual(images["oci_images"], {})  # Pandora was only ie's

    def test_apply_retries_until_converged(self):
        self.checkout.fail("apply", 2)
        iid, job = self.create("meridian-bank")
        done = self.run_job(job)
        self.assertEqual(done.state, "succeeded", self.log(job))
        self.assertIn("images apply attempt 3/6", self.log(job))

    def test_failure_and_resume(self):
        self.checkout.fail("ansible", 1)
        iid, job = self.create("meridian-bank")
        failed = self.run_job(job)
        self.assertEqual(failed.state, "failed")
        self.assertEqual(failed.stage, "configure")
        record = store.get_instance(iid)
        self.assertEqual(record.state, "failed")
        self.assertEqual(record.stages_done, ["validate", "allocate", "render", "apply", "wait"])
        self.assertIn("configure", record.error)
        applies_before = sum(1 for c in self.checkout.calls("terraform") if c["args"][0] == "apply")

        retry = runner.new_job("create_instance", iid, stages.CREATE_STAGES, resume_from=failed.stage, retry_of=failed.id)
        done = self.run_job(retry)
        self.assertEqual(done.state, "succeeded", self.log(retry))
        self.assertNotIn("=== apply ===", self.log(retry))  # resumed at configure
        self.assertIn("=== configure ===", self.log(retry))
        applies_after = sum(1 for c in self.checkout.calls("terraform") if c["args"][0] == "apply")
        self.assertEqual(applies_before, applies_after)
        self.assertEqual(store.get_instance(iid).state, "ready")

    def test_destroy_refreshes_only_when_retrying(self):
        # A refresh waits on a VM's guest agent for the whole agent.timeout when it has none
        iid, job = self.create("information-environment")
        self.assertEqual(self.run_job(job).state, "succeeded", self.log(job))
        self.checkout.fail("destroy", 1)
        done = self.destroy(iid)
        self.assertEqual(done.state, "succeeded", self.log(done))
        destroys = [c["args"] for c in self.checkout.calls("terraform") if c["module"] == "slice" and c["args"][0] == "destroy"]
        self.assertEqual(["-refresh=false" in d for d in destroys], [True, False])

    def test_failed_checks_fail_the_job_and_destroy_still_works(self):
        self.ssh_ok = False
        async def once(check, attempts=3, delay=10):
            return await check()

        with patch.object(stages, "_retrying", new=once):
            iid, job = self.create("meridian-bank")
            failed = self.run_job(job)
        self.assertEqual((failed.state, failed.stage), ("failed", "check"))
        self.assertIn("can't reach lum-chi", failed.error)
        done = self.destroy(iid)
        self.assertEqual(done.state, "succeeded", self.log(done))
        self.assertEqual(store.get_instance(iid).state, "destroyed")

    def test_no_create_retry_after_a_destroy_started(self):
        with patch.dict(stages.CREATE, {"check": AsyncMock(side_effect=runner.StageError("checks failed"))}):
            iid, job = self.create("meridian-bank")
            failed = self.run_job(job)
        with patch.dict(stages.DESTROY, {"destroy": AsyncMock(side_effect=runner.StageError("proxmox busy"))}):
            destroy = runner.new_job("destroy_instance", iid, stages.DESTROY_STAGES)
            with store.update_instance(iid) as r:
                r.jobs.append(destroy.id)
            self.assertEqual(self.run_job(destroy).state, "failed")
        self.assertEqual(store.get_instance(iid).state, "failed")
        retry = runner.new_job("create_instance", iid, stages.CREATE_STAGES, retry_of=failed.id)
        done = self.run_job(retry)
        self.assertEqual(done.state, "failed")
        self.assertIn("partly destroyed", done.error)
        # The destroy can be retried to the end
        self.assertEqual(self.destroy(iid).state, "succeeded")

    def test_destroy_a_failed_create_before_apply(self):
        with patch.dict(stages.CREATE, {"render": AsyncMock(side_effect=runner.StageError("CA unreachable"))}):
            iid, job = self.create("meridian-bank")
            self.assertEqual(self.run_job(job).state, "failed")
        done = self.destroy(iid)
        self.assertEqual(done.state, "succeeded", self.log(done))
        self.assertIn("No Terraform workspace", self.log(done))

    def test_validation_failure(self):
        other_spec, _ = self.put("routed-campus")
        # Take every static address on com-chi so the readers site can't attach
        alloc, _ = plan.resolve(SliceSpec.model_validate({
            "apiVersion": "grayspace/v1", "kind": "Slice", "slug": "hog", "prefix": "hog",
            "hosts": [{"name": "h", "count": 18, "internet": {"router": "com-chi"}, "image": "ghosts-pandora"}]}),
            service.context(), stages.catalog.images(), stages.catalog.roles(), plan.Taken())
        store.claim_instance(InstanceRecord(id="hog", slice=SliceRef(slug="hog", version="sha256:" + "1" * 64), state="ready",
                                            created="t", updated="t", allocation=alloc))
        iid, job = self.create("information-environment")
        failed = self.run_job(job)
        self.assertEqual((failed.state, failed.stage), ("failed", "validate"))
        self.assertIn("NO_STATIC_ADDRESSES", failed.error)
        self.assertEqual(self.checkout.calls("terraform"), [])

    def test_cancel_at_the_next_stage_boundary(self):
        iid, job = self.create("meridian-bank")
        original = stages.CREATE["allocate"]

        async def allocate_then_cancel(ctx, record):
            await original(ctx, record)
            with store.update_job(job.id) as j:
                j.cancel_requested = True

        with patch.dict(stages.CREATE, {"allocate": allocate_then_cancel}):
            done = self.run_job(job)
        self.assertEqual(done.state, "cancelled")
        self.assertEqual(store.get_instance(iid).stages_done, ["validate", "allocate"])
        self.assertEqual(store.get_instance(iid).state, "failed")

    def test_waits_for_the_node_lock(self):
        iid, job = self.create("meridian-bank")
        lock = open(self.checkout.root / "terraform" / ".grayspace.lock", "w")
        fcntl.flock(lock, fcntl.LOCK_EX)
        threading.Timer(3, lambda: (fcntl.flock(lock, fcntl.LOCK_UN), lock.close())).start()
        done = self.run_job(job)
        self.assertEqual(done.state, "succeeded")
        self.assertIn("Waiting for the node lock", self.log(job))

    def test_plan_job(self):
        spec, version = self.put("routed-campus")
        job = runner.new_job("plan_slice", spec.slug, stages.PLAN_STAGES, slice_ref=SliceRef(slug=spec.slug, version=version))
        done = self.run_job(job)
        self.assertEqual(done.state, "succeeded", self.log(job))
        self.assertEqual(done.result["plan"]["add"], 5)
        self.assertEqual(done.result["capacity_required"]["guests"], 5)
        self.assertEqual(done.result["allocation"]["sites"][0]["asn"], 64512)
        self.assertEqual(self.checkout.workspaces(), [])  # the scratch workspace is gone
        self.assertEqual(store.live_instances(), [])  # nothing was allocated

    def test_routed_site_attaches_base_routers(self):
        iid, job = self.create("routed-campus")
        done = self.run_job(job)
        self.assertEqual(done.state, "succeeded", self.log(job))
        attach = [c for c in self.checkout.calls("ansible-playbook") if "slice-attach.yml" in c["args"]]
        self.assertEqual(attach[0]["args"][-1], "are-chi,lum-chi")
        self.assertEqual(attach[0]["data_path"], str(self.data))
        record = store.get_instance(iid)
        self.assertEqual({(a.router, a.ip, a.asn) for a in record.attachments}, {("lum-chi", "209.151.130.2", 64512), ("are-chi", "199.7.83.2", 64512)})
        play = [c for c in self.checkout.calls("ansible-playbook") if c["args"][-1] == "slice.yml"][-1]
        self.assertEqual(play["vars"]["slice_edges"]["rc-campus-edge"]["bgp"]["neighbors"][0]["ip"], "209.151.130.1")

        self.destroy(iid)
        detach = [c for c in self.checkout.calls("ansible-playbook") if "slice-attach.yml" in c["args"]][-1]
        self.assertEqual(detach["args"][-1], "are-chi,lum-chi")
        self.assertEqual(store.get_instance(iid).attachments, [])

    def test_restart_marks_running_jobs_failed(self):
        iid, job = self.create("meridian-bank")
        with store.update_job(job.id) as j:
            j.state = "running"
        r = runner.Runner()
        asyncio.run(self._start_stop(r))
        self.assertEqual(store.get_job(job.id).state, "failed")
        self.assertIn("Interrupted", store.get_job(job.id).error)
        self.assertEqual(store.get_instance(iid).state, "failed")

    async def _start_stop(self, r):
        await r.start()
        await r.stop()


if __name__ == "__main__":
    unittest.main()
