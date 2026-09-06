<div align="center">

# 📮 tahidromos

**A development mail server that can hold a conversation.**

Real SMTP · Real IMAP · Real reply threads · One container · No login

</div>

---

Mailpit and MailHog are excellent, but they are *sinks*: mail goes in and never
comes out. You cannot reply to a message in a sink, so you cannot test the one
thing email chat apps are made of — a thread.

tahidromos is a mail server with real mailboxes. Send a message to
`bob@tahidromos.test`, read it over IMAP, reply to it, reply to the reply, and
watch `In-Reply-To` and `References` build a proper RFC 5322 chain — with
nothing ever reaching the real internet.

It is written from scratch on the Python standard library. There is no Postfix,
no Dovecot, no third-party mail server underneath — just one image you run.

![The tahidromos inbox](docs/screenshots/inbox.png)

## Quick start

```sh
curl -O https://raw.githubusercontent.com/pamfilico/tahidromos/main/docker-compose.yml
docker compose up -d
```

Or without compose at all:

```sh
docker run -d --name tahidromos \
  -p 1025:25 -p 1587:587 -p 1143:143 -p 8080:8080 \
  ghcr.io/pamfilico/tahidromos
```

That is the whole setup. No config files, no account creation, no certificates,
and no login screen anywhere. Open **<http://localhost:8080>**.

| | |
| --- | --- |
| Inbox browser + REST API | <http://localhost:8080> |
| SMTP — no credentials needed | `localhost:1025` |
| Submission — authenticated | `localhost:1587` · TLS `localhost:1465` |
| IMAP | `localhost:1143` · TLS `localhost:1993` |

Ten mailboxes exist immediately — `alice`, `bob`, `carol`, `dave`, `support`,
`sales`, `noreply`, `postmaster`, `echo`, `echo2` — all at `@tahidromos.test`
with the password **`password`**.

Point your app at it:

```env
SMTP_HOST=localhost
SMTP_PORT=1025            # or 1587 with SMTP AUTH
SMTP_USER=noreply@tahidromos.test
SMTP_PASSWORD=password
IMAP_HOST=localhost
IMAP_PORT=1143
```

## Prove it works

```sh
python3 examples/send_email.py
```

```
→ connecting to SMTP localhost:1587
→ sent <17887…@tahidromos.test>
→ waiting for delivery to bob@tahidromos.test over IMAP localhost:1143
✓ delivered — uid 1
```

And the part a mail sink cannot do:

```sh
python3 examples/reply_conversation.py --turns 6
```

```
--- final message ---------------------------------------------
Subject     : Re: Let's talk
In-Reply-To : <17887…@tahidromos.test>
References  : 5 ancestors
✓ 6 messages, one unbroken thread rooted at <17887…@tahidromos.test>
```

Both scripts use nothing but `smtplib` and `imaplib`, so they double as worked
examples of how your own app should thread its replies.

## Three ways to reply

### 1. By hand, in the browser

Pick a mailbox in the sidebar, open a message, hit **Reply**. The server builds
`In-Reply-To` and `References` from the original, so the thread is correct by
construction — the composer shows you exactly what it will hang the reply onto.

![Replying from the browser](docs/screenshots/reply-composer.png)

Messages group into threads with one click, and every message shows its
position in the chain:

![Threads](docs/screenshots/threads.png)

### 2. From your test suite, over REST

```sh
# send
curl -sX POST localhost:8080/send -H 'content-type: application/json' -d '{
  "from": "alice", "to": "bob", "subject": "Invoice #42", "text": "Any questions?"
}'

# wait for bob to receive it — long-polls, so no sleep() in your tests
curl -sX POST localhost:8080/wait -H 'content-type: application/json' -d '{
  "user": "bob", "subject_contains": "Invoice #42", "timeout": 30
}'

# reply, with In-Reply-To / References / "Re:" handled for you
curl -sX POST localhost:8080/reply -H 'content-type: application/json' -d '{
  "user": "bob", "message_id": "<...>", "text": "Yes — when is it due?"
}'
```

Or build an entire thread in one call:

```sh
curl -sX POST localhost:8080/conversation -H 'content-type: application/json' -d '{
  "participants": ["alice", "bob"], "subject": "Standup", "turns": 8
}'
```

### 3. Automatically, with the echo bots

`echo@` and `echo2@` are ordinary mailboxes with an auto-responder attached.
They reply to anything they receive, threading correctly, so your app always
has a conversation partner. Mail `echo`, get an answer, reply to that answer,
get another one.

Send one bot a message *from* the other and they volley on their own until
`BOT_MAX_DEPTH` stops them — the reply-to-a-reply-to-a-reply case, end to end,
with no human in the loop:

```
echo  replied depth=1 to=echo2  subject='Re: ping pong'
echo2 replied depth=2 to=echo   subject='Re: ping pong'
echo  replied depth=3 to=echo2  subject='Re: ping pong'
…
echo2 replied depth=8 to=echo   subject='Re: ping pong'   ← stops at the limit
```

The bots are a thread inside the same process — there is no extra service to
run. Turn them off with `BOT_ENABLED=false`.

## One server, many apps

Instead of a mail sink per docker-compose file, give every app its own domain,
its own SMTP credentials and its own mailboxes. Drop a file in `tahidromos.d/`:

