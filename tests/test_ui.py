"""The mailbox browser served at / — the page, and the data behind its badges."""

from __future__ import annotations

import re

from conftest import DOMAIN, MailClient


def test_ui_page_is_served(api):
    response = api.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "<title>tahidromos</title>" in body
    assert 'id="apps"' in body, "sidebar container missing"
    assert 'id="msgs"' in body, "message list container missing"
    assert 'id="reader"' in body, "reader pane missing"


def test_ui_is_self_contained(api):
    """No CDN, no external assets — it has to work offline."""
    body = api.get("/").text
    external = re.findall(r'(?:src|href)="(https?://[^"]+)"', body)
    assert not external, f"the UI must not load anything remote: {external}"


def test_ui_offers_both_themes(api):
    body = api.get("/").text
    assert '[data-theme="light"]' in body
    assert 'id="theme"' in body


def test_overview_shape_matches_what_the_sidebar_needs(api):
    payload = api.get("/overview").json()
    assert {"apps", "total", "unread", "domains", "generated_at"} <= set(payload)

    for app in payload["apps"]:
        assert {"name", "domain", "mailboxes", "total", "unread"} <= set(app)
        for box in app["mailboxes"]:
            assert {"localpart", "address", "unread", "total", "online", "is_bot"} <= set(box)
            assert isinstance(box["unread"], int) and box["unread"] >= 0
            assert box["unread"] <= box["total"]


def test_overview_totals_are_the_sum_of_their_parts(api):
    payload = api.get("/overview").json()
    for app in payload["apps"]:
        assert app["total"] == sum(b["total"] for b in app["mailboxes"])
        assert app["unread"] == sum(b["unread"] for b in app["mailboxes"])
    assert payload["total"] == sum(a["total"] for a in payload["apps"])
    assert payload["unread"] == sum(a["unread"] for a in payload["apps"])


def test_every_mailbox_is_online(api):
    payload = api.get("/overview").json()
    offline = [b["address"] for a in payload["apps"] for b in a["mailboxes"] if not b["online"]]
    assert not offline, f"unreachable mailboxes: {offline}"


def test_bots_are_flagged_for_the_badge(api):
    payload = api.get("/overview").json()
    bots = [b["address"] for a in payload["apps"] for b in a["mailboxes"] if b["is_bot"]]
    assert f"echo@{DOMAIN}" in bots
    assert any(address.endswith("@shop.test") for address in bots)


def test_openapi_documents_every_endpoint(api):
    schema = api.get("/openapi.json").json()
    paths = set(schema["paths"])
    for expected in ("/health", "/config", "/accounts", "/overview", "/apps",
                     "/send", "/reply", "/wait", "/conversation",
                     "/messages/{user}", "/threads/{user}"):
        assert expected in paths, f"{expected} missing from the OpenAPI schema"


def test_docs_page_loads(api):
    assert api.get("/docs").status_code == 200
