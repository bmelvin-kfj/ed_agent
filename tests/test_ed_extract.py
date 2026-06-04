import ed_extract


def test_student_dashboard_url_matches_any_eleve_page():
    assert ed_extract._looks_like_dashboard_url("https://www.ecoledirecte.com/Eleves/8602") is True
    assert ed_extract._looks_like_dashboard_url("https://www.ecoledirecte.com/Eleves/12345/Accueil") is True


def test_student_dashboard_url_rejects_login_page():
    assert ed_extract._looks_like_dashboard_url("https://www.ecoledirecte.com/login") is False
    assert ed_extract._looks_like_dashboard_url("https://www.ecoledirecte.com/forgot-password") is False
