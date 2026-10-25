import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENV_KEYS = ["GE_LLM", "GE_FAULT", "GE_DATA_DIR", "GE_CONTENT_DIR", "GE_OUT_DIR", "GE_LOG_DIR", "UTM_BASE_URL",
            "GE_CONTENT_BASE_URL", "LLM_CONCURRENCY"]


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A throwaway project root: real config + text samples, its own database and output folders."""
    shutil.copytree(ROOT / "config", tmp_path / "config")
    (tmp_path / "samples").mkdir()
    for name in ("I2_blog.md", "I2_blog_v2.md"):
        shutil.copy(ROOT / "samples" / name, tmp_path / "samples" / name)
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GE_LLM", "mock")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def settings(project):
    from growthengine.settings import load_settings
    return load_settings(project)


def run(settings, *args, **kw):
    from growthengine.pipeline import Pipeline
    kw.setdefault("week", "2026-11-02")
    return Pipeline(settings, quiet=True).run(*args, **kw)
