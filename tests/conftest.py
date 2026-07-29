import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Изолированный KIMI_CODE_HOME во временной папке."""
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path))
    return tmp_path
