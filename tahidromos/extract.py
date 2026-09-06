"""Pulling the useful parts out of a message.

Three jobs that every email test ends up doing by hand:

  * strip the quoted reply and signature, so an assertion can look at what
    the sender actually wrote this time
  * find the links, so an end-to-end test can follow a magic link
  * find the one-time code, so a login test can type it in

The quote stripping is deliberately conservative. It is better to leave a
line in than to eat the reply, so every rule here matches a well-known
client convention rather than guessing.
"""

from __future__ import annotations

import re
from html import unescape

# ---------------------------------------------------------------- quotes

# "On Mon, 6 Sep 2026 at 20:38, alice@x.test wrote:" and its many dialects.
ATTRIBUTION = re.compile(
    r"""^\s*(
        On\s.{0,180}?\bwrote:                 |   # English
        Am\s.{0,180}?\bschrieb\s.{0,80}?:     |   # German
        Le\s.{0,180}?\ba\s+écrit\s*:          |   # French
        El\s.{0,180}?\bescribió\s*:           |   # Spanish
        Στις\s.{0,180}?\bέγραψε\s*:           |   # Greek
        .{0,80}?\bwrote:                          # bare "X wrote:"
    )\s*$""",
    re.IGNORECASE | re.VERBOSE,
)

# Outlook and friends put a header block above the quoted text.
FORWARD_HEADER = re.compile(
    r"^\s*(-{2,}\s*(Original Message|Forwarded message|Ursprüngliche Nachricht)\s*-{2,}"
    r"|_{5,}"
    r"|\*?From:\*?\s.+)\s*$",
    re.IGNORECASE,
)

# RFC 3676 §4.3 signature separator, plus the common unmarked variants.
SIGNATURE = re.compile(
    r"^\s*(--\s*$|--$|__+$|—\s*$|Sent from my \w+|Get Outlook for \w+|"
    r"Sent via \w+)\s*$",
    re.IGNORECASE,
)

QUOTE_MARKER = re.compile(r"^\s*>")


def strip_quotes(text: str, keep_signature: bool = False) -> str:
    """Return only what this author wrote, without the thread beneath it."""
    if not text:
        return ""

    lines = text.replace("\r\n", "\n").split("\n")
    kept: list[str] = []

    for index, line in enumerate(lines):
        if ATTRIBUTION.match(line) or FORWARD_HEADER.match(line):
            break
        if QUOTE_MARKER.match(line):
            # A quote block ends the new text, unless it is the very first
            # thing (a top-quoting client that put the reply underneath).
            if kept and any(l.strip() for l in kept):
                break
            continue
        if not keep_signature and SIGNATURE.match(line):
            # Only treat it as a signature if something follows it; a
            # trailing "--" on its own is just punctuation.
            if index < len(lines) - 1:
                break
        kept.append(line)

    return "\n".join(kept).strip()


# ---------------------------------------------------------------- links

URL = re.compile(r"""https?://[^\s<>"')\]]+""")
HREF = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)

# Trailing punctuation that is almost always sentence punctuation, not URL.
TRAILING = ".,;:!?"


def _clean(url: str) -> str:
    url = unescape(url).strip()
    while url and url[-1] in TRAILING:
        url = url[:-1]
    # a closing paren only belongs to the URL if it was opened inside it
    while url.endswith(")") and url.count("(") < url.count(")"):
        url = url[:-1]
    return url


def find_links(text: str = "", html: str = "") -> list[str]:
    """Every distinct http(s) link, HTML hrefs first, in document order."""
    found: list[str] = []
    for candidate in HREF.findall(html or ""):
        cleaned = _clean(candidate)
        if cleaned.startswith(("http://", "https://")) and cleaned not in found:
            found.append(cleaned)
    for candidate in URL.findall(text or "") + URL.findall(
            re.sub(r"<[^>]+>", " ", html or "")):
        cleaned = _clean(candidate)
        if cleaned and cleaned not in found:
            found.append(cleaned)
    return found


def find_link(text: str = "", html: str = "", contains: str | None = None) -> str | None:
    """The first link, optionally the first one containing a substring."""
    for link in find_links(text, html):
        if contains is None or contains.lower() in link.lower():
            return link
    return None


# ---------------------------------------------------------------- codes

CODE_LABEL = re.compile(
    r"\b(?:code|otp|pin|passcode|token|verification|verify|confirm(?:ation)?|"
    r"2fa|mfa|one[-\s]?time(?:\s+(?:password|code|pin))?)\b",
    re.IGNORECASE,
)
DIGIT_CODE = re.compile(r"(?<![0-9A-Za-z])([0-9]{4,8})(?![0-9A-Za-z])")
ALNUM_CODE = re.compile(r"(?<![0-9A-Za-z])([A-Z0-9]{6,8})(?![0-9A-Za-z])")

# How far past the label to look. Long enough for "your code is:\n\n  483920",
# short enough not to wander into the next paragraph.
CODE_WINDOW = 60


def _without_urls(text: str) -> str:
    """A one-time code is never inside a link, and `?token=abc123` looks like one."""
    return URL.sub(" ", text or "")


def find_code(text: str = "", html: str = "", length: int | None = None) -> str | None:
    """The one-time code, if the message looks like it contains one.

    A labelled code wins over a bare number, and digits win over letters,
    because "your code is 483920" is far more common than an order number
    that happens to sit near the word "confirmation".
    """
    haystack = _without_urls(f"{text}\n{re.sub(r'<[^>]+>', ' ', html or '')}")

    def sized(candidate: str) -> bool:
        return length is None or len(candidate) == length

    # 1. a code-shaped token shortly after a label like "code" or "OTP"
    for label in CODE_LABEL.finditer(haystack):
        window = haystack[label.end():label.end() + CODE_WINDOW]
        for pattern in (DIGIT_CODE, ALNUM_CODE):
            for match in pattern.finditer(window):
                if sized(match.group(1)):
                    return match.group(1)

    # 2. nothing labelled: fall back to a bare run of digits, but only when
    #    the caller told us how long it should be
    if length is not None:
        for match in DIGIT_CODE.finditer(haystack):
            if sized(match.group(1)):
                return match.group(1)
    return None


def summarize(text: str = "", html: str = "") -> dict:
    """Everything an end-to-end test usually wants, computed once."""
    links = find_links(text, html)
    return {
        "stripped_text": strip_quotes(text),
        "links": links,
        "link": links[0] if links else None,
        "code": find_code(text, html),
    }
