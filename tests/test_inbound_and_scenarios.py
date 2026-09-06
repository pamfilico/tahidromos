"""Disposable inboxes, canned scenarios, extraction, and the inbound webhook."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import requests

from conftest import API_URL, MailClient


# ---------------------------------------------------------------- inboxes


def test_a_disposable_inbox_is_unique_and_usable(unique):
    box = requests.post(f"{API_URL}/inboxes",
                        json={"prefix": "iso", "run_id": f"t-{unique}"}, timeout=30).json()
    assert box["address"].startswith("iso-")
    assert box["address"] != requests.post(
        f"{API_URL}/inboxes", json={"prefix": "iso"}, timeout=30).json()["address"]

    MailClient("alice").send(box["address"], f"hello {unique}", "hi")
    got = requests.post(f"{API_URL}/wait",
                        json={"user": box["address"], "subject_contains": unique,
                              "timeout": 20}, timeout=40)
    assert got.status_code == 200


def test_run_teardown_removes_only_that_run(unique):
    run = f"run-{unique}"
    mine = [requests.post(f"{API_URL}/inboxes",
                          json={"prefix": "a", "run_id": run}, timeout=30).json()["address"]
            for _ in range(3)]
    other = requests.post(f"{API_URL}/inboxes",
                          json={"prefix": "b", "run_id": f"other-{unique}"}, timeout=30
                          ).json()["address"]

    removed = requests.delete(f"{API_URL}/inboxes", params={"run_id": run}, timeout=30).json()
    assert removed["count"] == 3
    assert set(removed["deleted"]) == set(mine)

    still_there = {a["address"] for a in
                   requests.get(f"{API_URL}/accounts", timeout=30).json()["accounts"]}
    assert other in still_there
    assert "alice@tahidromos.test" in still_there


def test_prefix_is_sanitised(unique):
    box = requests.post(f"{API_URL}/inboxes",
                        json={"prefix": "Has Spaces & Symbols!"}, timeout=30).json()
    localpart = box["address"].split("@")[0]
    assert " " not in localpart and "&" not in localpart and "!" not in localpart


# ---------------------------------------------------------------- waiting


def test_wait_can_require_a_code_and_a_link(unique):
    box = requests.post(f"{API_URL}/inboxes", json={"prefix": "otp"}, timeout=30).json()["address"]
    requests.post(f"{API_URL}/scenarios",
                  json={"name": "otp", "to": box, "seed": 7}, timeout=30)

    got = requests.post(f"{API_URL}/wait",
                        json={"user": box, "has_code": True, "link_contains": "magic",
                              "timeout": 20}, timeout=40).json()
    assert got["code"].isdigit()
    assert "magic?token=" in got["link"]


def test_wait_does_not_match_when_a_filter_fails(unique):
    box = requests.post(f"{API_URL}/inboxes", json={"prefix": "nf"}, timeout=30).json()["address"]
    MailClient("alice").send(box, f"no code here {unique}", "plain words only")

    response = requests.post(f"{API_URL}/wait",
                             json={"user": box, "has_code": True, "timeout": 3}, timeout=30)
    assert response.status_code == 408


def test_wait_can_mark_seen(unique):
    box = requests.post(f"{API_URL}/inboxes", json={"prefix": "ms"}, timeout=30).json()["address"]
    MailClient("alice").send(box, f"mark me {unique}", "body")

    got = requests.post(f"{API_URL}/wait",
                        json={"user": box, "subject_contains": unique,
                              "mark_seen": True, "timeout": 20}, timeout=40).json()
    assert got["uid"]
    listing = requests.get(f"{API_URL}/messages/{box}", timeout=30).json()["messages"]
    assert all(m["seen"] for m in listing)


# ---------------------------------------------------------------- parsing


def test_parse_endpoint_strips_quotes_and_finds_things():
    result = requests.post(f"{API_URL}/parse", json={
        "text": "Yes please.\n\nOn Sun, 06 Sep 2026 at 20:38, bob@x.test wrote:\n"
                "> Shall we?\n\n--\nAlice\nSent from my iPhone",
        "html": "<a href='https://x.test/go?c=1'>go</a>",
    }, timeout=30).json()

    assert result["stripped_text"] == "Yes please."
    assert "https://x.test/go?c=1" in result["links"]


def test_quoted_thread_is_stripped_on_a_delivered_message(unique):
    box = requests.post(f"{API_URL}/inboxes", json={"prefix": "dr"}, timeout=30).json()["address"]
    requests.post(f"{API_URL}/scenarios",
                  json={"name": "deep_reply", "to": box, "seed": 3}, timeout=30)

    got = requests.post(f"{API_URL}/wait",
                        json={"user": box, "subject_contains": "order", "timeout": 20},
                        timeout=40).json()
    assert got["stripped_text"] == "Yes, please cancel it."
    assert ">" in got["text"]


# ---------------------------------------------------------------- scenarios


def test_every_scenario_is_listed_and_deliverable(unique):
    catalogue = requests.get(f"{API_URL}/scenarios", timeout=30).json()["scenarios"]
    names = {s["name"] for s in catalogue}
    assert {"bounce", "newsletter", "html_only", "otp", "deep_reply",
            "attachment", "unicode", "auto_reply", "signed", "large"} <= names

    box = requests.post(f"{API_URL}/inboxes", json={"prefix": "sc"}, timeout=30).json()["address"]
    for name in sorted(names):
        options = {"size_kb": 8} if name == "large" else {}
        sent = requests.post(f"{API_URL}/scenarios",
                             json={"name": name, "to": box, "seed": 11, "options": options},
                             timeout=60)
        assert sent.status_code == 201, f"{name}: {sent.text}"

        got = requests.post(f"{API_URL}/wait",
                            json={"user": box, "message_id": sent.json()["message_id"],
                                  "timeout": 20}, timeout=40)
        assert got.status_code == 200, f"{name} never arrived"


def test_a_seed_makes_a_scenario_reproducible(unique):
    one = requests.post(f"{API_URL}/inboxes", json={"prefix": "s1"}, timeout=30).json()["address"]
    two = requests.post(f"{API_URL}/inboxes", json={"prefix": "s2"}, timeout=30).json()["address"]

    first = requests.post(f"{API_URL}/scenarios",
                          json={"name": "otp", "to": one, "seed": 4242}, timeout=30).json()
    second = requests.post(f"{API_URL}/scenarios",
                           json={"name": "otp", "to": two, "seed": 4242}, timeout=30).json()
    assert first["message_id"] == second["message_id"]
    assert first["size"] == second["size"]

    different = requests.post(f"{API_URL}/scenarios",
                              json={"name": "otp", "to": one, "seed": 4243}, timeout=30).json()
    assert different["message_id"] != first["message_id"]


def test_bounce_is_a_real_delivery_status_report(unique):
    box = requests.post(f"{API_URL}/inboxes", json={"prefix": "bn"}, timeout=30).json()["address"]
    sent = requests.post(f"{API_URL}/scenarios",
                         json={"name": "bounce", "to": box, "seed": 1}, timeout=30).json()
    got = requests.post(f"{API_URL}/wait",
                        json={"user": box, "message_id": sent["message_id"], "timeout": 20},
                        timeout=40).json()

    raw = got["raw"]
    assert "multipart/report" in raw
    assert "message/delivery-status" in raw
    assert "Final-Recipient:" in raw
    assert "Status: 5.1.1" in raw


def test_unknown_scenario_lists_the_valid_ones():
    response = requests.post(f"{API_URL}/scenarios",
                             json={"name": "nope", "to": "alice"}, timeout=30)
    assert response.status_code == 404
    assert "bounce" in response.json()["detail"]


# ---------------------------------------------------------------- webhook


def test_webhook_is_off_by_default_and_says_how_to_turn_it_on():
    status = requests.get(f"{API_URL}/webhook", timeout=30).json()
    if status.get("enabled"):
        pytest.skip("INBOUND_WEBHOOK_URL is set for this run")
    assert status["enabled"] is False
    assert "INBOUND_WEBHOOK_URL" in status["hint"]


@pytest.mark.parametrize("fmt", ["postmark", "sendgrid", "mailgun", "raw"])
def test_webhook_payload_shapes(fmt):
    """The payload builder is what a provider-shaped webhook depends on."""
    from tahidromos.webhook import build_payload

    raw = ("From: Customer <customer@shop.test>\r\n"
           "To: support+ticket42@tahidromos.test\r\n"
           "Subject: Re: Order #10432\r\n"
           "Message-ID: <abc@shop.test>\r\n"
           "Date: Sun, 06 Sep 2026 20:00:00 +0000\r\n\r\n"
           "Where is my order?\r\n\r\n"
           "On Sun, 06 Sep 2026 at 19:00, orders@shop.test wrote:\r\n"
           "> It ships tomorrow.\r\n").encode()

    payload = build_payload(raw, "support+ticket42@tahidromos.test", "support@tahidromos.test", fmt)

    if fmt == "postmark":
        assert payload["From"] == "customer@shop.test"
        assert payload["OriginalRecipient"] == "support+ticket42@tahidromos.test"
        assert payload["MailboxHash"] == "ticket42"
        assert payload["StrippedTextReply"] == "Where is my order?"
    elif fmt == "sendgrid":
        assert json.loads(payload["envelope"])["to"] == ["support+ticket42@tahidromos.test"]
        assert "Where is my order?" in payload["text"]
    elif fmt == "mailgun":
        assert payload["recipient"] == "support+ticket42@tahidromos.test"
        assert payload["stripped-text"] == "Where is my order?"
        assert payload["Message-Id"] == "<abc@shop.test>"
    else:
        assert payload["stripped_text"] == "Where is my order?"
        assert payload["envelope_to"] == "support+ticket42@tahidromos.test"
