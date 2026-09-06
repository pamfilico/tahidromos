"""Email templates: they render, they substitute, and they are sendable."""

from __future__ import annotations

import re

import pytest
import requests

from conftest import API_URL, MailClient

EXPECTED = {"welcome", "otp", "password_reset", "receipt", "digest", "alert",
            "invite", "verify_email"}


@pytest.fixture(scope="module")
def templates():
    return requests.get(f"{API_URL}/templates", timeout=30).json()["templates"]


def test_every_template_is_listed(templates):
    assert EXPECTED <= {t["name"] for t in templates}


def test_both_authoring_paths_are_present(templates):
    sources = {t["source"] for t in templates}
    assert "handwritten" in sources
    assert "react-email" in sources, "the React Email export is missing"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_template_renders_without_leftover_placeholders(name):
    response = requests.get(f"{API_URL}/templates/{name}/preview", timeout=30)
    assert response.status_code == 200
    html = response.text
    assert "<html" in html.lower()
    assert not re.findall(r"\{\{[^}]*\}\}", html), f"{name} has unresolved placeholders"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_template_has_a_plain_text_alternative(name):
    rendered = requests.post(f"{API_URL}/templates/{name}/preview", json={}, timeout=30).json()
    assert rendered["text"].strip(), f"{name} renders no plain text"
    assert "<" not in rendered["text"].replace("<br>", ""), "text part contains markup"
    assert rendered["subject"].strip()
    assert "@" in rendered["from"]


def test_templates_are_fluid_not_fixed_width():
    """A 600px-locked table is unreadable on a phone."""
    html = requests.get(f"{API_URL}/templates/receipt/preview", timeout=30).text
    assert "max-width:600px" in html.replace(" ", "")
    assert 'width="600"' not in html, "the shell should be fluid, not a rigid 600px table"


def test_context_overrides_the_defaults():
    rendered = requests.post(f"{API_URL}/templates/welcome/preview",
                             json={"context": {"name": "Zaphod", "product": "Heart of Gold"}},
                             timeout=30).json()
    assert "Zaphod" in rendered["html"]
    assert "Heart of Gold" in rendered["subject"]


def test_repeating_blocks_expand():
    rendered = requests.post(
        f"{API_URL}/templates/receipt/preview",
        json={"context": {"items": [{"name": "One", "qty": 1, "price": "€1"},
                                    {"name": "Two", "qty": 2, "price": "€2"},
                                    {"name": "Three", "qty": 3, "price": "€3"}]}},
        timeout=30).json()
    for label in ("One", "Two", "Three"):
        assert label in rendered["html"]
    assert rendered["html"].count("€") >= 3


def test_context_is_escaped_so_a_template_cannot_be_injected():
    rendered = requests.post(f"{API_URL}/templates/welcome/preview",
                             json={"context": {"name": "<script>alert(1)</script>"}},
                             timeout=30).json()
    assert "<script>alert(1)</script>" not in rendered["html"]
    assert "&lt;script&gt;" in rendered["html"]


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_template_can_be_delivered_and_read(name, unique):
    address = f"tpl-{name}-{unique}@tahidromos.test"
    requests.post(f"{API_URL}/accounts", json={"address": address}, timeout=30)

    sent = requests.post(f"{API_URL}/templates",
                         json={"name": name, "to": address}, timeout=60)
    assert sent.status_code == 201, sent.text

    delivered = requests.post(f"{API_URL}/wait",
                              json={"user": address, "message_id": sent.json()["message_id"],
                                    "timeout": 30}, timeout=60)
    assert delivered.status_code == 200, delivered.text
    message = delivered.json()

    assert message["html"], f"{name} arrived with no HTML part"
    assert message["text"].strip(), f"{name} arrived with no text part"
    assert "<html" in message["html"].lower()


def test_html_endpoint_serves_the_part_for_an_iframe(unique):
    address = f"tpl-html-{unique}@tahidromos.test"
    requests.post(f"{API_URL}/accounts", json={"address": address}, timeout=30)
    sent = requests.post(f"{API_URL}/templates",
                         json={"name": "receipt", "to": address}, timeout=60).json()
    message = requests.post(f"{API_URL}/wait",
                            json={"user": address, "message_id": sent["message_id"],
                                  "timeout": 30}, timeout=60).json()

    response = requests.get(f"{API_URL}/messages/{address}/{message['uid']}/html", timeout=30)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "AC-10432" in response.text


def test_eml_download_is_a_real_message(unique):
    address = f"tpl-eml-{unique}@tahidromos.test"
    requests.post(f"{API_URL}/accounts", json={"address": address}, timeout=30)
    sent = requests.post(f"{API_URL}/templates",
                         json={"name": "otp", "to": address}, timeout=60).json()
    message = requests.post(f"{API_URL}/wait",
                            json={"user": address, "message_id": sent["message_id"],
                                  "timeout": 30}, timeout=60).json()

    response = requests.get(f"{API_URL}/messages/{address}/{message['uid']}/eml", timeout=30)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("message/rfc822")
    assert "attachment" in response.headers["content-disposition"]
    body = response.text
    assert body.startswith(("Received:", "From:", "Content-Type:", "MIME-Version:"))
    assert "Message-ID:" in body


def test_a_plain_text_message_still_has_an_html_endpoint(alice, unique):
    """The iframe needs something to render even with no HTML part."""
    message_id = alice.send("alice", f"plain only {unique}", "just text")
    received = alice.wait_for_id(message_id)
    uid = None
    listing = requests.get(f"{API_URL}/messages/alice", timeout=30).json()["messages"]
    for item in listing:
        if item["message_id"] == message_id.strip():
            uid = item["uid"]
    assert uid is not None

    response = requests.get(f"{API_URL}/messages/alice/{uid}/html", timeout=30)
    assert response.status_code == 200
    assert "just text" in response.text


def test_unknown_template_is_a_404():
    assert requests.get(f"{API_URL}/templates/nope/preview", timeout=30).status_code == 404
    assert requests.post(f"{API_URL}/templates",
                         json={"name": "nope", "to": "alice"}, timeout=30).status_code == 404
