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


# ---------------------------------------------------------------- branding


def test_the_logo_and_favicon_are_served(api):
    for path, expected in (("/logo.png", "image/png"),
                           ("/favicon.png", "image/png"),
                           ("/favicon.ico", "image/png")):
        response = api.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"] == expected
        assert len(response.content) > 1000, f"{path} looks empty"


def test_the_page_uses_the_logo_not_an_emoji(api):
    body = api.get("/").text
    assert 'src="/logo.png"' in body
    assert 'href="/favicon.png"' in body


def test_the_theme_is_built_on_the_greek_palette(api):
    body = api.get("/").text
    assert "--greek-blue:#0d5eaf" in body, "the flag blue should be a named token"
    assert "--gold:" in body
    assert "meander" in body, "the Greek key strip is part of the identity"


# ------------------------------------------------- mailboxes made on the fly


def test_sending_to_an_unknown_address_creates_it_and_shows_it(api, unique):
    """The mailbox has to appear in the only view that lists mailboxes."""
    address = f"nobody-{unique}@tahidromos.test"
    before = api.get("/overview").json()
    assert not any(box["address"] == address
                   for app in before["apps"] for box in app["mailboxes"])

    sent = api.post("/send", {"from": "alice", "to": address,
                              "subject": f"Made on the fly {unique}", "text": "hello"})
    assert sent.status_code == 201
    assert address in sent.json()["accounts_created"]

    delivered = api.post("/wait", {"user": address,
                                   "message_id": sent.json()["message_id"], "timeout": 30})
    assert delivered.status_code == 200

    after = api.get("/overview").json()
    found = [box for app in after["apps"] for box in app["mailboxes"]
             if box["address"] == address]
    assert found, "the new mailbox is missing from the sidebar"
    assert found[0]["created_on_demand"] is True
    assert found[0]["total"] == 1 and found[0]["unread"] == 1


def test_an_on_demand_mailbox_joins_the_app_that_owns_its_domain(api, unique):
    address = f"walkin-{unique}@shop.test"
    api.post("/send", {"from": "alice", "to": address,
                       "subject": "cross domain", "text": "hi"})
    api.post("/wait", {"user": address, "subject_contains": "cross domain", "timeout": 30})

    overview = api.get("/overview").json()
    owner = [app["name"] for app in overview["apps"]
             for box in app["mailboxes"] if box["address"] == address]
    assert owner == ["shop"], f"grouped under {owner} rather than the shop app"


def test_configured_mailboxes_are_not_labelled_as_on_demand(api):
    overview = api.get("/overview").json()
    by_address = {box["address"]: box
                  for app in overview["apps"] for box in app["mailboxes"]}

    for address in ("alice@tahidromos.test", "echo@tahidromos.test",
                    "captured@tahidromos.test", "support@shop.test"):
        assert by_address[address]["created_on_demand"] is False, address

    # an app's SMTP login is configured, not conjured
    assert by_address["shop@shop.test"]["is_credential"] is True
    assert by_address["shop@shop.test"]["created_on_demand"] is False


def test_the_capture_mailbox_is_still_marked_after_the_merge(api):
    overview = api.get("/overview").json()
    capture = [box for app in overview["apps"] for box in app["mailboxes"]
               if box["is_capture"]]
    assert len(capture) == 1
    assert capture[0]["address"] == overview["capture_address"]
