"""Canned messages that are awkward to produce by hand.

Every one of these is a real shape you eventually have to handle: a bounce,
a newsletter with List-Unsubscribe, an HTML-only message, a reply buried
under three levels of quoting, a message with an attachment, one written in
a script that is not Latin.

    curl -sX POST localhost:8080/scenarios -d '{"name":"bounce","to":"alice"}'

They are deterministic: same name and seed, same bytes.
"""

from __future__ import annotations

import base64
import random
from dataclasses import dataclass
from email.message import EmailMessage, MIMEPart
from email.utils import formatdate, make_msgid
from typing import Callable

from . import message as msg


@dataclass(frozen=True)
class Scenario:
    name: str
    summary: str
    build: Callable[..., EmailMessage]


def _base(sender: str, to: str, subject: str, domain: str,
          rng: random.Random) -> EmailMessage:
    message = EmailMessage(policy=msg.POLICY)
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = f"<{rng.getrandbits(64):016x}@{domain}>"
    return message


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------


def bounce(to: str, domain: str, rng: random.Random, **_) -> EmailMessage:
    """A delivery status notification, the shape a real MTA sends back."""
    failed = f"nobody-{rng.getrandbits(16):04x}@example.invalid"
    message = _base(f"MAILER-DAEMON@{domain}", to,
                    "Undelivered Mail Returned to Sender", domain, rng)
    message["Auto-Submitted"] = "auto-replied"
    message.set_content(
        f"This is the mail system at host {domain}.\n\n"
        f"I'm sorry to have to inform you that your message could not\n"
        f"be delivered to one or more recipients.\n\n"
        f"<{failed}>: host example.invalid said:\n"
        f"    550 5.1.1 <{failed}>: Recipient address rejected:\n"
        f"    User unknown in virtual mailbox table\n"
    )
    # The machine-readable half, as a real RFC 3464 report: message/delivery-status
    # is a sequence of header-only blocks, not a text body.
    per_message = MIMEPart(policy=msg.POLICY)
    per_message["Reporting-MTA"] = f"dns; {domain}"

    per_recipient = MIMEPart(policy=msg.POLICY)
    per_recipient["Final-Recipient"] = f"rfc822; {failed}"
    per_recipient["Action"] = "failed"
    per_recipient["Status"] = "5.1.1"
    per_recipient["Diagnostic-Code"] = "smtp; 550 5.1.1 User unknown"

    report = MIMEPart(policy=msg.POLICY)
    report.set_type("message/delivery-status")
    report.set_payload([per_message, per_recipient])

    message.make_mixed()
    message.attach(report)
    message.set_type("multipart/report")
    message.set_param("report-type", "delivery-status")
    return message


def newsletter(to: str, domain: str, rng: random.Random, **_) -> EmailMessage:
    """A bulk mailing with the headers a good citizen sets."""
    token = f"{rng.getrandbits(48):012x}"
    message = _base(f"news@{domain}", to, "Your weekly digest", domain, rng)
    message["List-Unsubscribe"] = (
        f"<https://{domain}/unsubscribe?token={token}>, "
        f"<mailto:unsubscribe@{domain}?subject={token}>")
    message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    message["List-Id"] = f"Weekly digest <digest.{domain}>"
    message["Precedence"] = "bulk"
    message.set_content(
        "Three things worth reading this week.\n\n"
        f"1. https://{domain}/posts/one\n"
        f"2. https://{domain}/posts/two\n"
        f"3. https://{domain}/posts/three\n\n"
        f"Unsubscribe: https://{domain}/unsubscribe?token={token}\n")
    message.add_alternative(
        f"<html><body><h1>Your weekly digest</h1>"
        f"<ol><li><a href='https://{domain}/posts/one'>One</a></li>"
        f"<li><a href='https://{domain}/posts/two'>Two</a></li>"
        f"<li><a href='https://{domain}/posts/three'>Three</a></li></ol>"
        f"<p><a href='https://{domain}/unsubscribe?token={token}'>Unsubscribe</a></p>"
        f"</body></html>", subtype="html")
    return message


