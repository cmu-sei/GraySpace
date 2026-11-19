"""Settings are read when the app's modules are imported; give the required ones test values."""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

for key, value in {
    "ENVIRONMENT_ID": "test",
    "PROMETHEUS_URL": "http://prometheus.invalid:9090",
    "PROXMOX_API_HOST": "proxmox.invalid",
    "PROXMOX_NODE": "pve",
    "TFVARS_PATH": "/nonexistent/terraform.tfvars.json",
    "BASES_PATH": str(REPO / "bases"),
    "SCRIPTS_PATH": str(REPO / "scripts"),
    # No checkout: whether jobs can run must never depend on the machine running the tests (CI has
    # no .environment or base SSH key). Tests that need one use tests/fake_tools.py's FakeCheckout
    "CODEBASE_PATH": "/nonexistent/checkout",
    "GRAYSPACE_API_TOKEN": "test-token",
    "GRAYSPACE_CORS_ORIGINS": "http://ui.example",
    # Slices, instances and jobs; tests that write there patch settings.data_path to their own
    "DATA_PATH": tempfile.mkdtemp(prefix="grayspace-test-data-"),
}.items():
    os.environ[key] = value

atexit.register(shutil.rmtree, os.environ["DATA_PATH"], True)

# The header tests send to pass the token check (services/auth.py)
AUTH = {"Authorization": "Bearer test-token"}
