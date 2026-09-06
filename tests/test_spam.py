"""Spam scoring: it catches the obvious, and leaves good mail alone.

A scorer that flags well-built marketing email is worse than no scorer, so
the false-positive tests matter more than the true-positive ones.
"""

from __future__ import annotations

import pytest
import requests

from conftest import API_URL

TEMPLATES = ["welcome", "otp", "password_reset", "receipt", "digest", "alert",
             "invite", "verify_email"]


def score(**payload) -> dict:
    response = requests.post(f"{API_URL}/spam", json=payload, timeout=30)
    assert response.status_code == 200, response.text
    return response.json()


def test_engines_endpoint_reports_what_is_available():
    engines = requests.get(f"{API_URL}/spam", timeout=30).json()
    assert engines["builtin"]["available"] is True
    assert engines["builtin"]["spam_at"] == 5.0
    assert "rspamd" in engines


def test_ordinary_mail_scores_zero():
    result = score(subject="Lunch on Thursday?",
                   text="Are you free around one? There is a new place near the office.")
    assert result["score"] == 0
    assert result["verdict"] == "ham"
    assert result["hits"] == []


@pytest.mark.parametrize("name", TEMPLATES)
def test_no_template_is_flagged(name):
    """Every template we ship must score as ham, or the scorer is useless."""
    rendered = requests.post(f"{API_URL}/templates/{name}/preview", json={}, timeout=30).json()
    result = score(subject=rendered["subject"], text=rendered["text"],
                   html=rendered["html"], **{"from": rendered["from"]})
    assert result["verdict"] == "ham", \
        f"{name} was flagged: {[h['rule'] for h in result['hits']]}"


def test_shouting_and_prize_claims_score_as_spam():
    result = score(subject="WIN FREE MONEY NOW!!!",
                   text="ACT NOW! YOU HAVE WON THE LOTTERY. Claim your unclaimed funds.")
    assert result["verdict"] == "spam"
    rules = {h["rule"] for h in result["hits"]}
    assert {"SUBJECT_ALL_CAPS", "SUBJECT_EXCLAMATION", "LOUD_PHRASE"} <= rules


def test_display_name_spoofing_is_caught():
    result = score(subject="Account verification required",
                   text="Please confirm your details.",
                   **{"from": '"security@yourbank.test" <attacker@evil.test>'})
    rules = {h["rule"] for h in result["hits"]}
    assert "FROM_DISPLAY_SPOOF" in rules
    assert result["verdict"] in ("suspicious", "spam")


def test_link_to_a_bare_ip_is_caught():
    result = score(subject="Sign in", text="Log in at http://192.0.2.44/login")
    assert "URL_BARE_IP" in {h["rule"] for h in result["hits"]}


def test_url_shortener_inside_an_href_is_caught():
    """Tag stripping must not hide the link target."""
    result = score(subject="Have a look", text="See this.",
                   html="<p>See <a href='https://bit.ly/abc123'>this</a>.</p>")
    assert "URL_SHORTENER" in {h["rule"] for h in result["hits"]}


def test_a_preheader_is_not_treated_as_hidden_text():
    """Every real template opens with one; flagging it would flag everything."""
    html = ("<div style='display:none;max-height:0;overflow:hidden'>Your receipt is ready</div>"
            "<p>Thanks for your order.</p>")
    result = score(subject="Receipt", text="Thanks for your order.", html=html)
    assert "HIDDEN_TEXT" not in {h["rule"] for h in result["hits"]}


def test_keyword_stuffing_below_the_preheader_is_caught():
    html = ("<p>Hello</p>"
            "<div style='display:none'>" + "casino loans pharmacy " * 60 + "</div>")
    result = score(subject="Hello", text="Hello", html=html)
    assert "HIDDEN_TEXT" in {h["rule"] for h in result["hits"]}


def test_bulk_mail_without_unsubscribe_is_flagged():
    raw = ("From: news@x.test\r\nTo: a@x.test\r\nSubject: Digest\r\n"
           "Message-ID: <1@x.test>\r\nDate: Sun, 06 Sep 2026 20:00:00 +0000\r\n"
           "Precedence: bulk\r\n\r\nOur newsletter.\r\n")
    result = score(raw=raw)
    assert "BULK_NO_UNSUBSCRIBE" in {h["rule"] for h in result["hits"]}


def test_missing_message_id_and_date_are_flagged():
    result = score(raw="From: a@x.test\r\nTo: b@x.test\r\nSubject: Hi\r\n\r\nHello\r\n")
    rules = {h["rule"] for h in result["hits"]}
    assert {"MISSING_MESSAGE_ID", "MISSING_DATE"} <= rules


def test_every_hit_explains_itself():
    result = score(subject="WIN NOW!!!", text="ACT NOW you have won")
    for hit in result["hits"]:
        assert hit["rule"] and hit["description"]
        assert isinstance(hit["weight"], (int, float))
    assert result["summary"]


def test_a_delivered_message_can_be_scored(alice, unique):
    message_id = alice.send("alice", f"SPAMMY {unique}!!!",
                            "ACT NOW! YOU HAVE WON. Click https://bit.ly/x")
    alice.wait_for_id(message_id)
    listing = requests.get(f"{API_URL}/messages/alice", timeout=30).json()["messages"]
    uid = next(m["uid"] for m in listing if m["message_id"] == message_id.strip())

    result = requests.get(f"{API_URL}/messages/alice/{uid}/spam", timeout=30).json()
    assert result["engine"] == "builtin"
    assert result["score"] > 0
    assert result["verdict"] in ("suspicious", "spam")


def test_asking_for_rspamd_when_it_is_absent_is_a_clear_error():
    response = requests.post(f"{API_URL}/spam",
                             json={"text": "hello", "engine": "rspamd"}, timeout=30)
    assert response.status_code == 503
    assert "RSPAMD_URL" in response.json()["detail"]
