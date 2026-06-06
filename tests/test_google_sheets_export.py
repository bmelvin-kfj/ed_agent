import json

import ed_extract


def test_upload_export_to_google_sheets_uses_service_account(monkeypatch, tmp_path):
    export_file = tmp_path / "ecoledirecte_export_test.json"
    export_file.write_text(json.dumps({"ok": True}), encoding="utf-8")

    captured = {}

    class FakeCredentials:
        @staticmethod
        def from_service_account_info(info, scopes):
            captured["service_account_info"] = info
            captured["scopes"] = scopes
            return object()

    class FakeSheetsService:
        def spreadsheets(self):
            return self

        def values(self):
            return self

        def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
            captured["spreadsheetId"] = spreadsheetId
            captured["range"] = range
            captured["valueInputOption"] = valueInputOption
            captured["insertDataOption"] = insertDataOption
            captured["body"] = body
            return self

        def execute(self):
            captured["executed"] = True
            return {"updates": {"updatedRows": 1}}

    monkeypatch.setenv("GOOGLE_SHEETS_SPREADSHEET_ID", "sheet-id-123")
    monkeypatch.setenv("GOOGLE_SHEETS_RANGE", "Exports!A1")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')

    monkeypatch.setattr(ed_extract, "service_account", type("svc", (), {"Credentials": FakeCredentials}), raising=False)
    monkeypatch.setattr(ed_extract, "build", lambda *args, **kwargs: FakeSheetsService(), raising=False)

    result = ed_extract._upload_export_to_google_sheets(export_file)

    assert result["enabled"] is True
    assert captured["spreadsheetId"] == "sheet-id-123"
    assert captured["range"] == "Exports!A1"
    assert captured["body"]["values"][0][0].startswith("Export JSON")
    assert captured["executed"] is True
