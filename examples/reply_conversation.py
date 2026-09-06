#!/usr/bin/env python3
"""Walk a full conversation: send → reply → reply to the reply.

Uses nothing but smtplib and imaplib, so it doubles as a worked example of
how your own app should thread its replies.

    python3 examples/reply_conversation.py
    python3 examples/reply_conversation.py --turns 8
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


class Mailbox:
    def __init__(self, user: str, args):
        self.address = user if "@" in user else f"{user}@{args.domain}"
        self.args = args

    def _smtp(self):
        smtp = smtplib.SMTP(self.args.smtp_host, self.args.smtp_port, timeout=20)
        smtp.ehlo()
        smtp.login(self.address, self.args.password)
        return smtp

    def send(self, to: str, subject: str, body: str) -> str:
        message = EmailMessage()
        message["From"] = self.address
        message["To"] = to
        message["Subject"] = subject
        message["Message-ID"] = make_msgid(domain=self.args.domain)
        message.set_content(body)
        with self._smtp() as smtp:
            smtp.send_message(message)
        return str(message["Message-ID"])

    def reply(self, original: EmailMessage, body: str) -> str:
        # --- the three rules of threading -------------------------------
        # 1. In-Reply-To  = the parent's Message-ID
        # 2. References   = the parent's References, then the parent's Message-ID
        # 3. Subject      = "Re: " once, not once per reply
        parent_id = str(original["Message-ID"]).strip()
        references = str(original.get("References", "")).split()
        if parent_id not in references:
            references.append(parent_id)

        subject = str(original["Subject"])
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        reply = EmailMessage()
        reply["From"] = self.address
        reply["To"] = parseaddr(str(original.get("Reply-To") or original["From"]))[1]
        reply["Subject"] = subject
        reply["Message-ID"] = make_msgid(domain=self.args.domain)
        reply["In-Reply-To"] = parent_id
        reply["References"] = " ".join(references)
        reply.set_content(body)

        with self._smtp() as smtp:
            smtp.send_message(reply)
        return str(reply["Message-ID"])

    def wait_for(self, message_id: str, timeout: float = 40.0) -> EmailMessage:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with imaplib.IMAP4(self.args.imap_host, self.args.imap_port, timeout=15) as imap:
                    imap.login(self.address, self.args.password)
                    imap.select("INBOX")
                    _, data = imap.uid("SEARCH", None, "ALL")
                    for uid in (data[0] or b"").split():
                        _, fetched = imap.uid("FETCH", uid, "(BODY.PEEK[])")
                        parsed = email.message_from_bytes(fetched[0][1], policy=POLICY)
                        if str(parsed["Message-ID"]).strip() == message_id.strip():
                            return parsed
            except Exception:
                pass
            time.sleep(0.5)
        raise SystemExit(f"✗ {self.address} never received {message_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--a", default="alice")
    parser.add_argument("--b", default="bob")
    parser.add_argument("--turns", type=int, default=5)
    parser.add_argument("--subject", default="Let's talk")
    parser.add_argument("--domain", default="tahidromos.test")
    parser.add_argument("--password", default="password")
    parser.add_argument("--smtp-host", default="localhost")
    parser.add_argument("--smtp-port", type=int, default=1587)
    parser.add_argument("--imap-host", default="localhost")
    parser.add_argument("--imap-port", type=int, default=1143)
    args = parser.parse_args()

    people = [Mailbox(args.a, args), Mailbox(args.b, args)]
    subject = f"{args.subject} ({int(time.time())})"

    print(f"1. {people[0].address} opens the thread")
    current_id = people[0].send(people[1].address, subject, "Turn 1 — opening message.")
    root_id = current_id

    for turn in range(2, args.turns + 1):
        speaker = people[(turn - 1) % 2]
        received = speaker.wait_for(current_id)
        current_id = speaker.reply(received, f"Turn {turn} — replying to the previous message.")
        print(f"{turn}. {speaker.address} replies")
        print(f"     In-Reply-To: {received['Message-ID']}")

    final = people[args.turns % 2].wait_for(current_id)
    references = str(final["References"]).split()

    print("\n--- final message ---------------------------------------------")
    print(f"Subject     : {final['Subject']}")
    print(f"From        : {final['From']}")
    print(f"Message-ID  : {final['Message-ID']}")
    print(f"In-Reply-To : {final['In-Reply-To']}")
    print(f"References  : {len(references)} ancestors")
    for index, reference in enumerate(references, 1):
        print(f"   {index}. {reference}")

    assert references[0] == root_id.strip(), "thread root drifted!"
    assert str(final["Subject"]).lower().count("re:") == 1, "Re: prefix doubled!"
    print(f"\n✓ {args.turns} messages, one unbroken thread rooted at {root_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
