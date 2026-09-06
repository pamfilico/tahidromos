#!/usr/bin/env python3
"""Send a mail through tahidromos and confirm it arrived.

The point of this script is to be boring: plain smtplib, plain imaplib, no
project imports.  If this works, tahidromos works.

    python3 examples/send_email.py
    python3 examples/send_email.py --from alice --to bob --subject "hi" --body "there"
    python3 examples/send_email.py --to stranger@gmail.com   # captured by Mailpit
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
from email.utils import make_msgid

DEFAULT_DOMAIN = "tahidromos.test"


def qualify(user: str, domain: str) -> str:
    return user if "@" in user else f"{user}@{domain}"


def send(args) -> str:
    sender = qualify(args.sender, args.domain)
    recipient = qualify(args.to, args.domain)

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = args.subject
    message["Message-ID"] = make_msgid(domain=args.domain)
    message.set_content(args.body)

    print(f"→ connecting to SMTP {args.smtp_host}:{args.smtp_port}")
    with smtplib.SMTP(args.smtp_host, args.smtp_port, timeout=20) as smtp:
        smtp.ehlo()
        if not args.no_auth:
            smtp.login(sender, args.password)
        smtp.send_message(message)
    print(f"→ sent {message['Message-ID']}")
    print(f"  from    {sender}")
    print(f"  to      {recipient}")
    print(f"  subject {args.subject}")
    return str(message["Message-ID"])


def wait_for_delivery(args, message_id: str) -> bool:
    """Poll the recipient's INBOX over IMAP until the message shows up."""
    recipient = qualify(args.to, args.domain)
    if not recipient.endswith("@" + args.domain):
        print(f"\n! {recipient} is outside {args.domain}: it was routed to Mailpit.")
        print(f"  Look for it at http://localhost:8025")
        return True

    print(f"\n→ waiting for delivery to {recipient} over IMAP "
          f"{args.imap_host}:{args.imap_port}")
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        try:
            with imaplib.IMAP4(args.imap_host, args.imap_port, timeout=10) as imap:
                imap.login(recipient, args.password)
                imap.select("INBOX")
                status, data = imap.uid("SEARCH", None, "ALL")
                for uid in (data[0] or b"").split():
                    _, fetched = imap.uid("FETCH", uid, "(BODY.PEEK[])")
                    parsed = email.message_from_bytes(fetched[0][1], policy=email.policy.default)
                    if str(parsed["Message-ID"]).strip() == message_id.strip():
                        print(f"✓ delivered — uid {uid.decode()}")
                        print(f"  Subject: {parsed['Subject']}")
                        print(f"  From:    {parsed['From']}")
                        print(f"  Date:    {parsed['Date']}")
                        body = parsed.get_body(preferencelist=("plain",))
                        if body is not None:
                            print(f"  Body:    {body.get_content().strip()[:200]}")
                        return True
        except Exception as exc:  # server may still be starting
            print(f"  ... {exc.__class__.__name__}: {exc}")
        time.sleep(1)

    print(f"✗ not delivered within {args.timeout}s")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="sender", default="alice")
    parser.add_argument("--to", default="bob")
    parser.add_argument("--subject", default="Hello from tahidromos")
    parser.add_argument("--body", default="If you can read this, the dev mail server works.")
    parser.add_argument("--domain", default=DEFAULT_DOMAIN)
    parser.add_argument("--password", default="password")
    parser.add_argument("--smtp-host", default="localhost")
    parser.add_argument("--smtp-port", type=int, default=1587)
    parser.add_argument("--imap-host", default="localhost")
    parser.add_argument("--imap-port", type=int, default=1143)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--no-auth", action="store_true",
                        help="Skip SMTP AUTH (use with --smtp-port 1025)")
    parser.add_argument("--no-verify", action="store_true", help="Send without checking delivery")
    args = parser.parse_args()

    message_id = send(args)
    if args.no_verify:
        return 0
    return 0 if wait_for_delivery(args, message_id) else 1


if __name__ == "__main__":
    sys.exit(main())
