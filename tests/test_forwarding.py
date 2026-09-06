"""Forwarding, and the thing that makes it worth having: replying to a forward.

A forward is not a reply, so it carries no In-Reply-To — but it does carry
References, so a reply to the forward still lands in the right conversation.
Attachments have to survive, the Fwd: prefix must not stack, and a mailbox
that forwards to another mailbox must not be able to loop.
"""

from __future__ import annotations

import pytest
import requests

from conftest import API_URL, MailClient


def api_post(path, payload, timeout=60):
    response = requests.post(f"{API_URL}{path}", json=payload, timeout=timeout)
    assert response.status_code in (200, 201), f"{path}: {response.status_code} {response.text}"
    return response.json()


@pytest.fixture
def three(unique):
    """Three fresh mailboxes: a sender, a forwarder, and a recipient."""
    made = [requests.post(f"{API_URL}/inboxes",
                          json={"prefix": name, "run_id": f"fwd-{unique}"}, timeout=30
                          ).json()["address"]
            for name in ("sender", "forwarder", "recipient")]
    yield made
    requests.delete(f"{API_URL}/inboxes", params={"run_id": f"fwd-{unique}"}, timeout=30)


def deliver(sender, to, subject, text="body"):
    sent = api_post("/send", {"from": sender, "to": to, "subject": subject, "text": text})
    return api_post("/wait", {"user": to, "message_id": sent["message_id"], "timeout": 30})


# ---------------------------------------------------------------- basics


def test_forward_reaches_a_user_in_the_app(three, unique):
    sender, forwarder, recipient = three
    original = deliver(sender, forwarder, f"Q3 report {unique}", "Here is the report.")

    forwarded = api_post("/forward", {"user": forwarder, "uid": original["uid"],
                                      "to": recipient, "note": "See below."})
    arrived = api_post("/wait", {"user": recipient,
                                 "message_id": forwarded["message_id"], "timeout": 30})

    assert arrived["subject"] == f"Fwd: Q3 report {unique}"
    assert "See below." in arrived["text"]
    assert "---------- Forwarded message ----------" in arrived["text"]
    assert "Here is the report." in arrived["text"], "the original body must be carried"


def test_the_forwarded_block_carries_the_original_headers(three, unique):
    sender, forwarder, recipient = three
    original = deliver(sender, forwarder, f"Header block {unique}")

    forwarded = api_post("/forward", {"user": forwarder, "uid": original["uid"],
                                      "to": recipient})
    arrived = api_post("/wait", {"user": recipient,
                                 "message_id": forwarded["message_id"], "timeout": 30})

    for expected in (f"From: {sender}", "Date:", f"Subject: Header block {unique}",
                     f"To: {forwarder}"):
        assert expected in arrived["text"], f"{expected!r} missing from the forwarded block"


def test_a_forward_is_not_a_reply(three, unique):
    sender, forwarder, recipient = three
    original = deliver(sender, forwarder, f"Not a reply {unique}")

    forwarded = api_post("/forward", {"user": forwarder, "uid": original["uid"],
                                      "to": recipient})
    arrived = api_post("/wait", {"user": recipient,
                                 "message_id": forwarded["message_id"], "timeout": 30})

    assert arrived["in_reply_to"] is None, "a forward must not set In-Reply-To"
    assert arrived["forwarded_from"] == original["message_id"]
    assert original["message_id"] in arrived["references"], \
        "References is carried so a reply still threads"


def test_forwarding_by_message_id_works_too(three, unique):
    sender, forwarder, recipient = three
    original = deliver(sender, forwarder, f"By id {unique}")

    forwarded = api_post("/forward", {"user": forwarder, "message_id": original["message_id"],
                                      "to": recipient})
    assert forwarded["forwarded_message_id"] == original["message_id"]


def test_the_fwd_prefix_does_not_stack(three, unique):
    sender, forwarder, recipient = three
    original = deliver(sender, forwarder, f"No stacking {unique}")

    first = api_post("/forward", {"user": forwarder, "uid": original["uid"], "to": recipient})
    at_recipient = api_post("/wait", {"user": recipient,
                                      "message_id": first["message_id"], "timeout": 30})

    second = api_post("/forward", {"user": recipient, "uid": at_recipient["uid"],
                                   "to": forwarder})
    assert second["subject"] == f"Fwd: No stacking {unique}"
    assert second["subject"].lower().count("fwd:") == 1
    assert second["forward_count"] == 2


# ---------------------------------------------------------------- replying


def test_replying_to_a_forward_threads_correctly(three, unique):
    sender, forwarder, recipient = three
    original = deliver(sender, forwarder, f"Reply to fwd {unique}")

    forwarded = api_post("/forward", {"user": forwarder, "uid": original["uid"],
                                      "to": recipient})
    at_recipient = api_post("/wait", {"user": recipient,
                                      "message_id": forwarded["message_id"], "timeout": 30})

    reply = api_post("/reply", {"user": recipient, "uid": at_recipient["uid"],
                                "text": "Thanks, reviewing today."})
    back = api_post("/wait", {"user": forwarder,
                              "message_id": reply["message_id"], "timeout": 30})

    assert back["subject"] == f"Re: Fwd: Reply to fwd {unique}"
    assert back["in_reply_to"] == forwarded["message_id"]
    assert original["message_id"] in back["references"], \
        "the reply should still reach back to the original"


