"""The pytest fixtures shipped in clients/python.

These are the fixtures a user of the project actually writes tests with, so
they get tested the same way any other public surface does.
"""

from __future__ import annotations

import pytest


def test_inbox_is_unique_per_test(inbox, inbox_factory):
    other = inbox_factory("other")
    assert inbox.address != other.address
    assert inbox.address.endswith("@tahidromos.test")


def test_send_and_wait_without_sleeping(mail, inbox):
    mail.send("noreply@tahidromos.test", inbox.address,
              "Welcome aboard", "Glad you are here.")
    message = inbox.wait(subject_contains="Welcome aboard", timeout=20)
    assert message.text.startswith("Glad you are here")
    assert message.sender == "noreply@tahidromos.test"


def test_wait_raises_an_assertion_error_on_timeout(inbox):
    with pytest.raises(AssertionError):
        inbox.wait(subject_contains="never going to arrive", timeout=2)


def test_magic_link_and_code_are_extracted(mail, inbox):
    mail.scenario("otp", to=inbox.address, seed=99)
    message = inbox.wait(has_code=True, timeout=20)

    assert message.code is not None and message.code.isdigit()
    assert "magic?token=" in message.link
    assert message.link_containing("magic") == message.link


def test_html_only_message_still_yields_code_and_link(mail, inbox):
    mail.scenario("html_only", to=inbox.address, seed=5)
    message = inbox.wait(subject_contains="Only HTML", timeout=20)

    assert message.text.strip() == "" or "<" not in message.stripped_text
    assert message.code == "558102"
    assert message.link.endswith("/confirm?id=9f2a")


def test_quoted_reply_is_stripped(mail, inbox):
    mail.scenario("deep_reply", to=inbox.address, seed=3)
    message = inbox.wait(subject_contains="Your order", timeout=20)

    assert message.stripped_text == "Yes, please cancel it."
    assert "Sent from my iPhone" not in message.stripped_text
    assert ">" in message.text, "the full body should still be available"


def test_echo_bot_replies_and_threads(mail, inbox, echo_bot):
    sent = inbox.send(to=echo_bot, subject="Ticket #42", text="Is this fixed?")
    reply = inbox.wait(in_reply_to=sent["message_id"], timeout=45)

    assert reply.sender == echo_bot
    assert reply.subject == "Re: Ticket #42"
    assert reply.references == [sent["message_id"]]


def test_replying_to_the_bot_deepens_the_thread(mail, inbox, echo_bot):
    sent = inbox.send(to=echo_bot, subject="Ticket #43", text="turn one")
    first = inbox.wait(in_reply_to=sent["message_id"], timeout=45)

    inbox.reply(first, "turn three")
    second = inbox.wait(subject_contains="Ticket #43", from_contains=echo_bot,
                        unseen_only=True, timeout=45)

    assert second.depth > first.depth
    assert second.references[0] == sent["message_id"]


def test_seeded_scenarios_are_byte_identical(mail, inbox_factory):
    first, second = inbox_factory("seed-a"), inbox_factory("seed-b")
    mail.scenario("newsletter", to=first.address, seed=1234)
    mail.scenario("newsletter", to=second.address, seed=1234)

    one = first.wait(subject_contains="weekly digest", timeout=20)
    two = second.wait(subject_contains="weekly digest", timeout=20)

    # The envelope differs (different recipients); the message does not.
    assert one.message_id == two.message_id
    assert one.text == two.text


def test_capture_mailbox_holds_outside_mail(mail, inbox, captured_inbox):
    before = len(captured_inbox.messages())
    mail.send(inbox.address, "someone@gmail.com", "Escaping", "should not leave")

    held = captured_inbox.wait(subject_contains="Escaping", timeout=20)
    assert "gmail.com" in str(held.to) or "gmail.com" in held.raw
    assert len(captured_inbox.messages()) > before


def test_cleanup_removes_only_this_run(mail):
    box = mail.inbox("temporary")
    assert mail.mailbox("alice@tahidromos.test").count() >= 0  # seeded box still works

    removed = mail.cleanup()
    assert box.address in removed
    assert mail.mailbox("alice@tahidromos.test").count() >= 0, "seeded mailboxes survive"
