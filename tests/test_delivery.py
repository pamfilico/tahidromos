"""Does mail actually get from A to B?"""

from __future__ import annotations

import pytest

from conftest import DOMAIN, SMTP_PLAIN_PORT, MailClient


def test_local_delivery(alice, bob, unique):
    subject = f"basic delivery {unique}"
    message_id = alice.send("bob", subject, "hello bob")

    received = bob.wait_for_id(message_id)
    assert str(received["Subject"]) == subject
    assert f"alice@{DOMAIN}" in str(received["From"])
    assert "hello bob" in received.get_body(preferencelist=("plain",)).get_content()


def test_message_id_survives_delivery(alice, bob, unique):
    """A stable Message-ID is the anchor every reply chain hangs off."""
    message_id = alice.send("bob", f"id check {unique}", "body")
    received = bob.wait_for_id(message_id)
    assert str(received["Message-ID"]).strip() == message_id.strip()


def test_unauthenticated_smtp_on_port_25(alice, bob, unique):
    """Most dev apps just point SMTP_HOST at the server with no credentials."""
    subject = f"no auth {unique}"
    message_id = alice.send("bob", subject, "sent without SMTP AUTH",
                            auth=False, port=SMTP_PLAIN_PORT)
    received = bob.wait_for_id(message_id)
    assert str(received["Subject"]) == subject


def test_cc_recipient_receives(alice, bob, carol, unique):
    subject = f"cc {unique}"
    message_id = alice.send("bob", subject, "you are both on this", cc="carol")

    for client in (bob, carol):
        received = client.wait_for_id(message_id)
        assert f"carol@{DOMAIN}" in str(received["Cc"])


def test_plus_addressing_lands_in_base_mailbox(alice, bob, unique):
    """bob+invoices@ is bob@ -- handy for testing tagged addresses."""
    subject = f"plus addressing {unique}"
    message_id = alice.send(f"bob+invoices@{DOMAIN}", subject, "tagged")
    received = bob.wait_for_id(message_id)
    assert "bob+invoices" in str(received["To"])


def test_imaps_tls_port_works(alice, bob, unique):
    """The self-signed cert on 993 lets you exercise TLS client code paths."""
    subject = f"over tls {unique}"
    message_id = alice.send("bob", subject, "tls read")
    bob.wait_for_id(message_id)

    over_tls = [m for m in bob.inbox(use_ssl=True)
                if str(m.get("Message-ID", "")).strip() == message_id.strip()]
    assert over_tls, "message not readable over IMAPS"


def test_standard_mailboxes_exist(alice):
    folders = alice.mailboxes()
    assert "INBOX" in folders
    for expected in ("Sent", "Drafts", "Trash"):
        assert expected in folders, f"{expected} missing from {folders}"


def test_second_app_domain_delivers_locally(unique):
    """shop.test is a second app's domain, so it must NOT hit the catch-all."""
    sender = MailClient("alice")
    recipient = MailClient("orders@shop.test")

    subject = f"cross domain {unique}"
    message_id = sender.send("orders@shop.test", subject, "hello other app")
    received = recipient.wait_for_id(message_id)
    assert str(received["Subject"]) == subject


def test_external_address_is_captured_not_sent(alice, captured, unique):
    """Nothing may escape to the real internet."""
    subject = f"captured {unique}"
    alice.send("stranger@gmail.com", subject, "this must never leave the network")

    held = captured.wait_for_subject(subject)
    # the recipient was rewritten, but the original To: is preserved
    assert "stranger@gmail.com" in str(held["To"])


def test_many_external_domains_all_captured(alice, captured, unique):
    for index, domain in enumerate(("gmail.com", "example.org", "outlook.com")):
        subject = f"escape {index} {unique}"
        alice.send(f"person@{domain}", subject, "nope")
        held = captured.wait_for_subject(subject)
        assert domain in str(held["To"])


def test_purge_empties_a_mailbox(alice, bob, unique):
    message_id = alice.send("bob", f"to be purged {unique}", "temporary")
    bob.wait_for_id(message_id)
    bob.purge()
    assert bob.inbox() == []
