from pathlib import Path

import ed_extract


def test_run_full_extraction_prefers_live_extraction(monkeypatch, tmp_path):
    token_file = tmp_path / "ed_session_config.json"
    token_file.write_text('{"token": "abc"}', encoding="utf-8")

    export_file = tmp_path / "export.json"
    export_file.write_text('{"ok": true}', encoding="utf-8")

    calls = []

    class FakeManager:
        def __init__(self, username, password):
            calls.append(("manager", username, password))

        def run_auth_workflow(self):
            calls.append("live")

    def fake_load_credentials():
        calls.append("credentials")
        return ("user", "pass")

    monkeypatch.setattr(ed_extract, "TOKEN_SAVED_FILE", token_file)
    monkeypatch.setattr(ed_extract, "load_credentials", fake_load_credentials)
    monkeypatch.setattr(ed_extract, "EcoleDirecteSessionManager", FakeManager)
    monkeypatch.setattr(ed_extract, "_resolve_latest_export_file", lambda: export_file)
    monkeypatch.setattr(ed_extract.time, "sleep", lambda *_: None)

    def fail_data_extract():
        calls.append("data_fallback")
        raise AssertionError("saved-session fallback should not be used")

    monkeypatch.setattr(ed_extract, "_run_data_extract", fail_data_extract)

    result = ed_extract._run_full_extraction()

    assert result == export_file
    assert calls.count("live") == 1
    assert calls.count("data_fallback") == 0
