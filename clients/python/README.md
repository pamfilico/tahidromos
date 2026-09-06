# tahidromos-client

Python client and pytest fixtures for [tahidromos](https://github.com/pamfilico/tahidromos),
the development mail server you can reply to.

```sh
pip install tahidromos-client
```

## pytest

The fixtures register themselves — there is no conftest wiring to copy.

```python
def test_signup_sends_a_confirmation(inbox, client):
    client.post("/register", json={"email": inbox.address})

    message = inbox.wait(subject_contains="Confirm your address")

    assert message.link.startswith("https://")
    assert message.code is not None
```

`inbox` is a mailbox created for that test alone and deleted afterwards, so
parallel workers cannot read each other's mail. `inbox.wait()` long-polls the
server, so no test ever needs `sleep()`.

### Fixtures

| Fixture | What it gives you |
| --- | --- |
| `inbox` | A throwaway mailbox for this test |
| `inbox_factory` | Make several isolated mailboxes in one test |
| `mail` | The `Tahidromos` client, session-scoped |
| `echo_bot` | An address that always replies, threaded correctly |
| `captured_inbox` | The mailbox holding mail addressed outside the local domains |

If the server is not running the fixtures **skip** rather than fail, and tell
you the command to start it.

### Options

```sh
pytest --tahidromos-url http://localhost:8080 --tahidromos-keep
```

`--tahidromos-keep` leaves the inboxes behind so you can look at them in the
browser at <http://localhost:8080>. Also readable from `TAHIDROMOS_URL`,
`TAHIDROMOS_SMTP_PORT`, `TAHIDROMOS_IMAP_PORT` and `TAHIDROMOS_RUN_ID`.

## Testing a reply

The thing a mail sink cannot do:

```python
def test_we_handle_an_incoming_reply(inbox, echo_bot):
    inbox.send(to=echo_bot, subject="Ticket #42", text="Is this fixed?")

    reply = inbox.wait(from_contains=echo_bot, timeout=30)
    assert reply.in_reply_to is not None

    inbox.reply(reply, "Thanks, closing it.")
    second = inbox.wait(in_reply_to=None, subject_contains="Ticket #42", timeout=30)
    assert second.depth > reply.depth
```

## Without pytest

```python
from tahidromos_client import Tahidromos

mail = Tahidromos("http://localhost:8080")
inbox = mail.inbox("checkout")

mail.send("noreply@tahidromos.test", inbox.address, "Receipt", "Thanks!")
print(inbox.wait(subject_contains="Receipt").text)

mail.cleanup()          # delete every inbox this client made
```

### Forwarding

```python
def test_a_forwarded_thread_keeps_going(inbox_factory):
    bob, carol = inbox_factory("bob"), inbox_factory("carol")
    bob.send(to=bob.address, subject="Q3 report", text="Here it is.")
    original = bob.wait(subject_contains="Q3 report")

    forwarded = bob.forward(original, carol.address, note="Carol — see below.")
    at_carol = carol.wait(message_id=forwarded["message_id"])

    assert at_carol.subject == "Fwd: Q3 report"
    assert at_carol.in_reply_to is None            # a forward is not a reply
    assert at_carol.forwarded_from == original.message_id
    assert at_carol.attachments == original.attachments

    carol.reply(at_carol, "Thanks.")               # and the thread continues
```

### Canned awkward messages

```python
mail.scenario("bounce", to=inbox.address)        # an RFC 3464 DSN
mail.scenario("newsletter", to=inbox.address)    # List-Unsubscribe + HTML part
mail.scenario("deep_reply", to=inbox.address)    # three levels of quoting
mail.scenario("otp", to=inbox.address, seed=42)  # same bytes every run
```

`mail.scenarios()` lists them all.

## License

MIT
