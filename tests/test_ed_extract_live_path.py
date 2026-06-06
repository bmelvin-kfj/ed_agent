import json
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


def test_run_auth_workflow_cleans_session_after_use(monkeypatch, tmp_path):
    token_file = tmp_path / "ed_session_config.json"
    token_file.write_text('{"token": "old"}', encoding="utf-8")

    class FakeLocator:
        def __init__(self, *args, **kwargs):
            self.first = self

        def fill(self, value):
            return None

        def click(self):
            return None

        def count(self):
            return 0

    class FakePage:
        def __init__(self):
            self.url = "https://www.ecoledirecte.com/Eleves/8602"
            self._response_handlers = []

        def on(self, event, handler):
            self._response_handlers.append((event, handler))

        def goto(self, *args, **kwargs):
            return None

        def locator(self, selector):
            return FakeLocator()

        def wait_for_function(self, *args, **kwargs):
            return None

        def wait_for_url(self, *args, **kwargs):
            return None

    class FakeContext:
        def __init__(self):
            self.cleared_cookies = False
            self.closed = False

        def set_default_timeout(self, *_args, **_kwargs):
            return None

        def new_page(self):
            return FakePage()

        def clear_cookies(self):
            self.cleared_cookies = True

        def close(self):
            self.closed = True

    class FakeBrowser:
        def __init__(self):
            self.context = FakeContext()

        def new_context(self, **kwargs):
            return self.context

        def close(self):
            return None

    launched_browser = FakeBrowser()

    class FakeChromium:
        def launch(self, headless=True):
            return launched_browser

    class FakePlaywright:
        def __init__(self):
            self.chromium = FakeChromium()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeExtractor:
        def __init__(self, page, session_config, account_id):
            self.page = page
            self.session_config = session_config
            self.account_id = account_id

        def extract_all(self):
            return tmp_path / "export.json"

    fake_playwright = FakePlaywright()

    monkeypatch.setattr(ed_extract, "TOKEN_SAVED_FILE", token_file)
    monkeypatch.setattr(ed_extract, "sync_playwright", lambda: fake_playwright)
    monkeypatch.setattr(ed_extract, "EcoleDirecteExtractor", FakeExtractor)
    monkeypatch.setattr(ed_extract.EcoleDirecteSessionManager, "_human_delay", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(ed_extract.EcoleDirecteSessionManager, "_wait_for_login_result", lambda self, page: None)
    monkeypatch.setattr(ed_extract.EcoleDirecteSessionManager, "_wait_for_homepage", lambda self, page: None)
    monkeypatch.setattr(ed_extract.EcoleDirecteSessionManager, "_wait_for_token_capture", lambda self, timeout_seconds: None)
    monkeypatch.setattr(ed_extract.EcoleDirecteSessionManager, "_get_account_id", lambda self, page: "8602")
    monkeypatch.setattr(ed_extract.EcoleDirecteSessionManager, "_handle_response", lambda self, response: None)

    manager = ed_extract.EcoleDirecteSessionManager("user", "pass")
    manager.extracted_token = "token-123"
    manager.extracted_headers = {"X-Token": "token-123"}

    result = manager.run_auth_workflow()

    assert result["account_id"] == "8602"
    assert launched_browser.context.cleared_cookies is True
    assert not token_file.exists()