def test_replying_to_the_reply_to_a_forward(three, unique):
    """The whole point: a forwarded conversation keeps going."""
    sender, forwarder, recipient = three
    original = deliver(sender, forwarder, f"Deep fwd {unique}")

    forwarded = api_post("/forward", {"user": forwarder, "uid": original["uid"],
                                      "to": recipient})
    at_recipient = api_post("/wait", {"user": recipient,
                                      "message_id": forwarded["message_id"], "timeout": 30})

    first = api_post("/reply", {"user": recipient, "uid": at_recipient["uid"], "text": "turn 1"})
    at_forwarder = api_post("/wait", {"user": forwarder,
                                      "message_id": first["message_id"], "timeout": 30})

    second = api_post("/reply", {"user": forwarder, "uid": at_forwarder["uid"], "text": "turn 2"})
    final = api_post("/wait", {"user": recipient,
                               "message_id": second["message_id"], "timeout": 30})

    assert final["subject"] == f"Re: Fwd: Deep fwd {unique}", "Re: must not stack either"
    assert final["depth"] > at_recipient["depth"]
    assert final["references"][0] == original["message_id"], "thread root drifted"
    assert len(final["references"]) >= 3


def test_a_forwarded_thread_groups_together(three, unique):
    sender, forwarder, recipient = three
    original = deliver(sender, forwarder, f"Grouping {unique}")
    forwarded = api_post("/forward", {"user": forwarder, "uid": original["uid"],
                                      "to": recipient})
    at_recipient = api_post("/wait", {"user": recipient,
                                      "message_id": forwarded["message_id"], "timeout": 30})
    reply = api_post("/reply", {"user": recipient, "uid": at_recipient["uid"], "text": "ack"})
    api_post("/wait", {"user": forwarder, "message_id": reply["message_id"], "timeout": 30})

    threads = requests.get(f"{API_URL}/threads/{forwarder}", timeout=30).json()["threads"]
    match = [t for t in threads if unique in t["subject"]]
    assert match, "the forwarded conversation should group into one thread"
    assert match[0]["message_count"] >= 2


# ---------------------------------------------------------------- content


def test_attachments_survive_a_forward(three, unique):
    sender, forwarder, recipient = three
    sent = api_post("/scenarios", {"name": "attachment", "to": forwarder, "seed": 5})
    original = api_post("/wait", {"user": forwarder,
                                  "message_id": sent["message_id"], "timeout": 30})
    assert len(original["attachments"]) == 2, "fixture should have two attachments"

    forwarded = api_post("/forward", {"user": forwarder, "uid": original["uid"],
                                      "to": recipient})
    arrived = api_post("/wait", {"user": recipient,
                                 "message_id": forwarded["message_id"], "timeout": 30})

    names = {a["filename"] for a in arrived["attachments"]}
    assert {"invoice-10432.pdf", "lines.csv"} <= names, f"attachments lost: {names}"
    assert all(a["size"] > 0 for a in arrived["attachments"])


def test_forward_as_attachment_keeps_the_original_intact(three, unique):
    sender, forwarder, recipient = three
    original = deliver(sender, forwarder, f"As attachment {unique}", "Original body here.")

    forwarded = api_post("/forward", {"user": forwarder, "uid": original["uid"],
                                      "to": recipient, "mode": "attachment"})
    arrived = api_post("/wait", {"user": recipient,
                                 "message_id": forwarded["message_id"], "timeout": 30})

    types = {a["content_type"] for a in arrived["attachments"]}
    assert "message/rfc822" in types
    assert "the original message is attached" in arrived["text"]
    assert original["message_id"] in arrived["raw"], "the original bytes should be in there"


def test_an_html_message_forwards_with_its_html(three, unique):
    sender, forwarder, recipient = three
    sent = api_post("/templates", {"name": "receipt", "to": forwarder})
    original = api_post("/wait", {"user": forwarder,
                                  "message_id": sent["message_id"], "timeout": 30})
    assert original["html"]

    forwarded = api_post("/forward", {"user": forwarder, "uid": original["uid"],
                                      "to": recipient, "note": "Passing this on."})
    arrived = api_post("/wait", {"user": recipient,
                                 "message_id": forwarded["message_id"], "timeout": 30})

    assert arrived["html"], "the HTML part should survive"
    assert "AC-10432" in arrived["html"]
    assert "Passing this on." in arrived["html"]


def test_forwarding_to_several_people_at_once(three, unique):
    sender, forwarder, recipient = three
    original = deliver(sender, forwarder, f"Everyone {unique}")
    second = requests.post(f"{API_URL}/inboxes",
                           json={"prefix": "also", "run_id": f"fwd-{unique}"},
                           timeout=30).json()["address"]

    forwarded = api_post("/forward", {"user": forwarder, "uid": original["uid"],
                                      "to": [recipient, second]})
    for address in (recipient, second):
        arrived = api_post("/wait", {"user": address,
                                     "message_id": forwarded["message_id"], "timeout": 30})
        assert arrived["subject"].startswith("Fwd:")


