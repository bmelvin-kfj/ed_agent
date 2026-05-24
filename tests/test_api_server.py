from fastapi.testclient import TestClient

import api_server


def test_requires_api_key(monkeypatch):
    monkeypatch.setattr(api_server, "API_KEY", "secret")
    client = TestClient(api_server.app)

    response = client.get("/data")

    assert response.status_code == 401
    assert response.json()["detail"] == "Clé API invalide ou absente."


def test_returns_cached_payload(monkeypatch):
    payload = {
        "meta": {
            "eleve_id": "8602",
            "school_year": "2025-2026",
            "source": "test",
            "fetched_at": 123.0,
        },
        "notes": [{"matiere": "Mathématiques", "valeur": 15}],
        "devoirs": [],
        "emploi_du_temps": [],
    }
    calls = []

    def fake_fetch_ecoledirecte_data():
        calls.append(1)
        return payload

    monkeypatch.setattr(api_server, "API_KEY", "secret")
    monkeypatch.setattr(api_server, "fetch_ecoledirecte_data", fake_fetch_ecoledirecte_data)
    api_server.CACHE["current"] = None

    client = TestClient(api_server.app)

    first = client.get("/data", headers={"X-API-Key": "secret"})
    second = client.get("/data", headers={"X-API-Key": "secret"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["cache"] == "miss"
    assert second.json()["cache"] == "hit"
    assert len(calls) == 1