def html_only(to: str, domain: str, rng: random.Random, **_) -> EmailMessage:
    """No plain-text part at all — the case that breaks naive body parsing."""
    message = _base(f"marketing@{domain}", to, "Only HTML in here", domain, rng)
    message.add_header("Content-Type", "text/html; charset=utf-8")
    message.set_payload(
        "<html><body style='font-family:sans-serif'>"
        "<h2>No plain text part</h2>"
        f"<p>Your code is <b>558102</b>.</p>"
        f"<p><a href='https://{domain}/confirm?id=9f2a'>Confirm your address</a></p>"
        "</body></html>", charset="utf-8")
    return message


def otp(to: str, domain: str, rng: random.Random, **_) -> EmailMessage:
    """A sign-in code and a magic link, the two things e2e tests fish for."""
    code = f"{rng.randrange(100000, 999999)}"
    token = f"{rng.getrandbits(64):016x}"
    message = _base(f"noreply@{domain}", to, "Your sign-in code", domain, rng)
    message.set_content(
        f"Your verification code is {code}.\n\n"
        f"Or sign in with one click:\n"
        f"https://{domain}/magic?token={token}\n\n"
        f"The code expires in 10 minutes.\n\n"
        f"--\nThe {domain} team\n")
    return message


def deep_reply(to: str, domain: str, rng: random.Random, **_) -> EmailMessage:
    """A short reply buried under three levels of quoting and a signature."""
    root = f"<{rng.getrandbits(64):016x}@{domain}>"
    first = f"<{rng.getrandbits(64):016x}@{domain}>"
    second = f"<{rng.getrandbits(64):016x}@{domain}>"
    message = _base(f"customer@{domain}", to, "Re: Re: Re: Your order", domain, rng)
    message["In-Reply-To"] = second
    message["References"] = f"{root} {first} {second}"
    message.set_content(
        "Yes, please cancel it.\n\n"
        "On Sun, 06 Sep 2026 at 20:38, support wrote:\n"
        "> Would you like us to cancel the order?\n"
        ">\n"
        "> On Sun, 06 Sep 2026 at 20:12, customer wrote:\n"
        "> > It still has not arrived.\n"
        "> >\n"
        "> > On Sun, 06 Sep 2026 at 19:55, support wrote:\n"
        "> > > Your order shipped on Tuesday.\n\n"
        "--\n"
        "Sent from my iPhone\n")
    return message


def attachment(to: str, domain: str, rng: random.Random, **_) -> EmailMessage:
    """A small PDF and a CSV, so attachment handling has something to chew on."""
    message = _base(f"billing@{domain}", to, "Invoice #10432", domain, rng)
    message.set_content("Your invoice is attached.\n")
    pdf = base64.b64decode(
        "JVBERi0xLjQKMSAwIG9iago8PC9UeXBlL0NhdGFsb2cvUGFnZXMgMiAwIFI+PgplbmRvYmoK"
        "dHJhaWxlcgo8PC9Sb290IDEgMCBSPj4KJSVFT0YK")
    message.add_attachment(pdf, maintype="application", subtype="pdf",
                           filename="invoice-10432.pdf")
    message.add_attachment(b"item,qty,price\nWidget,2,19.99\nGadget,1,49.00\n",
                           maintype="text", subtype="csv", filename="lines.csv")
    return message


def unicode_subject(to: str, domain: str, rng: random.Random, **_) -> EmailMessage:
    """Non-Latin subject and body, so encoding bugs show up early."""
    message = _base(f"γραμματεία@{domain}", to,
                    "Παραγγελία #10432 — επιβεβαίωση 📮", domain, rng)
    message.set_content(
        "Καλησπέρα,\n\nΗ παραγγελία σας επιβεβαιώθηκε.\n\n"
        "日本語のテキストもここにあります。\n\nΜε εκτίμηση,\nη ομάδα\n")
    return message


def auto_reply(to: str, domain: str, rng: random.Random, **_) -> EmailMessage:
    """An out-of-office. Your app must not answer this, or you get a loop."""
    message = _base(f"someone@{domain}", to, "Automatic reply: out of office", domain, rng)
    message["Auto-Submitted"] = "auto-replied"
    message["X-Auto-Response-Suppress"] = "All"
    message["Precedence"] = "bulk"
    message.set_content("I am away until Monday and will reply when I am back.\n")
    return message


