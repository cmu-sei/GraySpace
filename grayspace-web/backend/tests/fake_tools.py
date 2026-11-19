"""A GraySpace checkout in a temporary directory, with fake terraform and ansible-playbook on PATH.

The fake terraform keeps a state file per workspace: apply stores the var file (or, applying a saved
plan, the vars the plan was made with), plan reports no changes once the stored vars match and
writes its vars to -out=, output -json reports each guest with a management address,
destroy removes the state. Every call is logged to calls.log, as JSON lines, so tests can check
what ran in which order. fail("apply" | "destroy" | "ansible", n) makes the next n calls fail.
"""

import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

TERRAFORM = r'''#!{python}
import json, os, sys
from pathlib import Path

root = Path(os.environ["FAKE_ROOT"])
args = sys.argv[1:]
cwd = Path.cwd()
module = cwd.name
states = cwd / "fake-state"
states.mkdir(exist_ok=True)

# The module's selected workspace, as .terraform/environment holds it; TF_WORKSPACE overrides it.
selected_file = states / "selected"
selected = selected_file.read_text() if selected_file.exists() else "default"
ws = os.environ.get("TF_WORKSPACE", selected)
with open(root / "calls.log", "a") as f:
    f.write(json.dumps({"tool": "terraform", "module": module, "workspace": ws, "args": args}) + "\n")


def var_file():
    for a in args:
        if a.startswith("-var-file="):
            return json.loads(Path(a.split("=", 1)[1]).read_text())
    return {}


def saved_plan():
    """The plan file an apply names (a saved plan carries its variables), or None."""
    files = [a for a in args[1:] if not a.startswith("-")]
    return Path(files[0]) if files else None


def counter(name):
    path = root / name
    n = int(path.read_text()) if path.exists() else 0
    if n > 0:
        path.write_text(str(n - 1))
        return True
    return False


cmd = args[0]
if cmd == "init" and "-upgrade" not in args and counter("outdated_locks"):
    # What Terraform says when a constraint moved past the lock file
    print("Error: Failed to query available provider packages", file=sys.stderr)
    print("locked provider registry.terraform.io/cmu-sei/crucible 2.5.0 does not match configured version constraint ~> 2.7.1; "
          "must use terraform init -upgrade to allow selection of new versions", file=sys.stderr)
    sys.exit(1)
elif cmd == "init":
    print("Terraform has been successfully initialized!")
elif cmd == "workspace":
    sub = args[1]
    existing = sorted(p.stem for p in states.glob("*.ws"))
    if sub == "list":
        print("* default")
        for w in existing:
            print(f"  {w}")
    elif sub == "new":
        if (states / f"{args[2]}.ws").exists():
            print(f'Workspace "{args[2]}" already exists', file=sys.stderr)
            sys.exit(1)
        (states / f"{args[2]}.ws").touch()
        selected_file.write_text(args[2])  # like Terraform, new selects the workspace
    elif sub == "select":
        name = args[-1]
        if name != "default" and not (states / f"{name}.ws").exists():
            print(f"Workspace {name} doesn't exist", file=sys.stderr)
            sys.exit(1)
        selected_file.write_text(name)
    elif sub == "delete":
        name = args[-1]
        if name == ws:
            print(f'Workspace "{name}" is your active workspace. You cannot delete the currently active workspace.', file=sys.stderr)
            sys.exit(1)
        if (states / f"{name}.json").exists() and "-force" not in args:
            print("Workspace is not empty", file=sys.stderr)
            sys.exit(1)
        (states / f"{name}.ws").unlink(missing_ok=True)
        (states / f"{name}.json").unlink(missing_ok=True)
elif cmd == "apply":
    if "-refresh-only" in args:
        print("Apply complete!")
    elif counter("fail_applies"):
        print("Error: SDN apply in progress", file=sys.stderr)
        sys.exit(1)
    else:
        plan_file = saved_plan()
        if plan_file and any(a.startswith("-var") for a in args):
            print("Error: Can't set variables when applying a saved plan", file=sys.stderr)
            sys.exit(1)
        (states / f"{ws}.json").write_text(plan_file.read_text() if plan_file else json.dumps(var_file()))
        print("Apply complete! Resources: 3 added, 0 changed, 0 destroyed.")
elif cmd == "plan":
    for a in args:
        if a.startswith("-out="):
            Path(a.split("=", 1)[1]).write_text(json.dumps(var_file()))
    path = states / f"{ws}.json"
    if path.exists() and json.loads(path.read_text()) == var_file():
        print("No changes. Your infrastructure matches the configuration.")
        sys.exit(0)
    print("Plan: 5 to add, 0 to change, 0 to destroy.")
    sys.exit(2 if "-detailed-exitcode" in args else 0)
elif cmd == "output":
    path = states / f"{ws}.json"
    v = json.loads(path.read_text()) if path.exists() else {}
    guests, n = {}, 10
    for kind in ("edges", "lxc_hosts", "vm_hosts", "oci_hosts"):
        for name in sorted(v.get(kind) or {}):
            guests[name] = {"vm_id": 1000 + n, "kind": kind, "mgmt_ip": None if kind == "oci_hosts" else f"10.0.9.{n}"}
            n += 1
    print(json.dumps({"guests": {"value": guests}}))
elif cmd == "destroy" and counter("fail_destroys"):
    print("Error: destroying: a resource couldn't be deleted", file=sys.stderr)
    sys.exit(1)
elif cmd == "destroy":
    (states / f"{ws}.json").unlink(missing_ok=True)
    print("Destroy complete!")
elif cmd == "state":
    path = states / f"{ws}.json"
    if path.exists():
        print("proxmox_sdn_vnet.slice")
else:
    print(f"fake terraform: unknown command {args}", file=sys.stderr)
    sys.exit(1)
'''

