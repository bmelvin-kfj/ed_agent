from __future__ import annotations

import os
import time
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from data_extract import calculate_academic_year, load_session, make_api_request, safe_base64_decode

load_dotenv()

APP_NAME = "ecoledirecte-api"
CACHE_TTL_SECONDS = 60 * 60
CACHE = {"current": None, "expires_at": 0}
API_KEY = os.getenv("ED_API_KEY", "")

app = FastAPI(title=APP_NAME, description="Pont FastAPI pour exposer des données École Directe en JSON.")


def _get_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> str:
    if not API_KEY:
        raise HTTPException(status_code=500, detail="ED_API_KEY n'est pas configuré dans l'environnement.")
    if not x_api_key or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Clé API invalide ou absente.")
    return x_api_key


def _build_devoirs_payload(session_headers: dict[str, Any], eleve_id: str) -> dict[str, Any]:
    agenda_url = f"https://api.ecoledirecte.com/v3/Eleves/{eleve_id}/cahierdetexte.awp?verbe=get"
    agenda_response = make_api_request(agenda_url, session_headers)

    if not agenda_response or agenda_response.get("code") != 200:
        raise RuntimeError("Impossible de récupérer le cahier de textes.")

    agenda_data = agenda_response.get("data", {})
    detailed_homeworks: dict[str, Any] = {}

    for day in list(agenda_data.keys()):
        day_url = f"https://api.ecoledirecte.com/v3/Eleves/{eleve_id}/cahierdetexte/{day}.awp?verbe=get"
        day_response = make_api_request(day_url, session_headers)
        if not day_response or day_response.get("code") != 200:
            raise RuntimeError(f"Impossible de récupérer les détails du cahier de textes pour {day}.")

        day_details = day_response.get("data", {})
        for subject in day_details.get("matieres", []):
            if isinstance(subject, dict):
                a_faire = subject.get("aFaire")
                if isinstance(a_faire, dict):
                    a_faire["contenu_decode"] = safe_base64_decode(a_faire.get("contenu"))

                contenu_seance = subject.get("contenuDeSeance")
                if isinstance(contenu_seance, dict):
                    contenu_seance["contenu_decode"] = safe_base64_decode(contenu_seance.get("contenu"))

        detailed_homeworks[day] = day_details

    return {
        "vue_ensemble": agenda_data,
        "details_jours": detailed_homeworks,
    }


def _build_emploi_du_temps_payload(session_headers: dict[str, Any], eleve_id: str) -> dict[str, Any]:
    edt_url = f"https://api.ecoledirecte.com/v3/E/{eleve_id}/emploidutemps.awp?verbe=get"
    payload = {
        "data": {
            "dateDebut": (time.strftime("%Y-%m-%d", time.localtime(time.time() - 14 * 24 * 60 * 60))),
            "dateFin": (time.strftime("%Y-%m-%d", time.localtime(time.time() + 21 * 24 * 60 * 60))),
            "avecCoursAnnule": True,
        }
    }
    response = make_api_request(edt_url, session_headers, payload)
    if not response or response.get("code") != 200:
        raise RuntimeError("Impossible de récupérer l'emploi du temps.")
    return response.get("data", {})


def _resolve_student_id(session: dict[str, Any]) -> str:
    for key in ("eleve_id", "student_id", "account_id"):
        value = session.get(key)
        if value:
            return str(value)

    env_id = os.getenv("ED_STUDENT_ID")
    if env_id:
        return env_id

    return "8602"


def fetch_ecoledirecte_data() -> dict[str, Any]:
    session = load_session()
    if not session:
        raise RuntimeError("Session École Directe introuvable. Lancez d'abord le script d'authentification.")

    headers = session.get("headers")
    if not isinstance(headers, dict) or not headers:
        raise RuntimeError("Headers de session École Directe manquants ou invalides.")

    eleve_id = _resolve_student_id(session)
    school_year = calculate_academic_year()

    notes_url = f"https://api.ecoledirecte.com/v3/eleves/{eleve_id}/notes.awp?verbe=get"
    notes_response = make_api_request(notes_url, headers, {"data": {"anneeScolaire": ""}})
    if not notes_response or notes_response.get("code") != 200:
        raise RuntimeError("Impossible de récupérer les notes.")

    devoirs_payload = _build_devoirs_payload(headers, eleve_id)
    edt_payload = _build_emploi_du_temps_payload(headers, eleve_id)

    return {
        "meta": {
            "eleve_id": eleve_id,
            "school_year": school_year,
            "source": "EcoleDirecte",
            "fetched_at": time.time(),
        },
        "notes": notes_response.get("data", {}),
        "devoirs": devoirs_payload,
        "emploi_du_temps": edt_payload,
    }


@app.get("/data")
def get_data(x_api_key: str = Header(default=None, alias="X-API-Key")) -> JSONResponse:
    _get_api_key(x_api_key)

    now = time.time()
    cache_entry = CACHE.get("current")
    if cache_entry is not None and now < CACHE["expires_at"]:
        return JSONResponse(
            {
                "cache": "hit",
                "fetched_at": CACHE["current"]["meta"]["fetched_at"],
                "expires_at": CACHE["expires_at"],
                "data": CACHE["current"],
            }
        )

    try:
        data = fetch_ecoledirecte_data()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Impossible de récupérer les données École Directe : {exc}") from exc

    meta = data.get("meta") or {"fetched_at": now, "source": "mock", "eleve_id": None, "school_year": None}
    data["meta"] = meta
    CACHE["current"] = data
    CACHE["expires_at"] = now + CACHE_TTL_SECONDS

    return JSONResponse(
        {
            "cache": "miss",
            "fetched_at": meta["fetched_at"],
            "expires_at": CACHE["expires_at"],
            "data": data,
        }
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("API_PORT", "8000")))