def test_forwarding_creates_a_missing_local_recipient(three, unique):
    sender, forwarder, _ = three
    fresh = f"never-seen-{unique}@tahidromos.test"
    original = deliver(sender, forwarder, f"Auto create {unique}")

    forwarded = api_post("/forward", {"user": forwarder, "uid": original["uid"], "to": fresh})
    assert fresh in forwarded["accounts_created"]
    assert api_post("/wait", {"user": fresh,
                              "message_id": forwarded["message_id"], "timeout": 30})


# ---------------------------------------------------------------- rules


def test_configured_rules_are_reported():
    rules = requests.get(f"{API_URL}/forwards", timeout=30).json()
    by_mailbox = {rule["mailbox"]: rule for rule in rules["rules"]}
    assert "helpdesk@shop.test" in by_mailbox
    assert by_mailbox["helpdesk@shop.test"]["forward_to"] == ["support@shop.test"]
    assert by_mailbox["helpdesk@shop.test"]["keep_copy"] is True
    assert by_mailbox["contact@shop.test"]["keep_copy"] is False
    assert rules["max_hops"] >= 1


def test_a_forwarding_mailbox_delivers_to_its_target(unique):
    subject = f"rule keeps a copy {unique}"
    api_post("/send", {"from": "alice", "to": "helpdesk@shop.test",
                       "subject": subject, "text": "It arrived cracked."})

    at_support = api_post("/wait", {"user": "support@shop.test",
                                    "subject_contains": subject, "timeout": 30})
    assert at_support["subject"] == f"Fwd: {subject}"
    assert "It arrived cracked." in at_support["text"]
    assert "auto-forwarded" in at_support["raw"], "should be marked as machine-generated"

    kept = requests.get(f"{API_URL}/messages/helpdesk@shop.test",
                        timeout=30).json()["messages"]
    assert any(subject in m["subject"] for m in kept), "keep_copy: true should keep it"


def test_keep_copy_false_is_a_pure_redirect(unique):
    subject = f"rule keeps nothing {unique}"
    api_post("/send", {"from": "alice", "to": "contact@shop.test",
                       "subject": subject, "text": "Address please."})

    api_post("/wait", {"user": "support@shop.test", "subject_contains": subject, "timeout": 30})

    left = requests.get(f"{API_URL}/messages/contact@shop.test", timeout=30).json()["messages"]
    assert not any(subject in m["subject"] for m in left), \
        "keep_copy: false should leave nothing behind"


def test_replying_to_an_auto_forwarded_message_reaches_the_forwarder(unique):
    subject = f"reply to auto forward {unique}"
    api_post("/send", {"from": "alice", "to": "helpdesk@shop.test",
                       "subject": subject, "text": "Please help."})
    at_support = api_post("/wait", {"user": "support@shop.test",
                                    "subject_contains": subject, "timeout": 30})

    reply = api_post("/reply", {"user": "support@shop.test", "uid": at_support["uid"],
                                "text": "A replacement is on the way."})
    back = api_post("/wait", {"user": "helpdesk@shop.test",
                              "message_id": reply["message_id"], "timeout": 30})
    assert back["subject"] == f"Re: Fwd: {subject}"


# ---------------------------------------------------------------- loops


def test_a_forwarding_loop_cannot_run_away():
    """Two mailboxes forwarding to each other must stop, not fill the disk."""
    import asyncio

    from tahidromos import message as msg
    from tahidromos.config import App, Config, Mailbox
    from tahidromos.router import Router
    from tahidromos.store import Store

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as directory:
        store = Store(str(Path(directory) / "loop.db"))
        left = Mailbox("left", "left@loop.test", "pw", forward_to=["right@loop.test"])
        right = Mailbox("right", "right@loop.test", "pw", forward_to=["left@loop.test"])
        app = App(name="loop", domain="loop.test", mailboxes=[left, right])
        config = Config(apps=[app], domains=["loop.test"], primary_domain="loop.test",
                        hostname="loop.test", default_password="pw",
                        capture_address="captured@loop.test", sources=[])
        for box in (left, right, Mailbox("captured", "captured@loop.test", "pw")):
            store.create_account(box.address, "pw")

        router = Router(store, config, max_forwards=4)
        seed = msg.build("outsider@loop.test", ["left@loop.test"], "ping", "hello",
                         domain="loop.test")

        asyncio.run(router.deliver("outsider@loop.test", ["left@loop.test"],
                                   msg.to_bytes(seed)))

        total = sum(store.counts(a)[0] for a in ("left@loop.test", "right@loop.test"))
        assert total <= 8, f"the loop produced {total} messages"
        assert router.forwarded <= 6, f"{router.forwarded} forwards is a runaway"
        assert total >= 1, "the first delivery should still have happened"
