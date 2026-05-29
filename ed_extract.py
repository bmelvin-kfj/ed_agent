from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
    from fastapi import FastAPI, Header, HTTPException, Response
    from playwright.sync_api import Browser, BrowserContext, Page, Response as PlaywrightResponse, sync_playwright
except ImportError as exc:
    raise SystemExit(
        "Dependances manquantes. Installez-les avec :\n"
        "python -m pip install playwright python-dotenv fastapi uvicorn"
        "Puis installez le navigateur : python -m playwright install chromium"
    ) from exc

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

LOGIN_URL = "https://www.ecoledirecte.com/login"
HOME_URL_FRAGMENT = "/Eleves/8602"
os.chdir(BASE_DIR)
TOKEN_SAVED_FILE = BASE_DIR / "ed_session_config.json"
EXPORT_DIR = BASE_DIR / "exports"
REQUEST_TIMEOUT = 30000
API_BASE_URL = "https://api.ecoledirecte.com/v3"
REFERENCE_DATE = date(2026, 5, 23)
HOMEWORK_DAYS_AHEAD = 7
DATA_JSON_PATH = BASE_DIR / "data.json"
app = FastAPI(title="ed_extract-api")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

DOUBLE_AUTH_ANSWERS = {
    "quelle est votre date de naissance ?": "21/11/2011",
    "quelle est votre classe ?": "310",
    "quel est votre mois de naissance ?": "novembre",
    "QUELLE EST VOTRE ANNÉE DE NAISSANCE ?": "2011",
    "quel est votre jour de naissance ?": "21",
    "quel est le nom de famille de votre professeur principal ?": "GRACIA M.",
}

MONTHS_MAPPING = {
    "janvier": 0,
    "fevrier": 1,
    "février": 1,
    "mars": 2,
    "avril": 3,
    "mai": 4,
    "juin": 5,
    "juillet": 6,
    "aout": 7,
    "août": 7,
    "septembre": 8,
    "octobre": 9,
    "novembre": 10,
    "decembre": 11,
    "décembre": 11,
}


def _require_api_key(api_key: str | None) -> None:
    expected_api_key = os.getenv("ED_API_KEY", "").strip() or os.getenv("API_KEY", "").strip()

    if not expected_api_key:
        raise HTTPException(status_code=500, detail="La variable d'environnement ED_API_KEY n'est pas configurée.")

    if not api_key or api_key != expected_api_key:
        raise HTTPException(status_code=401, detail="Clé API invalide ou absente.")


