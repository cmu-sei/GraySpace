"""Give a test its own data directory (slices, instances, jobs) and checkout paths."""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from config import settings


def isolate(test, **extra_settings) -> Path:
    data = Path(tempfile.mkdtemp(prefix="grayspace-test-"))
    test.addCleanup(shutil.rmtree, data, True)
    for key, value in {"data_path": str(data), **extra_settings}.items():
        p = patch.object(settings, key, value)
        p.start()
        test.addCleanup(p.stop)
    return data