def signed(to: str, domain: str, rng: random.Random, **_) -> EmailMessage:
    """Carries DKIM/SPF/DMARC headers, for code that reads them.

    The signature is not cryptographically valid — this server does not sign
    mail. It is here so header parsing has something realistic to read.
    """
    message = _base(f"noreply@{domain}", to, "Signed message", domain, rng)
    body_hash = base64.b64encode(bytes(rng.getrandbits(8) for _ in range(32))).decode()
    signature = base64.b64encode(bytes(rng.getrandbits(8) for _ in range(64))).decode()
    # Long headers are folded by the policy on the way out, so keep it flat here.
    message["DKIM-Signature"] = (
        f"v=1; a=rsa-sha256; c=relaxed/relaxed; d={domain}; s=default; "
        f"t={rng.getrandbits(31)}; bh={body_hash}; "
        f"h=from:to:subject:date; b={signature}")
    message["Authentication-Results"] = (
        f"{domain}; dkim=pass header.d={domain}; spf=pass smtp.mailfrom={domain}; "
        f"dmarc=pass header.from={domain}")
    message["Received-SPF"] = f"pass ({domain}: domain of {domain} designates 127.0.0.1)"
    message.set_content("This message carries authentication headers.\n")
    return message


def large(to: str, domain: str, rng: random.Random, size_kb: int = 512, **_) -> EmailMessage:
    """A big body, to check nothing chokes on size."""
    message = _base(f"reports@{domain}", to, f"Report ({size_kb} KB)", domain, rng)
    filler = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz \n") for _ in range(size_kb * 1024))
    message.set_content(f"A large message follows.\n\n{filler}")
    return message


SCENARIOS: dict[str, Scenario] = {
    scenario.name: scenario
    for scenario in [
        Scenario("bounce", "A delivery status notification from MAILER-DAEMON", bounce),
        Scenario("newsletter", "Bulk mail with List-Unsubscribe and an HTML part", newsletter),
        Scenario("html_only", "HTML with no plain-text alternative", html_only),
        Scenario("otp", "A sign-in code and a magic link", otp),
        Scenario("deep_reply", "A short reply under three levels of quoting", deep_reply),
        Scenario("attachment", "A PDF and a CSV attached", attachment),
        Scenario("unicode", "Greek and Japanese subject and body", unicode_subject),
        Scenario("auto_reply", "An out-of-office your app must not answer", auto_reply),
        Scenario("signed", "DKIM, SPF and DMARC headers to parse", signed),
        Scenario("large", "A large body (size_kb, default 512)", large),
    ]
}


def catalogue() -> list[dict]:
    return [{"name": s.name, "summary": s.summary} for s in SCENARIOS.values()]


# A fixed point in time for seeded builds, so Date is reproducible too.
REFERENCE_EPOCH = 1_788_000_000


def _make_reproducible(message: EmailMessage, rng: random.Random) -> None:
    """Pin the two things that are otherwise random on every build.

    MIME boundaries and Date are generated fresh each time, which would make
    a seeded scenario differ byte-for-byte between runs — the opposite of
    what a fixture is for.
    """
    for part in message.walk():
        if part.is_multipart():
            part.set_boundary(f"=_tahidromos_{rng.getrandbits(64):016x}")
    del message["Date"]
    message["Date"] = formatdate(REFERENCE_EPOCH + rng.randrange(0, 86_400), localtime=False)


def build(name: str, to: str, domain: str, seed: int | None = None, **options) -> EmailMessage:
    """Build one scenario. With a seed, the bytes are identical every time."""
    scenario = SCENARIOS.get(name)
    if scenario is None:
        raise KeyError(name)
    rng = random.Random(seed if seed is not None else random.getrandbits(32))
    message = scenario.build(to=to, domain=domain, rng=rng, **options)
    if seed is not None:
        _make_reproducible(message, random.Random(seed ^ 0x5EED))
    return message
