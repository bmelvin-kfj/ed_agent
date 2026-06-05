import json
from pathlib import Path

import ed_extract
from fastapi.testclient import TestClient


def test_latest_export_generates_when_no_export_file_exists(monkeypatch, tmp_path):
    export_file = tmp_path / "generated.json"
    export_file.write_text(json.dumps({"generated": True}), encoding="utf-8")

    monkeypatch.setattr(ed_extract, "ED_API_KEY", "secret", raising=False)
    monkeypatch.setattr(ed_extract.os, "getenv", lambda name, default=None: "secret" if name in {"ED_API_KEY", "API_KEY"} else default)
    monkeypatch.setattr(ed_extract, "_resolve_latest_export_file", lambda: (_ for _ in ()).throw(RuntimeError("no export")))
    monkeypatch.setattr(ed_extract, "_run_full_extraction", lambda: export_file)

    client = TestClient(ed_extract.app)
    response = client.get("/latest-export", headers={"API_KEY": "secret"})

    assert response.status_code == 200
    assert response.json() == {"generated": True}