```yaml
default_password: password

apps:
  shop:
    domain: shop.test
    smtp:
      username: shop
      password: shop-secret
    mailboxes:
      - orders
      - customer
      - name: echo
        bot: true

  crm:
    domain: crm.test
    smtp: { username: crm, password: crm-secret }
    mailboxes:
      sales:
        description: Inbound leads
      agent:
        password: agent-only-password
```

Each app then authenticates with its own credentials and can send as any of its
own addresses:

```env
# the shop app
SMTP_USER=shop@shop.test
SMTP_PASSWORD=shop-secret
```

The sidebar groups mailboxes by app, shows each app's credentials with a copy
button, and carries a live unread badge per mailbox and per app — so you can
see at a glance what has actually been delivered, and to whom.

## Nothing escapes

Mail addressed outside the local domains is never sent onward. The recipient is
rewritten to the `captured` mailbox while the original `To:` header is left
untouched, so you can still see exactly who it was meant for.

![Captured mail](docs/screenshots/captured.png)

If your app accidentally mails a real customer address in development, you find
it here and nowhere else.

## REST API

Interactive docs at <http://localhost:8080/docs>.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` · `/config` · `/apps` | Status, connection details, app config |
| `GET` | `/overview` | Every app and mailbox with unread/total counts |
| `GET` `POST` | `/accounts` | List mailboxes · create one on demand |
| `GET` | `/messages/{user}` | List messages (`?unseen=true`, `?mailbox=Sent`) |
| `GET` | `/messages/{user}/{uid}` · `/raw` | One message, parsed or as raw RFC 5322 |
| `PATCH` | `/messages/{user}/{uid}` | Mark read or unread |
| `DELETE` | `/messages/{user}` | Empty a mailbox between test cases |
| `GET` | `/threads/{user}` | Messages grouped into threads by `References` |
| `POST` | `/send` | Send (auto-creates unknown local mailboxes) |
| `POST` | `/reply` | Reply, headers handled for you |
| `POST` | `/wait` | Block until a matching message arrives |
| `POST` | `/conversation` | Generate a real multi-turn thread |

Reading a message never changes its flags — the UI fetches with `BODY.PEEK[]` —
so browsing a mailbox cannot perturb a test that is waiting on an unread count.

## Configuration

Every setting has a working default.

| Variable | Default | Meaning |
| --- | --- | --- |
| `MAIL_DOMAIN` | `tahidromos.test` | Primary local domain |
| `MAIL_LOCAL_DOMAINS` | *(app domains)* | Extra domains to deliver locally |
| `MAIL_ACCOUNTS` | `alice,bob,carol,…` | Seeded mailboxes; `user` or `user:password` |
| `MAIL_DEFAULT_PASSWORD` | `password` | Shared password for seeded mailboxes |
| `MAIL_AUTO_CREATE` | `true` | Create unknown local mailboxes on first delivery |
| `BOT_ENABLED` | `true` | Run the auto-responders |
| `BOT_ACCOUNTS` | `echo,echo2` | Which mailboxes auto-reply |
| `BOT_MODE` | `echo` | `echo` · `mirror` · `ack` · `counter` |
| `BOT_MAX_DEPTH` | `8` | Where a bot-to-bot chain stops |
| `ENABLE_TLS` | `true` | Generate a self-signed cert and open 465/993 |

Because unknown local addresses are created on first delivery, random
per-test addresses like `user-8f21a@tahidromos.test` just work.

## Light and dark

The UI follows whichever you pick; the choice is remembered.

![Light theme](docs/screenshots/light-theme.png)

## Development

```sh
make dev      # build the image from this checkout and run it
make test     # run the whole suite against the running server
make seed     # fill it with realistic conversations
make logs
make clean    # delete every mailbox and message
```

`make help` lists everything.

### Tests

The suite is deliberately written against plain `smtplib`, `imaplib` and
`requests` rather than the project's own code, so it proves the *server* works
rather than that the helpers agree with themselves.

```
tests/test_delivery.py    delivery, CC, plus-addressing, TLS, capture
tests/test_threading.py   replies, replies to replies, 10-deep chains
tests/test_api.py         the REST harness
tests/test_multiapp.py    per-app domains, credentials, isolation, badges
tests/test_echobot.py     auto-responders and bot-to-bot threading
tests/test_ui.py          the mailbox browser and its data
```

### What is inside

| Module | Role |
| --- | --- |
| `tahidromos/smtp.py` | SMTP + submission server (RFC 5321, AUTH, STARTTLS) |
| `tahidromos/imap.py` | IMAP4rev1 server (RFC 3501, UID commands, IDLE) |
| `tahidromos/store.py` | SQLite message store: mailboxes, UIDs, flags |
| `tahidromos/message.py` | Message building and the three rules of threading |
| `tahidromos/router.py` | Delivery, auto-creation, and the capture safety net |
| `tahidromos/bot.py` | Auto-responders |
| `tahidromos/config.py` | Multi-app configuration |
| `tahidromos/api.py` | REST API and the mailbox browser |

Four runtime dependencies: FastAPI, uvicorn, PyYAML and cryptography. The mail
servers themselves use only the standard library.

## Not for production

Self-signed certificates, plaintext authentication, no spam filtering, no
SPF/DKIM/DMARC checks, and an open relay on port 25 inside the container
network. Every one of those is a deliberate choice to make development
frictionless, and a reason never to expose this to the internet.

## Name

*ταχυδρόμος* (tahidromos) — Greek for **postman**.

## License

MIT
