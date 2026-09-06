#!/usr/bin/env python3
"""Fill the mail server with a realistic set of conversations.

Handy for demos, for screenshots, and for seeing the UI with something in it.

    python3 examples/seed_demo.py
"""

from __future__ import annotations

import argparse
import email
import email.policy
import imaplib
import smtplib
import sys
import time
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr

POLICY = email.policy.default


class Sender:
    def __init__(self, host: str, port: int):
        self.host, self.port = host, port

    def send(self, auth_user: str, auth_password: str, author: str, to: str,
             subject: str, body: str, parent: EmailMessage | None = None) -> str:
        """Authenticate once as the app, send as any of that app's addresses.

        This is how a real application behaves: one set of SMTP credentials,
        many From addresses.
        """
        message = EmailMessage()
        message["From"] = author
        message["To"] = to
        message["Message-ID"] = make_msgid(domain=author.split("@")[1])
        if parent is not None:
            parent_id = str(parent["Message-ID"]).strip()
            references = str(parent.get("References", "")).split()
            if parent_id not in references:
                references.append(parent_id)
            message["In-Reply-To"] = parent_id
            message["References"] = " ".join(references)
            subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(self.host, self.port, timeout=20) as smtp:
            smtp.ehlo()
            smtp.login(auth_user, auth_password)
            smtp.send_message(message)
        return str(message["Message-ID"])


def fetch(address: str, password: str, message_id: str, host: str, port: int,
          timeout: float = 30) -> EmailMessage:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with imaplib.IMAP4(host, port, timeout=15) as imap:
                imap.login(address, password)
                imap.select("INBOX")
                _, data = imap.uid("SEARCH", None, "ALL")
                for uid in (data[0] or b"").split():
                    _, fetched = imap.uid("FETCH", uid, "(BODY.PEEK[])")
                    parsed = email.message_from_bytes(fetched[0][1], policy=POLICY)
                    if str(parsed["Message-ID"]).strip() == message_id.strip():
                        return parsed
        except Exception:
            pass
        time.sleep(0.4)
    raise SystemExit(f"{address} never received {message_id}")


CONVERSATIONS = [
    # (app smtp user, password, thread)
    ("app@tahidromos.test", "password", [
        ("alice@tahidromos.test", "bob@tahidromos.test", "Ship the billing flow on Friday?",
         "The new billing flow is on staging and QA signed off this morning.\n"
         "Are you happy to ship it Friday afternoon?"),
        ("bob@tahidromos.test", "alice@tahidromos.test", None,
         "Friday works. Can we put it behind a flag for the first week?"),
        ("alice@tahidromos.test", "bob@tahidromos.test", None,
         "Yes — flag is already wired up, default off. I'll enable it for internal accounts first."),
        ("bob@tahidromos.test", "alice@tahidromos.test", None,
         "Perfect. I'll write the rollback note before I log off."),
    ]),
    ("app@tahidromos.test", "password", [
        ("carol@tahidromos.test", "alice@tahidromos.test", "Design review — checkout mock",
         "Left comments on the checkout mock. The main one: the summary panel\n"
         "competes with the pay button on mobile."),
        ("alice@tahidromos.test", "carol@tahidromos.test", None,
         "Good catch. I'll collapse the summary behind a disclosure under 640px."),
    ]),
    ("shop@shop.test", "shop-secret", [
        ("orders@shop.test", "customer@shop.test", "Order #10432 confirmed",
         "Thanks for your order! It ships tomorrow and should arrive Thursday."),
        ("customer@shop.test", "orders@shop.test", None,
         "Could you send it to my work address instead? 14 Kifisias Ave."),
        ("orders@shop.test", "customer@shop.test", None,
         "Updated — it now ships to 14 Kifisias Ave. Nothing else changes."),
    ]),
    ("crm@crm.test", "crm-secret", [
        ("sales@crm.test", "agent@crm.test", "New lead: Acme Corp",
         "Acme requested a demo of the enterprise tier. 200 seats, wants SSO."),
    ]),
]

EXTERNAL = [
    ("noreply@tahidromos.test", "password", "someone@gmail.com", "Your weekly digest",
     "This one is addressed outside the local domains, so it is captured by Mailpit\n"
     "instead of reaching the real internet."),
    ("noreply@tahidromos.test", "password", "customer@example.com", "Password reset",
     "Click the link to reset your password. Also captured, never delivered."),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--smtp-host", default="localhost")
    parser.add_argument("--smtp-port", type=int, default=1587)
    parser.add_argument("--imap-host", default="localhost")
    parser.add_argument("--imap-port", type=int, default=1143)
    parser.add_argument("--bot", action="store_true",
                        help="Also poke the auto-responder so a bot thread appears")
    parser.add_argument("--templates", action="store_true",
                        help="Also deliver one of every template, for the preview")
    parser.add_argument("--api", default="http://localhost:8080",
                        help="Base URL of the REST API [http://localhost:8080]")
    args = parser.parse_args()

    sender = Sender(args.smtp_host, args.smtp_port)
    threads = 0

    for smtp_user, smtp_password, turns in CONVERSATIONS:
        parent = None
        subject = turns[0][2]
        for author, recipient, turn_subject, body in turns:
            message_id = sender.send(smtp_user, smtp_password, author, recipient,
                                     turn_subject or subject, body, parent)
            # the reply has to be threaded onto the delivered copy
            password = "agent-only-password" if recipient == "agent@crm.test" else \
                ("shop-secret" if recipient.endswith("@shop.test") and recipient.startswith("shop@")
                 else "password")
            parent = fetch(recipient, password, message_id, args.imap_host, args.imap_port)
            print(f"  {author.split('@')[0]:9} → {recipient:24} {turn_subject or subject}")
        threads += 1

    for smtp_user, password, to, subject, body in EXTERNAL:
        sender.send(smtp_user, password, smtp_user, to, subject, body)
        print(f"  captured → {to:24} {subject}")

    if args.templates:
        import json as _json
        import urllib.request as _request

        for name in ("welcome", "otp", "password_reset", "receipt",
                     "digest", "alert", "invite", "verify_email"):
            payload = _json.dumps({"name": name, "to": "alice"}).encode()
            call = _request.Request(f"{args.api}/templates", data=payload,
                                    headers={"content-type": "application/json"},
                                    method="POST")
            try:
                with _request.urlopen(call, timeout=30) as response:
                    subject = _json.loads(response.read())["subject"]
                print(f"  template  → alice{'':18} {subject}")
            except Exception as exc:
                print(f"  template  → {name}: could not send ({exc})")

    if args.bot:
        sender.send("app@tahidromos.test", "password", "alice@tahidromos.test",
                    "echo@tahidromos.test", "Testing the auto-responder",
                    "Reply to me please.")
        print("  poked echo@tahidromos.test — it will reply within a few seconds")

    print(f"\n✓ seeded {threads} threads plus {len(EXTERNAL)} captured messages")
    print("  open http://localhost:8080 to browse them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
