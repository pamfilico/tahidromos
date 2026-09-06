"""One instance, many apps.

Each app in tahidromos.d/*.yml gets its own domain, its own SMTP credentials
and its own mailboxes, so a single tahidromos replaces the mail sink you would
otherwise run per docker-compose file.
"""

from __future__ import annotations

import smtplib

import pytest

from conftest import DOMAIN, SMTP_HOST, SMTP_PORT, MailClient, qualify


@pytest.fixture(scope="module")
def apps(api_module):
    return {app["name"]: app for app in api_module.get("/overview").json()["apps"]}


@pytest.fixture(scope="module")
def api_module():
    import requests

    from conftest import API_URL

    class Api:
        def get(self, path, **kwargs):
            return requests.get(f"{API_URL}{path}", timeout=60, **kwargs)

        def post(self, path, json=None, **kwargs):
            return requests.post(f"{API_URL}{path}", json=json, timeout=120, **kwargs)

    return Api()


def test_every_configured_app_is_present(apps):
    assert {"tahidromos", "shop", "crm"} <= set(apps), sorted(apps)


def test_each_app_has_its_own_domain(apps):
    domains = {app["domain"] for app in apps.values()}
    assert len(domains) == len(apps), f"apps share a domain: {domains}"
    assert apps["shop"]["domain"] == "shop.test"
    assert apps["crm"]["domain"] == "crm.test"


def test_each_app_has_its_own_smtp_credentials(apps):
    assert apps["shop"]["smtp_address"] == "shop@shop.test"
    assert apps["crm"]["smtp_address"] == "crm@crm.test"
    assert apps["shop"]["smtp_password"] != apps["crm"]["smtp_password"]


def test_app_credentials_authenticate(apps):
    for name in ("shop", "crm"):
        app = apps[name]
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
            smtp.ehlo()
            smtp.login(app["smtp_address"], app["smtp_password"])


def test_wrong_app_password_is_rejected(apps):
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
        smtp.ehlo()
        with pytest.raises(smtplib.SMTPAuthenticationError):
            smtp.login(apps["shop"]["smtp_address"], "definitely-not-the-password")


def test_an_app_delivers_within_its_own_domain(apps, unique):
    app = apps["shop"]
    sender = MailClient(app["smtp_address"], app["smtp_password"])
    subject = f"shop internal {unique}"
    message_id = sender.send("orders@shop.test", subject, "an order came in")

    received = MailClient("orders@shop.test").wait_for_id(message_id)
    assert str(received["Subject"]) == subject


def test_apps_can_mail_each_other(apps, unique):
    """Cross-app delivery still works -- useful for testing integrations."""
    app = apps["crm"]
    sender = MailClient(app["smtp_address"], app["smtp_password"])
    subject = f"crm to shop {unique}"
    message_id = sender.send("support@shop.test", subject, "a customer asked about an order")

    received = MailClient("support@shop.test").wait_for_id(message_id)
    assert "crm.test" in str(received["From"])


def test_mailboxes_are_isolated_between_apps(apps, unique):
    """support@shop.test and support@tahidromos.test are different mailboxes."""
    subject = f"isolation {unique}"
    MailClient("alice").send("support@shop.test", subject, "for the shop only")

    MailClient("support@shop.test").wait_for_subject(subject)

    other = MailClient(f"support@{DOMAIN}").inbox()
    assert not [m for m in other if subject in str(m.get("Subject", ""))], \
        "message leaked into the other app's mailbox"


def test_per_mailbox_password_override(apps):
    """crm's agent has its own password in the config file."""
    agent = MailClient("agent@crm.test", "agent-only-password")
    assert "INBOX" in agent.mailboxes()

    wrong = MailClient("agent@crm.test", "password")
    with pytest.raises(Exception):
        wrong.mailboxes()


def test_every_app_has_a_bot_that_replies(apps, unique):
    for name in ("shop", "crm"):
        app = apps[name]
        bots = [box for box in app["mailboxes"] if box["is_bot"]]
        assert bots, f"{name} has no bot mailbox"

        sender = MailClient(app["smtp_address"], app["smtp_password"])
        subject = f"{name} bot {unique}"
        message_id = sender.send(bots[0]["address"], subject, "are you there?")

        reply = MailClient(app["smtp_address"], app["smtp_password"]).wait_for(
            lambda m, mid=message_id: str(m.get("In-Reply-To", "")).strip() == mid.strip(),
            timeout=60)
        assert bots[0]["address"] in str(reply["From"])


def test_apps_endpoint_exposes_the_rendered_config(api_module):
    payload = api_module.get("/apps").json()
    assert payload["sources"], "config file was not picked up"
    assert any(source.endswith(".yml") for source in payload["sources"])
    names = {app["name"] for app in payload["apps"]}
    assert {"tahidromos", "shop", "crm"} <= names


def test_overview_badge_counts_track_delivery(api_module, unique):
    before = api_module.get("/overview").json()
    box_before = next(b for a in before["apps"] for b in a["mailboxes"]
                      if b["address"] == "dave@" + DOMAIN)

    MailClient("alice").send("dave", f"badge check {unique}", "counting")
    MailClient("dave").wait_for_subject(f"badge check {unique}")

    after = api_module.get("/overview").json()
    box_after = next(b for a in after["apps"] for b in a["mailboxes"]
                     if b["address"] == "dave@" + DOMAIN)

    assert box_after["total"] == box_before["total"] + 1
    assert box_after["unread"] == box_before["unread"] + 1
    assert after["total"] > before["total"]


def test_marking_read_moves_the_badge(api_module, unique):
    import requests

    from conftest import API_URL

    MailClient("dave").purge()
    MailClient("alice").send("dave", f"read badge {unique}", "mark me")
    MailClient("dave").wait_for_subject(f"read badge {unique}")

    unread_before = api_module.get("/overview").json()
    box = next(b for a in unread_before["apps"] for b in a["mailboxes"]
               if b["address"] == "dave@" + DOMAIN)
    assert box["unread"] == 1

    messages = api_module.get(f"/messages/dave@{DOMAIN}").json()["messages"]
    requests.patch(f"{API_URL}/messages/dave@{DOMAIN}/{messages[0]['uid']}",
                   json={"seen": True}, timeout=30).raise_for_status()

    after = api_module.get("/overview").json()
    box = next(b for a in after["apps"] for b in a["mailboxes"]
               if b["address"] == "dave@" + DOMAIN)
    assert box["unread"] == 0
    assert box["total"] == 1


def test_reading_a_message_does_not_mark_it_read(api_module, unique):
    """The UI must never perturb a test that is waiting on an unread count."""
    MailClient("carol").purge()
    MailClient("alice").send("carol", f"peek {unique}", "do not touch my flags")
    MailClient("carol").wait_for_subject(f"peek {unique}")

    messages = api_module.get(f"/messages/carol@{DOMAIN}").json()["messages"]
    uid = messages[0]["uid"]
    api_module.get(f"/messages/carol@{DOMAIN}/{uid}")           # full fetch
    api_module.get(f"/messages/carol@{DOMAIN}/{uid}/raw")       # raw fetch

    after = api_module.get(f"/messages/carol@{DOMAIN}").json()["messages"]
    assert after[0]["seen"] is False
