import ed_extract


class FakeRequest:
    def __init__(self, headers):
        self.headers = headers


class FakeResponse:
    def __init__(self, url, headers, request_headers=None):
        self.url = url
        self.headers = headers
        self.request = FakeRequest(request_headers or {})


def test_student_dashboard_url_matches_any_eleve_page():
    assert ed_extract._looks_like_dashboard_url("https://www.ecoledirecte.com/Eleves/8602") is True
    assert ed_extract._looks_like_dashboard_url("https://www.ecoledirecte.com/Eleves/12345/Accueil") is True


def test_student_dashboard_url_rejects_login_page():
    assert ed_extract._looks_like_dashboard_url("https://www.ecoledirecte.com/login") is False
    assert ed_extract._looks_like_dashboard_url("https://www.ecoledirecte.com/forgot-password") is False


def test_session_manager_reads_x_token_from_response_headers():
    manager = ed_extract.EcoleDirecteSessionManager("user", "pass")

    response = FakeResponse(
        "https://api.ecoledirecte.com/v3/Eleves/12345/notes.awp?verbe=get",
        headers={"x-token": "tok-123", "x-gtk": "gtk-456"},
        request_headers={"x-token": "ignored"},
    )

    manager._handle_response(response)

    assert manager.extracted_token == "tok-123"
    assert manager.extracted_headers["X-Token"] == "tok-123"
    assert manager.extracted_headers["X-Gtk"] == "gtk-456"
