import shutil
from pathlib import Path

import pytest

import cloud_run


class FakeBlob:
    """模擬 Cloud Storage 物件:記住內容與 generation,並檢查 if_generation_match。"""
    name = "bot.db"

    class bucket:
        name = "b"

    def __init__(self, src: Path | None = None):
        self.data, self.generation = (src.read_bytes(), 7) if src else (None, None)

    def exists(self): return self.data is not None
    def reload(self): pass

    def download_to_filename(self, path, if_generation_match=None):
        assert if_generation_match == self.generation
        Path(path).write_bytes(self.data)

    def upload_from_filename(self, path, if_generation_match=None):
        if if_generation_match != (self.generation or 0):
            raise RuntimeError("PreconditionFailed")
        self.data, self.generation = Path(path).read_bytes(), (self.generation or 0) + 1


def test_pull_refuses_to_start_fresh_unless_allowed(tmp_path):
    with pytest.raises(SystemExit):
        cloud_run.pull(FakeBlob(), tmp_path / "d" / "bot.db", allow_init=False)
    assert cloud_run.pull(FakeBlob(), tmp_path / "d" / "bot.db", allow_init=True) == 0


def test_roundtrip_and_conflict_detection(tmp_path):
    src = tmp_path / "src.db"; src.write_bytes(b"v1")
    blob, db = FakeBlob(src), tmp_path / "work" / "bot.db"
    gen = cloud_run.pull(blob, db, allow_init=False)
    assert db.read_bytes() == b"v1" and gen == 7
    db.write_bytes(b"v2")
    cloud_run.push(blob, db, gen)
    assert blob.data == b"v2" and blob.generation == 8
    with pytest.raises(RuntimeError):                      # 用舊的 generation 再傳一次 -> 被擋下,不會覆蓋
        cloud_run.push(blob, db, gen)


def test_main_uploads_only_when_the_day_actually_ran(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STATE_INIT", "1")
    import bot.runner as runner
    calls = iter([{"day": "2026-01-01", "actions": []}, {"skipped": True, "day": "2026-01-01"}])

    def fake_run(force=False):
        Path("data/bot.db").write_bytes(b"state")
        return next(calls)
    monkeypatch.setattr(runner, "run_once", fake_run)
    monkeypatch.setattr(runner, "report", lambda: "report")
    blob = FakeBlob()
    cloud_run.main(["cloud_run.py"], blob)
    assert blob.data == b"state" and blob.generation == 1
    cloud_run.main(["cloud_run.py"], blob)                 # 當天已跑過 -> 不再上傳
    assert blob.generation == 1
