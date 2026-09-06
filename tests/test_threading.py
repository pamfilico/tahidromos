"""Replies, replies to replies, and the RFC 5322 headers that tie them together.

This is the whole reason tahidromos exists: a catch-all SMTP sink cannot do
any of this, because it has no mailboxes to reply *into*.
"""

from __future__ import annotations

from conftest import DOMAIN


def test_reply_carries_in_reply_to(alice, bob, unique):
    original_id = alice.send("bob", f"opening move {unique}", "first message")
    original = bob.wait_for_id(original_id)

    reply_id = bob.reply_to(original, "and this is the answer")
    reply = alice.wait_for_id(reply_id)

    assert str(reply["In-Reply-To"]).strip() == original_id.strip()
    assert original_id.strip() in str(reply["References"])
    assert str(reply["Subject"]) == f"Re: opening move {unique}"


def test_reply_to_a_reply_keeps_the_whole_chain(alice, bob, unique):
    """Three messages deep: References must accumulate, not reset."""
    first_id = alice.send("bob", f"three deep {unique}", "message 1")
    first = bob.wait_for_id(first_id)

    second_id = bob.reply_to(first, "message 2")
    second = alice.wait_for_id(second_id)

    third_id = alice.reply_to(second, "message 3")
    third = bob.wait_for_id(third_id)

    assert str(third["In-Reply-To"]).strip() == second_id.strip()
    references = str(third["References"]).split()
    assert references == [first_id.strip(), second_id.strip()], references
    assert str(third["Subject"]) == f"Re: three deep {unique}"


def test_long_chain_keeps_a_single_root(alice, bob, unique):
    """Ten alternating turns still resolve to one thread root."""
    root_id = alice.send("bob", f"long chain {unique}", "turn 1")
    current_id = root_id
    speakers = [(bob, alice), (alice, bob)]

    final = None
    for turn in range(2, 11):
        responder, listener = speakers[turn % 2]
        incoming = responder.wait_for_id(current_id)
        current_id = responder.reply_to(incoming, f"turn {turn}")
        final = listener.wait_for_id(current_id)

    references = str(final["References"]).split()
    assert references[0] == root_id.strip(), "thread root drifted"
    assert len(references) == 9, f"expected 9 ancestors, got {len(references)}"


def test_subject_prefix_is_not_doubled(alice, bob, unique):
    first_id = alice.send("bob", f"no double re {unique}", "1")
    first = bob.wait_for_id(first_id)
    second_id = bob.reply_to(first, "2")
    second = alice.wait_for_id(second_id)
    third_id = alice.reply_to(second, "3")
    third = bob.wait_for_id(third_id)

    assert str(third["Subject"]) == f"Re: no double re {unique}"
    assert str(third["Subject"]).lower().count("re:") == 1


def test_reply_goes_back_to_the_original_sender(alice, bob, unique):
    original_id = alice.send("bob", f"routing back {unique}", "ping")
    original = bob.wait_for_id(original_id)
    reply_id = bob.reply_to(original, "pong")

    reply = alice.wait_for_id(reply_id)
    assert f"bob@{DOMAIN}" in str(reply["From"])
    assert f"alice@{DOMAIN}" in str(reply["To"])


def test_three_way_conversation(alice, bob, carol, unique):
    """Carol is CC'd, replies to all, and everyone stays in the same thread."""
    subject = f"group chat {unique}"
    first_id = alice.send("bob", subject, "hello both", cc="carol")

    from_bob = bob.wait_for_id(first_id)
    bob_reply_id = bob.reply_to(from_bob, "bob here")
    alice.wait_for_id(bob_reply_id)

    from_carol = carol.wait_for_id(first_id)
    carol_reply_id = carol.reply_to(from_carol, "carol here")
    carol_reply = alice.wait_for_id(carol_reply_id)

    assert str(carol_reply["In-Reply-To"]).strip() == first_id.strip()
    assert f"carol@{DOMAIN}" in str(carol_reply["From"])