def _resolve_data_source() -> Path | None:
    candidates = sorted(
        BASE_DIR.glob("data_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return candidates[0]

    if DATA_JSON_PATH.exists() and DATA_JSON_PATH.stat().st_size > 0:
        return DATA_JSON_PATH

    if not EXPORT_DIR.exists():
        return None

    exports = sorted(
        EXPORT_DIR.glob("ecoledirecte_export_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return exports[0] if exports else None


def _run_data_extract() -> Path:
    try:
        import data_extract
    except ImportError as exc:
        raise RuntimeError("Impossible d'importer data_extract.py") from exc

    os.chdir(BASE_DIR)
    data_extract.main()

    generated_file = BASE_DIR / data_extract.OUTPUT_DATA_FILE
    if not generated_file.exists() or generated_file.stat().st_size == 0:
        raise RuntimeError("data_extract.py n'a pas généré le fichier JSON attendu.")

    new_file = BASE_DIR / f"data_{time.strftime('%Y%m%d_%H%M%S')}.json"
    new_file.write_text(generated_file.read_text(encoding="utf-8"), encoding="utf-8")
    return new_file


def _run_full_extraction() -> Path:
    """Force l'authentification live et génère un nouveau fichier JSON d'extraction."""
    os.chdir(BASE_DIR)
    username, password = load_credentials()
    manager = EcoleDirecteSessionManager(username, password)
    manager.run_auth_workflow()

    output_path = _run_data_extract()
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("Aucune donnée JSON n'a été générée après l'extraction live.")
    return output_path


@app.get("/data")
def get_data(api_key: str | None = Header(default=None, alias="API_KEY")) -> dict[str, Any]:
    _require_api_key(api_key)

    try:
        output_path = _run_full_extraction()
        return Response(output_path.read_text(encoding="utf-8"), media_type="application/json")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"L'extraction a échoué : {exc}") from exc


@app.post("/trigger-extraction")
def trigger_extraction(api_key: str | None = Header(default=None, alias="API_KEY")) -> Response:
    _require_api_key(api_key)

    try:
        output_path = _run_full_extraction()
        return Response(output_path.read_text(encoding="utf-8"), media_type="application/json")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"L'extraction a échoué : {exc}") from exc


class EcoleDirecteAuthError(RuntimeError):
    pass


class EcoleDirecteExtractor:
    def __init__(self, page: Page, session_config: dict[str, Any], account_id: str) -> None:
        self.page = page
        self.session_config = session_config
        self.account_id = account_id
        self.headers = session_config["headers"]
        self.sections: dict[str, Any] = {}
        self.failures: list[dict[str, Any]] = []
        self.network_by_page: dict[str, list[dict[str, Any]]] = {}
        self.current_section = "initial"
        self.page.on("response", self._handle_api_response)

    def _human_pause(self, min_ms: int = 700, max_ms: int = 1800) -> None:
        time.sleep(random.randint(min_ms, max_ms) / 1000.0)

    def _handle_api_response(self, response: Response) -> None:
        if "api.ecoledirecte.com" not in response.url:
            return

        entry: dict[str, Any] = {
            "url": response.url,
            "status": response.status,
        }

        try:
            content_type = response.headers.get("content-type", "")
            if "json" in content_type or ".awp" in response.url:
                body_text = response.text()
                try:
                    entry["json"] = json.loads(body_text)
                except json.JSONDecodeError:
                    entry["text"] = body_text[:5000]
        except Exception as exc:
            entry["error"] = str(exc)

        self.network_by_page.setdefault(self.current_section, []).append(entry)

    def _post_api(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{API_BASE_URL}{path}"
        response = self.page.request.post(
            url,
            headers=self.headers,
            form={"data": json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))},
            timeout=REQUEST_TIMEOUT,
        )
        text = response.text()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {"raw_text": text}

        if not response.ok:
            raise RuntimeError(f"HTTP {response.status}: {text[:500]}")

        return data

    def _try_api_section(self, name: str, path: str, payload: dict[str, Any] | None = None) -> Any | None:
        print(f"[extract] {name}...")
        try:
            data = self._post_api(path, payload)
            if isinstance(data, dict) and data.get("code") not in (None, 200):
                raise RuntimeError(f"Code EcoleDirecte {data.get('code')}: {data.get('message', data)}")
            self.sections.setdefault("api_directe", {})[name] = data
            return data
        except Exception as exc:
            self.failures.append({"section": name, "path": path, "error": str(exc)})
            print(f"[extract] {name}: ignore ({exc})")
            return None

    def _date_range_payload(self, before_days: int, after_days: int) -> dict[str, str]:
        today = date.today()
        return {
            "dateDebut": (today - timedelta(days=before_days)).isoformat(),
            "dateFin": (today + timedelta(days=after_days)).isoformat(),
        }

    def _extract_current_page_text(self) -> None:
        try:
            self.sections["page_accueil_texte_visible"] = {
                "url": self.page.url,
                "title": self.page.title(),
                "text": self.page.locator("body").inner_text(timeout=5000),
            }
        except Exception as exc:
            self.failures.append({"section": "page_accueil_texte_visible", "path": self.page.url, "error": str(exc)})

    def _visible_text_snapshot(self, section_name: str) -> dict[str, Any]:
        text_before_scroll = self.page.locator("body").inner_text(timeout=7000)
        self.page.mouse.wheel(0, 900)
        self._human_pause(400, 900)
        text_after_scroll = self.page.locator("body").inner_text(timeout=7000)
        self.page.keyboard.press("Home")
        self._human_pause(300, 700)

        return {
            "section": section_name,
            "url": self.page.url,
            "title": self.page.title(),
            "text": text_after_scroll if len(text_after_scroll) > len(text_before_scroll) else text_before_scroll,
            "network_api_responses": self.network_by_page.get(section_name, []),
        }

    def _successful_network_json(self, section_name: str, url_part: str | None = None) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        for response in self.network_by_page.get(section_name, []):
            if url_part and url_part not in response.get("url", ""):
                continue
            payload = response.get("json")
            if isinstance(payload, dict) and payload.get("code") == 200:
                matches.append(payload)
        return matches

    def _extract_dom_structure(self) -> dict[str, Any]:
        return self.page.evaluate(
            """
            () => {
                const textOf = element => (element.innerText || element.textContent || "").trim();
                const tables = [...document.querySelectorAll("table")].map((table, tableIndex) => ({
                    tableIndex,
                    headers: [...table.querySelectorAll("thead th, thead td")].map(textOf),
                    rows: [...table.querySelectorAll("tbody tr, tr")].map(row =>
                        [...row.querySelectorAll("th, td")].map(textOf).filter(Boolean)
                    ).filter(row => row.length)
                }));
                const controls = [...document.querySelectorAll("button, a, [role='button'], input, textarea, select")]
                    .map((element, index) => ({
                        index,
                        tag: element.tagName.toLowerCase(),
                        role: element.getAttribute("role") || "",
                        text: textOf(element),
                        ariaLabel: element.getAttribute("aria-label") || "",
                        title: element.getAttribute("title") || "",
                        placeholder: element.getAttribute("placeholder") || "",
                        value: element.value || "",
                        visible: Boolean(element.offsetWidth || element.offsetHeight || element.getClientRects().length)
                    }))
                    .filter(item => item.visible && (item.text || item.ariaLabel || item.title || item.placeholder || item.value));
                return { tables, controls };
            }
            """
        )

    def _click_text_if_visible(self, label: str, exact: bool = False, timeout: int = 2500) -> bool:
        locator = self.page.get_by_text(label, exact=exact).first
        try:
            locator.wait_for(state="visible", timeout=timeout)
            locator.scroll_into_view_if_needed()
            self._human_pause(300, 900)
            locator.click()
            try:
                self.page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                self._human_pause(900, 1500)
            self._human_pause(700, 1400)
            return True
        except Exception:
            return False

    def _click_first_available_text(self, labels: list[str], exact: bool = False, timeout: int = 2500) -> bool:
        for label in labels:
            if self._click_text_if_visible(label, exact=exact, timeout=timeout):
                return True
        return False

    def _safe_snapshot(self, section_name: str) -> dict[str, Any]:
        snapshot = self._visible_text_snapshot(section_name)
        snapshot["dom_structure"] = self._extract_dom_structure()
        return snapshot

    def _click_menu_section(self, section_name: str, label: str) -> None:
        print(f"[ui] Ouverture de '{label}'...")
        self.current_section = section_name
        self.network_by_page[section_name] = []

        locator = self.page.get_by_text(label, exact=True).first
        locator.wait_for(state="visible", timeout=10000)
        locator.scroll_into_view_if_needed()
        self._human_pause()
        locator.click()

        try:
            self.page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            self._human_pause(1500, 2500)

        self._human_pause(1000, 2200)
        self.sections.setdefault("pages_interface", {})[section_name] = self._visible_text_snapshot(section_name)
        self.sections["pages_interface"][section_name]["dom_structure"] = self._extract_dom_structure()

    def _build_structured_notes(self) -> None:
        payloads = self._successful_network_json("notes", "notes.awp")
        if not payloads:
            self.failures.append(
                {
                    "section": "notes_structurees",
                    "path": "network notes.awp",
                    "error": "Aucune reponse notes.awp code 200 capturee pendant l'ouverture UI de Notes.",
                }
            )
            return

        data = payloads[-1].get("data", {})
        notes = data.get("notes", []) if isinstance(data, dict) else []
        periodes = data.get("periodes", []) if isinstance(data, dict) else []
        notes_by_period: dict[str, list[dict[str, Any]]] = {}
        notes_by_subject: dict[str, list[dict[str, Any]]] = {}

        for note in notes:
            if not isinstance(note, dict):
                continue
            notes_by_period.setdefault(str(note.get("codePeriode", "sans_periode")), []).append(note)
            notes_by_subject.setdefault(str(note.get("libelleMatiere", "sans_matiere")), []).append(note)

        self.sections["notes_structurees"] = {
            "source": "notes.awp capture pendant la navigation normale vers la page Notes",
            "total_notes": len(notes),
            "periodes": periodes,
            "notes": notes,
            "notes_par_periode": notes_by_period,
            "notes_par_matiere": notes_by_subject,
            "parametrage": data.get("parametrage") if isinstance(data, dict) else None,
            "competences_lsun": data.get("LSUN") if isinstance(data, dict) else None,
        }

    def _extract_notes_deep(self) -> None:
        try:
            self._click_menu_section("notes", "Notes")
            tabs = [
                ("notes_1er_trimestre", ["1er Trimestre"]),
                ("notes_2eme_trimestre", ["2ème Trimestre", "2eme Trimestre"]),
                ("notes_3eme_trimestre", ["3ème Trimestre", "3eme Trimestre"]),
                ("notes_evaluations", ["Évaluations", "Evaluations"]),
                ("notes_moyennes", ["Moyennes"]),
                ("notes_competences", ["Compétences", "Competences"]),
                ("notes_graphiques", ["Graphiques"]),
            ]

            for section_name, labels in tabs:
                self.current_section = section_name
                self.network_by_page[section_name] = []
                if self._click_first_available_text(labels, exact=True):
                    self.sections.setdefault("notes_vues_detaillees", {})[section_name] = self._safe_snapshot(section_name)
            self._build_structured_notes()
        except Exception as exc:
            self.failures.append({"section": "notes_detaillees", "path": "Notes", "error": str(exc)})

    def _message_items_from_network(self, section_name: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        seen: set[str] = set()
        for payload in self._successful_network_json(section_name, "messages.awp"):
            data = payload.get("data", {})
            message_groups = data.get("messages", {}) if isinstance(data, dict) else {}
            if isinstance(message_groups, dict):
                iterable = [item for group in message_groups.values() if isinstance(group, list) for item in group]
            elif isinstance(message_groups, list):
                iterable = message_groups
            else:
                iterable = []

            for item in iterable:
                if not isinstance(item, dict):
                    continue
                message_id = str(item.get("id", ""))
                if message_id and message_id not in seen:
                    seen.add(message_id)
                    messages.append(item)
        return messages

    def _open_visible_messages(self, folder_name: str, messages: list[dict[str, Any]], limit: int) -> None:
        details: list[dict[str, Any]] = []

        for message in messages[:limit]:
            subject = str(message.get("subject", "")).strip()
            if not subject:
                continue

            detail_section = f"message_{folder_name}_{message.get('id', len(details))}"
            self.current_section = detail_section
            self.network_by_page[detail_section] = []

            if not self._click_text_if_visible(subject, exact=False, timeout=1800):
                continue

            details.append(
                {
                    "metadata": message,
                    "snapshot": self._visible_text_snapshot(detail_section),
                    "dom_structure": self._extract_dom_structure(),
                }
            )

        self.sections.setdefault("messagerie_details_ui", {})[folder_name] = details

    def _prepare_optional_draft(self) -> None:
        if os.getenv("ED_CREATE_DRAFT", "").lower() not in {"1", "true", "yes", "oui"}:
            self.sections["messagerie_brouillon_creation"] = {
                "enabled": False,
                "reason": "Definis ED_CREATE_DRAFT=true, ED_DRAFT_RECIPIENT, ED_DRAFT_SUBJECT et ED_DRAFT_BODY pour creer un brouillon.",
            }
            return

        recipient = os.getenv("ED_DRAFT_RECIPIENT", "").strip()
        subject = os.getenv("ED_DRAFT_SUBJECT", "").strip()
        body = os.getenv("ED_DRAFT_BODY", "").strip()
        result: dict[str, Any] = {"enabled": True, "created": False, "recipient": recipient, "subject": subject}
        if not recipient or not subject or not body:
            result["reason"] = "Variables ED_DRAFT_RECIPIENT, ED_DRAFT_SUBJECT ou ED_DRAFT_BODY manquantes."
            self.sections["messagerie_brouillon_creation"] = result
            return

        try:
            if not self._click_text_if_visible("Nouveau message", exact=True, timeout=5000):
                raise RuntimeError("Bouton 'Nouveau message' introuvable.")

            self.page.keyboard.type(recipient, delay=20)
            self._human_pause(400, 900)
            self.page.keyboard.press("Enter")

            for selector in ["input[placeholder*='Objet']", "input[name*='subject']", "input"]:
                candidate = self.page.locator(selector).last
                if candidate.count() > 0 and candidate.is_visible():
                    candidate.fill(subject)
                    break

            body_filled = False
            for selector in ["textarea", "[contenteditable='true']", ".ql-editor"]:
                candidate = self.page.locator(selector).first
                if candidate.count() > 0 and candidate.is_visible():
                    if selector == "textarea":
                        candidate.fill(body)
                    else:
                        candidate.click()
                        self.page.keyboard.type(body, delay=10)
                    body_filled = True
                    break
            if not body_filled:
                raise RuntimeError("Zone de texte du message introuvable.")

            result["created"] = self._click_text_if_visible("Enregistrer", exact=False, timeout=3000)
            result["note"] = "Le script ne clique jamais sur Envoyer. Il tente seulement de sauvegarder un brouillon."
        except Exception as exc:
            result["error"] = str(exc)

        self.sections["messagerie_brouillon_creation"] = result

    def _extract_messaging_deep(self) -> None:
        try:
            self._click_menu_section("messagerie", "Messagerie")
            folders = [
                ("boite_reception", "Boîte de réception", 2),
                ("brouillons", "Brouillons", 1),
            ]

            for folder_name, label, limit in folders:
                section_name = f"messagerie_{folder_name}"
                self.current_section = section_name
                self.network_by_page[section_name] = []
                self._click_text_if_visible(label, exact=False, timeout=4000)
                snapshot = self._safe_snapshot(section_name)
                messages = self._message_items_from_network(section_name)
                if folder_name == "boite_reception" and not messages:
                    messages = self._message_items_from_network("messagerie")
                snapshot["messages_liste_api"] = messages
                self.sections.setdefault("messagerie_dossiers", {})[folder_name] = snapshot
                self._open_visible_messages(folder_name, messages, limit)
        except Exception as exc:
            self.failures.append({"section": "messagerie_detaillee", "path": "Messagerie", "error": str(exc)})

    def _extract_vie_scolaire_deep(self) -> None:
        try:
            self._click_menu_section("vie_scolaire", "Vie scolaire")
            self.sections["vie_scolaire_detaillee"] = {
                "snapshot_initial": self._safe_snapshot("vie_scolaire"),
                "onglets": {},
            }

            for label in ["Absences", "Retards", "Punitions", "Sanctions", "Carnet", "Infirmerie"]:
                section_name = f"vie_scolaire_{label.lower()}"
                self.current_section = section_name
                self.network_by_page[section_name] = []
                if self._click_text_if_visible(label, exact=False, timeout=2000):
                    self.sections["vie_scolaire_detaillee"]["onglets"][section_name] = self._safe_snapshot(section_name)
        except Exception as exc:
            self.failures.append({"section": "vie_scolaire_detaillee", "path": "Vie scolaire", "error": str(exc)})

    def _format_french_day_labels(self, target: date) -> list[str]:
        day_names = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
        month_names = [
            "janvier",
            "février",
            "mars",
            "avril",
            "mai",
            "juin",
            "juillet",
            "août",
            "septembre",
            "octobre",
            "novembre",
            "décembre",
        ]
        labels = [
            target.strftime("%d/%m/%Y"),
            target.strftime("%d/%m"),
            f"{target.day} {month_names[target.month - 1]}",
            f"{day_names[target.weekday()]} {target.day} {month_names[target.month - 1]}",
        ]
        if target == REFERENCE_DATE:
            labels.insert(0, "Aujourd'hui")
        if target == REFERENCE_DATE + timedelta(days=1):
            labels.insert(0, "Demain")
        return labels

    def _collect_dicts_with_dates(self, value: Any) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        if isinstance(value, dict):
            if any("date" in str(key).lower() for key in value):
                found.append(value)
            for item in value.values():
                found.extend(self._collect_dicts_with_dates(item))
        elif isinstance(value, list):
            for item in value:
                found.extend(self._collect_dicts_with_dates(item))
        return found

    def _first_date_in_dict(self, item: dict[str, Any]) -> date | None:
        for key, value in item.items():
            if "date" not in str(key).lower() or not isinstance(value, str):
                continue
            match = re.search(r"(\d{4})-(\d{2})-(\d{2})", value)
            if match:
                return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        return None

    def _build_structured_homework_from_network(self, result: dict[str, Any]) -> None:
        start = REFERENCE_DATE
        end = REFERENCE_DATE + timedelta(days=HOMEWORK_DAYS_AHEAD)
        candidates: list[dict[str, Any]] = []

        for section_name, responses in self.network_by_page.items():
            if not (section_name.startswith("cahier_de_textes") or section_name.startswith("devoirs_")):
                continue
            for response in responses:
                payload = response.get("json")
                if isinstance(payload, dict):
                    for item in self._collect_dicts_with_dates(payload.get("data", payload)):
                        item_date = self._first_date_in_dict(item)
                        if item_date and start <= item_date <= end:
                            candidates.append(item)

        result["devoirs_structures_capture_reseau"] = candidates
        result["total_devoirs_structures_detectes"] = len(candidates)

    def _extract_homework_week(self) -> None:
        start = REFERENCE_DATE
        end = REFERENCE_DATE + timedelta(days=HOMEWORK_DAYS_AHEAD)
        result: dict[str, Any] = {
            "periode": {
                "date_debut": start.isoformat(),
                "date_fin": end.isoformat(),
                "jours_inclus": HOMEWORK_DAYS_AHEAD + 1,
            },
            "jours": {},
        }

        try:
            self._click_menu_section("cahier_de_textes", "Cahier de textes")
            result["snapshot_initial"] = self._safe_snapshot("cahier_de_textes")

            for offset in range(HOMEWORK_DAYS_AHEAD + 1):
                target = start + timedelta(days=offset)
                section_name = f"devoirs_{target.isoformat()}"
                self.current_section = section_name
                self.network_by_page[section_name] = []

                opened = self._click_first_available_text(self._format_french_day_labels(target), exact=False, timeout=1800)
                snapshot = self._safe_snapshot(section_name)
                snapshot["opened_by_click"] = opened

                # Ouvre les premiers devoirs visibles du jour si l'interface les liste sous forme cliquable.
                opened_items: list[dict[str, Any]] = []
                for keyword in ["A faire", "À faire", "Devoir", "Travail", "Exercice", "Contrôle", "Evaluation", "Évaluation"]:
                    detail_section = f"{section_name}_{keyword.lower().replace(' ', '_')}"
                    self.current_section = detail_section
                    self.network_by_page[detail_section] = []
                    if self._click_text_if_visible(keyword, exact=False, timeout=800):
                        opened_items.append({"keyword": keyword, "snapshot": self._safe_snapshot(detail_section)})
                        if len(opened_items) >= 5:
                            break

                snapshot["devoirs_ouverts"] = opened_items
                result["jours"][target.isoformat()] = snapshot

            self._build_structured_homework_from_network(result)
            self.sections["devoirs_semaine_a_venir"] = result
        except Exception as exc:
            self.sections["devoirs_semaine_a_venir"] = result
            self.failures.append({"section": "devoirs_semaine_a_venir", "path": "Cahier de textes", "error": str(exc)})

    def _extract_ui_pages(self) -> None:
        pages = [
            ("accueil", "Accueil"),
            ("vie_scolaire", "Vie scolaire"),
            ("vie_de_la_classe", "Vie de la classe"),
            ("emploi_du_temps", "Emploi du temps"),
            ("cahier_de_textes", "Cahier de textes"),
            ("manuels_ressources", "Manuels & ressources"),
            ("qcm", "QCM"),
            ("formulaires_sondages", "Formulaires et sondages"),
            ("documents", "Documents"),
            ("espaces_de_travail", "Espaces de travail"),
            ("cloud", "Mon cloud"),
            ("mes_applis", "Mes Applis"),
        ]

        for section_name, label in pages:
            try:
                self._click_menu_section(section_name, label)
            except Exception as exc:
                self.failures.append({"section": f"ui_{section_name}", "path": label, "error": str(exc)})
                print(f"[ui] {label}: ignore ({exc})")

    def _collect_ids(self, value: Any, key_names: set[str] | None = None) -> set[str]:
        key_names = key_names or {"id", "idMessage", "idmessage", "messageId", "message_id"}
        ids: set[str] = set()

        if isinstance(value, dict):
            for key, item in value.items():
                if key in key_names and isinstance(item, (int, str)):
                    ids.add(str(item))
                else:
                    ids.update(self._collect_ids(item, key_names))
        elif isinstance(value, list):
            for item in value:
                ids.update(self._collect_ids(item, key_names))

        return ids

    def _extract_message_details(self) -> None:
        api_directe = self.sections.get("api_directe", {})
        message_sections = {
            key: value for key, value in api_directe.items() if key.startswith("messagerie_") and isinstance(value, (dict, list))
        }
        message_ids = sorted({message_id for data in message_sections.values() for message_id in self._collect_ids(data)})

        if not message_ids:
            self.sections["messagerie_details"] = {}
            return

        details: dict[str, Any] = {}
        print(f"[extract] messagerie_details ({len(message_ids)} messages detectes)...")
        for message_id in message_ids:
            try:
                details[message_id] = self._post_api(
                    f"/eleves/{self.account_id}/messages/{message_id}.awp?verbe=get"
                )
            except Exception as exc:
                self.failures.append(
                    {
                        "section": "messagerie_details",
                        "path": f"/eleves/{self.account_id}/messages/{message_id}.awp?verbe=get",
                        "error": str(exc),
                    }
                )

        self.sections["messagerie_details"] = details

    def extract_all(self) -> Path:
        print("\n[>>] Extraction ciblee des donnees EcoleDirecte...")
        self._extract_current_page_text()
        self._extract_notes_deep()
        self._extract_vie_scolaire_deep()
        self._extract_messaging_deep()
        self._extract_homework_week()

        export = {
            "format": "ecoledirecte_export_ai_ready",
            "version": 1,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "source": {
                "site": "EcoleDirecte",
                "home_url": self.page.url,
                "account_id": self.account_id,
                "reference_date": REFERENCE_DATE.isoformat(),
            },
            "notice": (
                "Export JSON cible pour lecture par IA : notes, vie scolaire, deux derniers messages, "
                "dernier brouillon et devoirs de la semaine a venir."
            ),
            "donnees": self.sections,
            "erreurs_extraction": self.failures,
        }

        EXPORT_DIR.mkdir(exist_ok=True)
        output_path = EXPORT_DIR / f"ecoledirecte_export_{time.strftime('%Y%m%d_%H%M%S')}.json"
        output_path.write_text(json.dumps(export, indent=2, ensure_ascii=False), encoding="utf-8")
        return output_path.resolve()


class EcoleDirecteSessionManager:
    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self.extracted_token: str | None = None
        self.extracted_headers: dict[str, str] = {}

    def _human_delay(self, min_ms: int = 1000, max_ms: int = 2000) -> None:
        time.sleep(random.randint(min_ms, max_ms) / 1000.0)

    def _handle_response(self, response: Response) -> None:
        if "api.ecoledirecte.com" not in response.url:
            return

        headers = response.request.headers
        token = headers.get("x-token")
        if not token or token == self.extracted_token:
            return

        self.extracted_token = token
        self.extracted_headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "X-Token": token,
            "Content-Type": "application/x-www-form-urlencoded",
        }

        if "x-gtk" in headers:
            self.extracted_headers["X-Gtk"] = headers["x-gtk"]

    def _wait_for_login_result(self, page: Page) -> None:
        page.wait_for_function(
            """
            homeUrlFragment => {
                const dashboardSelector = ".home-page, .dashboard, .main-content, #page-accueil";
                return Boolean(
                    document.querySelector("#formQuestions2FA")
                    || document.querySelector(dashboardSelector)
                    || location.href.includes(homeUrlFragment)
                );
            }
            """,
            arg=HOME_URL_FRAGMENT,
            timeout=15000,
        )

    def _wait_for_homepage(self, page: Page) -> None:
        dashboard_selector = ".home-page, .dashboard, .main-content, #page-accueil"
        try:
            page.wait_for_url(f"**{HOME_URL_FRAGMENT}**", timeout=15000)
        except Exception:
            page.wait_for_selector(dashboard_selector, timeout=15000)

    def _get_account_id(self, page: Page) -> str:
        match = re.search(r"/Eleves/(\d+)", page.url)
        if match:
            return match.group(1)
        match = re.search(r"/Eleves/(\d+)", HOME_URL_FRAGMENT)
        if match:
            return match.group(1)
        raise EcoleDirecteAuthError(f"Impossible de trouver l'id eleve depuis l'URL : {page.url}")

    def _solve_2fa_ui(self, page: Page) -> None:
        print("\n[Action] Double authentification detectee.")
        page.wait_for_selector("#formQuestions2FA", state="visible", timeout=10000)
        page.wait_for_function(
            """
            () => {
                const title = document.querySelector("#formQuestions2FA h3.mt-0");
                return Boolean(title && title.innerText.trim().length > 0);
            }
            """,
            timeout=5000,
        )

        raw_question = page.locator("#formQuestions2FA h3.mt-0").first.inner_text().strip()
        cleaned_question = raw_question.lower()
        print(f"Question posee par EcoleDirecte : {raw_question}")

        answer = DOUBLE_AUTH_ANSWERS.get(cleaned_question)
        if not answer:
            answer = next(
                (
                    value
                    for key, value in DOUBLE_AUTH_ANSWERS.items()
                    if key in cleaned_question or cleaned_question in key
                ),
                None,
            )

        if not answer:
            print("\n[!] Reponse automatique introuvable.")
            answer = input("Veuillez taper la reponse attendue : ").strip()
            if not answer:
                raise EcoleDirecteAuthError("Aucune reponse fournie. Abandon.")

        self._human_delay(800, 1500)
        cleaned_answer = answer.lower().strip()

        if "mois de naissance" in cleaned_question and cleaned_answer in MONTHS_MAPPING:
            month_index = MONTHS_MAPPING[cleaned_answer]
            radio_selector = f"#formQuestions2FA #prop_{month_index}"
            print(f"Selection du bouton radio {radio_selector} pour '{answer}'...")
            page.locator(radio_selector).check()
        else:
            print(f"Recherche de l'option : '{answer}'")
            target_label = page.locator("#formQuestions2FA label").get_by_text(answer, exact=False)
            if target_label.count() == 0:
                raise EcoleDirecteAuthError(f"Option de reponse introuvable pour : {answer}")

            input_id = target_label.first.get_attribute("for")
            if input_id:
                page.locator(f"#formQuestions2FA input#{input_id}").check()
            else:
                target_label.first.click()

        remember_me_checkbox = page.locator("#formQuestions2FA #saveAppareil")
        if remember_me_checkbox.count() > 0 and remember_me_checkbox.is_visible():
            remember_me_checkbox.check()

        self._human_delay(1000, 1800)
        submit_button = page.locator("#formQuestions2FA button[type='submit']").first
        if submit_button.count() == 0:
            raise EcoleDirecteAuthError("Bouton de validation 2FA introuvable.")
        if submit_button.is_disabled():
            raise EcoleDirecteAuthError("Le bouton de validation 2FA est reste bloque.")

        print("Soumission de la reponse 2FA...")
        submit_button.click()
        print("[OK] Reponse 2FA soumise.")

    def run_auth_workflow(self) -> dict[str, Any]:
        with sync_playwright() as p:
            print("Lancement du navigateur...")
            browser: Browser = p.chromium.launch(headless=False)
            context: BrowserContext = browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1280, "height": 720},
                locale="fr-FR",
                timezone_id="Europe/Paris",
            )
            context.set_default_timeout(REQUEST_TIMEOUT)

            page: Page = context.new_page()
            page.on("response", self._handle_response)

            try:
                print(f"Navigation vers {LOGIN_URL}...")
                page.goto(LOGIN_URL, wait_until="networkidle")
                self._human_delay(1000, 1800)

                print("Saisie des identifiants...")
                page.locator("#username").first.fill(self.username)
                self._human_delay(500, 1000)
                page.locator("#password").first.fill(self.password)
                self._human_delay(600, 1200)

                print("Clic sur 'Se connecter'...")
                page.locator("#connexion").first.click()

                print("Detection de la 2FA ou de l'accueil...")
                self._wait_for_login_result(page)

                if page.locator("#formQuestions2FA").count() > 0:
                    self._solve_2fa_ui(page)
                    print("2FA validee. Attente de l'accueil...")
                    self._wait_for_homepage(page)
                else:
                    print("[OK] Accueil atteint sans double authentification.")

                self._human_delay(1500, 2500)

                if not self.extracted_token:
                    raise EcoleDirecteAuthError("Accueil atteint, mais aucun X-Token n'a ete intercepte.")

                account_id = self._get_account_id(page)
                session_config = {
                    "status": "success",
                    "extracted_at": time.time(),
                    "account_id": account_id,
                    "headers": self.extracted_headers,
                    "token": self.extracted_token,
                }

                TOKEN_SAVED_FILE.write_text(
                    json.dumps(session_config, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                print(f"[OK] Session sauvegardee dans : {TOKEN_SAVED_FILE.resolve()}")

                extractor = EcoleDirecteExtractor(page, session_config, account_id)
                output_path = extractor.extract_all()
                print(f"\n[OK] Export termine : {output_path}")

                return session_config
            finally:
                print("Fermeture du navigateur...")
                context.close()
                browser.close()


def load_credentials() -> tuple[str, str]:
    load_dotenv()
    username = os.getenv("ED_USERNAME")
    password = os.getenv("ED_PASSWORD")
    if not username or not password:
        raise EcoleDirecteAuthError("Variables ED_USERNAME et/ou ED_PASSWORD manquantes dans .env.")
    return username, password


def run_extraction() -> int:
    try:
        username, password = load_credentials()
        manager = EcoleDirecteSessionManager(username, password)
        manager.run_auth_workflow()

        if not _run_data_extract():
            return 1

        return 0
    except Exception as exc:
        print(f"\n[ECHEC] Erreur d'execution : {exc}", file=sys.stderr)
        return 1


def main() -> int:
    return run_extraction()


if __name__ == "__main__":
    if "--run" in sys.argv:
        sys.exit(run_extraction())

    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)
