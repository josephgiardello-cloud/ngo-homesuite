from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


@pytest.fixture()
def client(app):
    return app.test_client()



def test_set_locale_persists_supported_language(client):
    rv = client.post("/locale/es?next=/help")
    assert rv.status_code == 302
    assert rv.headers["Location"].endswith("/help")

    with client.session_transaction() as sess:
        assert sess.get("lang") == "es"



def test_set_locale_ignores_unsupported_language(client):
    with client.session_transaction() as sess:
        sess["lang"] = "en"

    rv = client.post("/locale/de", data={"next": "/about"})
    assert rv.status_code == 302
    assert rv.headers["Location"].endswith("/about")

    with client.session_transaction() as sess:
        assert sess.get("lang") == "en"



def test_set_locale_blocks_external_redirect_targets(client):
    rv = client.post("/locale/fr?next=https://evil.example/phish")
    assert rv.status_code == 302
    assert rv.headers["Location"].endswith("/")
