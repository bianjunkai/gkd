import pytest

from app.auth import AuthService
from app.changes import ChangeEngine
from app.config import Settings
from app.db import Database
from app.storage import ContentStore


@pytest.fixture
def system(tmp_path):
    settings = Settings(_env_file=None, data_root=tmp_path, run_worker=False)
    db = Database(settings)
    db.initialize()
    store = ContentStore(tmp_path)
    auth = AuthService(db, store, settings)
    profile = auth.register("researcher", "correct-horse-2026", "研发同学")
    engine = ChangeEngine(db, store)
    yield settings, db, store, auth, profile, engine
    db.dispose()
