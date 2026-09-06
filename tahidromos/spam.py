"""Spam scoring.

Two engines, because they answer different questions:

  * a built-in heuristic scorer, always on and dependency-free. It applies a
    set of well-known SpamAssassin-flavoured rules and tells you which ones
    fired, so "why did this score 7.5?" has an answer.

  * Rspamd, if you point RSPAMD_URL at one. Rspamd is a real, actively
    developed filter; when you want a verdict close to what production will
    say, run it alongside and the score comes from there instead.

The built-in scorer is for testing your own "is this spammy?" handling and
for catching the obvious own-goals — an HTML-only bulk mail with no
unsubscribe header, a subject in capitals, a display name pretending to be
someone else. It is not a production filter and does not pretend to be.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import parseaddr

from . import message as msg

log = logging.getLogger("tahidromos.spam")

# Thresholds chosen to line up with SpamAssassin's habits: 5.0 is the
# classic default cut-off, and everything under 2.0 is unremarkable.
SUSPICIOUS_AT = 2.0
SPAM_AT = 5.0


@dataclass(frozen=True)
class Hit:
    rule: str
    weight: float
    description: str

    def as_dict(self) -> dict:
        return {"rule": self.rule, "weight": self.weight, "description": self.description}


SHORTENERS = {"bit.ly", "tinyurl.com", "goo.gl", "t.co", "ow.ly", "is.gd",
              "buff.ly", "rebrand.ly", "cutt.ly", "shorturl.at"}

LOUD_PHRASES = [
    (r"\bviagra\b", "pharmacy spam term"),
    (r"\bfree\s+money\b", "free money"),
    (r"\b(act|order|buy)\s+now\b", "urgency phrase"),
    (r"\blimited\s+time\s+offer\b", "urgency phrase"),
    (r"\bclick\s+here\s+now\b", "aggressive call to action"),
    (r"\byou\s+(have\s+)?won\b", "prize claim"),
    (r"\b(lottery|jackpot)\b", "prize claim"),
    (r"\brisk[-\s]?free\b", "risk-free claim"),
    (r"\bno\s+credit\s+check\b", "credit claim"),
    (r"\b100%\s+(free|guaranteed)\b", "absolute guarantee"),
    (r"\bwire\s+transfer\b", "wire transfer"),
    (r"\b(bitcoin|crypto)\s+(giveaway|doubling)\b", "crypto giveaway"),
    (r"\bdear\s+(friend|winner|customer)\b", "generic salutation"),
    (r"\bunclaimed\s+funds?\b", "advance-fee pattern"),
]

HIDDEN_TEXT = re.compile(
    r"font-size\s*:\s*0(?:\.0+)?(px|pt|em)?|display\s*:\s*none|visibility\s*:\s*hidden",
    re.IGNORECASE)

SCRIPT_RANGES = [
    ("latin", re.compile(r"[A-Za-z]")),
    ("cyrillic", re.compile(r"[Ѐ-ӿ]")),
    ("greek", re.compile(r"[Ͱ-Ͽ]")),
]


# Nearly every real template opens with a hidden preheader div — the inbox
# preview line. Flagging that would mark all well-built mail as spam, so only
# hidden text elsewhere, or a lot of it, counts.
PREHEADER_WINDOW = 1500
PREHEADER_MAX_CHARS = 400

HIDDEN_BLOCK = re.compile(
    r"<(div|span|p|td)\b[^>]*style=[\"\']([^\"\']*)[\"\'][^>]*>(.*?)</\1>",
    re.IGNORECASE | re.DOTALL)


def _hidden_text_beyond_preheader(html: str) -> int:
    """Characters of hidden text that are not just the preheader."""
    total = 0
    for match in HIDDEN_BLOCK.finditer(html or ""):
        style, inner = match.group(2), match.group(3)
        if not HIDDEN_TEXT.search(style):
            continue
        visible = re.sub(r"<[^>]+>", "", inner)
        # zero-width padding characters are preheader filler, not content
        visible = re.sub(r"[\u200b-\u200f\u2060\ufeff\s]+", "", visible)
        if match.start() < PREHEADER_WINDOW and len(visible) <= PREHEADER_MAX_CHARS:
            continue
        total += len(visible)
    return total


def _text_of(message: EmailMessage) -> tuple[str, str]:
    return msg.plain_body(message), (msg.html_body(message) or "")


def score_message(raw: bytes) -> dict:
    """Apply every rule and return the score with its breakdown."""
    message = msg.parse(raw)
    subject = str(message.get("Subject", ""))
    text, html = _text_of(message)
    body = f"{text}\n{re.sub(r'<[^>]+>', ' ', html)}"
    display_name, from_address = parseaddr(str(message.get("From", "")))
    hits: list[Hit] = []

    def hit(rule: str, weight: float, description: str) -> None:
        hits.append(Hit(rule, weight, description))

    # -- headers ---------------------------------------------------------
    if not str(message.get("Message-ID", "")).strip():
        hit("MISSING_MESSAGE_ID", 1.5, "No Message-ID header")
    if not str(message.get("Date", "")).strip():
        hit("MISSING_DATE", 1.0, "No Date header")
    if not from_address or "@" not in from_address:
        hit("BAD_FROM", 2.5, "From is missing or malformed")

    # A display name containing a different address is the classic phish.
    if display_name and "@" in display_name:
        shown = parseaddr(display_name)[1] or display_name
        if shown.lower().strip("<> ") != from_address.lower():
            hit("FROM_DISPLAY_SPOOF", 3.0,
                f"Display name shows {shown!r} but the address is {from_address!r}")

    reply_to = parseaddr(str(message.get("Reply-To", "")))[1]
    if reply_to and from_address and \
            reply_to.rsplit("@", 1)[-1].lower() != from_address.rsplit("@", 1)[-1].lower():
        hit("REPLY_TO_MISMATCH", 1.2,
            f"Reply-To domain ({reply_to}) differs from From ({from_address})")

    if str(message.get("Precedence", "")).lower() in {"bulk", "list"} and \
            not message.get("List-Unsubscribe"):
        hit("BULK_NO_UNSUBSCRIBE", 2.0, "Bulk precedence with no List-Unsubscribe header")

    # -- subject ---------------------------------------------------------
    letters = [c for c in subject if c.isalpha()]
    if len(letters) >= 8 and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        hit("SUBJECT_ALL_CAPS", 1.5, "Subject is mostly capitals")
    if subject.count("!") >= 3:
        hit("SUBJECT_EXCLAMATION", 1.0, f"{subject.count('!')} exclamation marks in the subject")
    if re.search(r"https?://", subject):
        hit("URL_IN_SUBJECT", 1.5, "A URL in the subject line")
    if re.search(r"[\U0001F300-\U0001FAFF]{3,}", subject):
        hit("SUBJECT_EMOJI_PILE", 0.8, "Three or more consecutive emoji in the subject")

    # -- body ------------------------------------------------------------
    for pattern, why in LOUD_PHRASES:
        if re.search(pattern, body, re.IGNORECASE):
            hit("LOUD_PHRASE", 1.2, why)

    body_letters = [c for c in body if c.isalpha()]
    if len(body_letters) >= 60 and \
            sum(c.isupper() for c in body_letters) / len(body_letters) > 0.6:
        hit("BODY_ALL_CAPS", 1.5, "Body is mostly capitals")

    if html and not text.strip():
        hit("HTML_ONLY", 1.0, "No plain-text alternative")

    if html and text.strip():
        ratio = len(html) / max(len(text), 1)
        # A well-built marketing template is legitimately 20-40x its text part,
        # so only a wild imbalance is worth a point.
        if ratio > 45:
            hit("HTML_TEXT_RATIO", 1.0, f"HTML is {ratio:.0f}x the size of the text part")

    hidden = _hidden_text_beyond_preheader(html)
    if hidden:
        hit("HIDDEN_TEXT", 2.0, f"{hidden} characters of hidden text below the preheader")

    # Stripping tags throws away href targets, so scan the attributes too.
    link_source = body + " " + " ".join(re.findall(r"""href\s*=\s*["']([^"']+)""", html))
    links = re.findall(r"https?://([^/\s\"'>]+)", link_source)
    if len(links) > 12:
        hit("MANY_LINKS", 1.0, f"{len(links)} links")
    for host in links:
        if host.lower().lstrip("www.") in SHORTENERS:
            hit("URL_SHORTENER", 1.2, f"Link through a shortener ({host})")
            break
    if re.search(r"https?://\d{1,3}(\.\d{1,3}){3}", link_source):
        hit("URL_BARE_IP", 2.0, "A link to a bare IP address")

    # Mixed scripts in a domain is how homograph attacks read.
    for host in set(links):
        present = [name for name, pattern in SCRIPT_RANGES if pattern.search(host)]
        if len(present) > 1:
            hit("URL_MIXED_SCRIPT", 2.5,
                f"Link host {host!r} mixes {' and '.join(present)} characters")
            break

    # -- attachments -----------------------------------------------------
    for part in message.walk():
        filename = part.get_filename() or ""
        if re.search(r"\.(exe|scr|bat|cmd|js|vbs|jar|com|pif)$", filename, re.IGNORECASE):
            hit("EXECUTABLE_ATTACHMENT", 4.0, f"Executable attachment: {filename}")
        if re.search(r"\.(zip|rar|7z)$", filename, re.IGNORECASE):
            hit("ARCHIVE_ATTACHMENT", 0.8, f"Archive attachment: {filename}")

    total = round(sum(h.weight for h in hits), 2)
    return {
        "engine": "builtin",
        "score": total,
        "required_score": SPAM_AT,
        "verdict": verdict_for(total),
        "hits": [h.as_dict() for h in hits],
        "summary": _summary(total, hits),
    }


def verdict_for(score: float) -> str:
    if score >= SPAM_AT:
        return "spam"
    if score >= SUSPICIOUS_AT:
        return "suspicious"
    return "ham"


def _summary(score: float, hits: list[Hit]) -> str:
    if not hits:
        return "Nothing flagged."
    worst = sorted(hits, key=lambda h: -h.weight)[:3]
    return f"{score} from {len(hits)} rule(s); worst: " + ", ".join(h.rule for h in worst)


# --------------------------------------------------------------------------
# Rspamd
# --------------------------------------------------------------------------


class Rspamd:
    """A thin client for Rspamd's HTTP API.

    Rspamd is a separate service. Run it alongside and set RSPAMD_URL; see
    the compose profile in the README. Without it the built-in scorer is used.
    """

    def __init__(self, url: str, password: str = "", timeout: float = 10.0):
        self.url = url.rstrip("/")
        self.password = password
        self.timeout = timeout

    def check(self, raw: bytes, recipient: str = "", sender: str = "") -> dict:
        headers = {"content-type": "text/plain"}
        if self.password:
            headers["Password"] = self.password
        if recipient:
            headers["Deliver-To"] = recipient
        if sender:
            headers["From"] = sender

        request = urllib.request.Request(f"{self.url}/checkv2", data=raw,
                                         headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read())

        symbols = payload.get("symbols", {}) or {}
        return {
            "engine": "rspamd",
            "score": round(float(payload.get("score", 0.0)), 2),
            "required_score": float(payload.get("required_score", SPAM_AT)),
            "verdict": _rspamd_verdict(payload.get("action", "")),
            "action": payload.get("action"),
            "hits": sorted(
                ({"rule": name,
                  "weight": round(float(entry.get("score", 0)), 2),
                  "description": entry.get("description") or entry.get("options") or name}
                 for name, entry in symbols.items()),
                key=lambda h: -abs(h["weight"]),
            ),
            "summary": f"{payload.get('action', 'unknown')} at "
                       f"{payload.get('score', 0)}/{payload.get('required_score', SPAM_AT)}",
        }


def _rspamd_verdict(action: str) -> str:
    if action in {"reject"}:
        return "spam"
    if action in {"add header", "rewrite subject", "soft reject", "greylist"}:
        return "suspicious"
    return "ham"
