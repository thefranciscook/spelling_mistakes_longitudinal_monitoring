import os
import sys
from pathlib import Path

import pytest

from spelling_observatory.config import Config, ensure_home
from spelling_observatory.resources import fetch_resources, resources_present

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

RESOURCE_CACHE = Path(os.environ.get(
    "SPELLING_OBSERVATORY_TEST_RESOURCES", Path.home() / ".cache" / "spelling-observatory-test-resources"))


@pytest.fixture(scope="session")
def resources_dir() -> Path:
    if not resources_present(RESOURCE_CACHE):
        try:
            fetch_resources(RESOURCE_CACHE, log=lambda *_: None)
        except Exception as e:  # pragma: no cover - offline machine without a cache
            pytest.skip(f"Hunspell dictionaries unavailable: {e}")
    return RESOURCE_CACHE


def make_cfg(home: Path, resources: Path) -> Config:
    cfg = Config(home=home)
    cfg.storage.encryption = "none"
    ensure_home(cfg)
    (home / "resources").symlink_to(resources, target_is_directory=True)
    return cfg


@pytest.fixture(scope="session")
def session_cfg(tmp_path_factory, resources_dir) -> Config:
    return make_cfg(tmp_path_factory.mktemp("home"), resources_dir)


@pytest.fixture(scope="session")
def analyzer(session_cfg):
    from spelling_observatory.classify.pipeline import Analyzer
    return Analyzer(session_cfg)


@pytest.fixture
def cfg(tmp_path, resources_dir) -> Config:
    return make_cfg(tmp_path / "home", resources_dir)


@pytest.fixture
def store(cfg):
    from spelling_observatory.store.db import connect
    s = connect(cfg)
    yield s
    s.close()


def errors(analysis):
    return [w for w in analysis.words if w.is_error]