ANSIBLE = r'''#!{python}
import json, os, sys
from pathlib import Path

root = Path(os.environ["FAKE_ROOT"])
args = sys.argv[1:]
entry = {"tool": "ansible-playbook", "args": args, "cwd": os.getcwd(), "data_path": os.environ.get("GRAYSPACE_DATA_PATH")}
for i, a in enumerate(args):
    if a == "-i":
        entry.setdefault("inventories", []).append(args[i + 1])
        inv = Path(args[i + 1])
        entry["inventory"] = entry.get("inventory", "") + (inv.read_text() if inv.is_file() else "")
    if a == "-e" and args[i + 1].startswith("@"):
        import yaml

        class Loader(yaml.SafeLoader):
            pass

        Loader.add_constructor("!unsafe", lambda loader, node: loader.construct_scalar(node))
        entry["vars"] = yaml.load(Path(args[i + 1][1:]).read_text(), Loader=Loader)
with open(root / "calls.log", "a") as f:
    f.write(json.dumps(entry) + "\n")
path = root / "fail_ansible"
n = int(path.read_text()) if path.exists() else 0
if n > 0:
    path.write_text(str(n - 1))
    print("fatal: [mb-ws-01]: UNREACHABLE!", file=sys.stderr)
    sys.exit(2)
print("PLAY RECAP")
'''

ENVIRONMENT = """export TF_VAR_environment_id="abc"
export TF_VAR_proxmox_filestore="local"
export TF_VAR_proxmox_blockstore="local-lvm"
export TF_VAR_proxmox_node="pve"
"""


class FakeCheckout:
    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="grayspace-checkout-"))
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        for name, text in (("terraform", TERRAFORM), ("ansible-playbook", ANSIBLE)):
            path = bin_dir / name
            path.write_text(text.replace("#!{python}", f"#!{sys.executable}", 1))
            path.chmod(path.stat().st_mode | stat.S_IEXEC)
        for d in ("terraform/slice", "terraform/images", "ansible", ".ssh"):
            (self.root / d).mkdir(parents=True)
        for f in ("terraform/main.tf", "terraform/slice/main.tf", "ansible/main.yml", "ansible/slice.yml"):
            (self.root / f).write_text("# fake\n")
        (self.root / ".ssh/terraform_key.pub").write_text("ssh-rsa AAAA test\n")
        (self.root / ".environment").write_text(ENVIRONMENT)
        self.path = f"{bin_dir}:{os.environ['PATH']}"

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def calls(self, tool=None):
        log = self.root / "calls.log"
        lines = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return [c for c in lines if tool is None or c["tool"] == tool]

    def fail(self, what: str, times: int = 1):
        (self.root / {"apply": "fail_applies", "destroy": "fail_destroys", "ansible": "fail_ansible", "lock": "outdated_locks"}[what]).write_text(str(times))

    def state(self, module, workspace):
        path = self.root / "terraform" / module / "fake-state" / f"{workspace}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def workspaces(self, module="slice"):
        return sorted(p.stem for p in (self.root / "terraform" / module / "fake-state").glob("*.ws"))

    def selected(self, module="slice"):
        path = self.root / "terraform" / module / "fake-state" / "selected"
        return path.read_text() if path.exists() else "default"
