"""Email templates you can send with one call.

Real applications send welcome mail, sign-in codes, receipts and digests.
Testing how your app handles them means having examples that look like the
real thing — table layouts, inline styles, a plain-text alternative, a
preheader — rather than a one-line body.

The renderer is deliberately tiny: `{{ name }}` substitution and a single
repeating block, `{{#each items}} … {{/each}}`. That is enough for a receipt
with line items and a digest with articles, and it means no template engine
dependency.

Templates live in tahidromos/templates/ as .html (and optional .txt) files,
described by manifest.json. Some are authored in React Email and exported
there; see templates/react-email/.
"""

from __future__ import annotations

import html as html_module
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TEMPLATE_DIR = Path(__file__).parent / "templates"

EACH_BLOCK = re.compile(r"\{\{#each\s+([a-zA-Z0-9_]+)\s*\}\}(.*?)\{\{/each\}\}", re.DOTALL)
VARIABLE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")
RAW_VARIABLE = re.compile(r"\{\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}\}")


def _lookup(context: dict, path: str) -> Any:
    value: Any = context
    for part in path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return ""
    return value


def render_string(template: str, context: dict, escape: bool = True) -> str:
    """Substitute variables and expand each-blocks."""

    def expand_each(match: re.Match) -> str:
        items = _lookup(context, match.group(1))
        body = match.group(2)
        if not isinstance(items, list):
            return ""
        pieces = []
        for item in items:
            scope = {**context, **(item if isinstance(item, dict) else {"item": item})}
            pieces.append(render_string(body, scope, escape))
        return "".join(pieces)

    output = EACH_BLOCK.sub(expand_each, template)

    # {{{ raw }}} first, so an escaped pass cannot double-encode it
    output = RAW_VARIABLE.sub(lambda m: str(_lookup(context, m.group(1))), output)
    output = VARIABLE.sub(
        lambda m: html_module.escape(str(_lookup(context, m.group(1))), quote=True)
        if escape else str(_lookup(context, m.group(1))),
        output,
    )
    return output


def _expand_defaults(values: dict) -> dict:
    """Let a default refer to another value, e.g. `alice@{{ domain }}`.

    One pass is enough: defaults reference the domain and product, never each
    other in a chain.
    """
    expanded = dict(values)
    for key, value in values.items():
        if isinstance(value, str) and "{{" in value:
            expanded[key] = render_string(value, values, escape=False)
    return expanded


@dataclass
class Template:
    name: str
    subject: str
    sender: str
    summary: str
    defaults: dict
    html_file: str
    text_file: str | None = None
    source: str = "handwritten"

    def render(self, context: dict | None = None, domain: str = "tahidromos.test") -> dict:
        merged = _expand_defaults({"domain": domain, **self.defaults, **(context or {})})
        html_path = TEMPLATE_DIR / self.html_file
        html = render_string(html_path.read_text(encoding="utf-8"), merged, escape=True)

        text = ""
        if self.text_file and (TEMPLATE_DIR / self.text_file).is_file():
            text = render_string((TEMPLATE_DIR / self.text_file).read_text(encoding="utf-8"),
                                 merged, escape=False)
        return {
            "subject": render_string(self.subject, merged, escape=False),
            "from": render_string(self.sender, merged, escape=False),
            "html": html,
            "text": text or _html_to_text(html),
            "context": merged,
        }


def _html_to_text(html: str) -> str:
    """A readable plain-text fallback when a template ships no .txt."""
    text = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|h[1-6]|li)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_module.unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


_CACHE: dict[str, Template] | None = None


def registry(reload: bool = False) -> dict[str, Template]:
    global _CACHE
    if _CACHE is not None and not reload:
        return _CACHE
    manifest_path = TEMPLATE_DIR / "manifest.json"
    if not manifest_path.is_file():
        _CACHE = {}
        return _CACHE
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _CACHE = {
        entry["name"]: Template(
            name=entry["name"],
            subject=entry["subject"],
            sender=entry.get("from", "noreply@{{ domain }}"),
            summary=entry.get("summary", ""),
            defaults=entry.get("defaults", {}),
            html_file=entry["html"],
            text_file=entry.get("text"),
            source=entry.get("source", "handwritten"),
        )
        for entry in manifest.get("templates", [])
    }
    return _CACHE


def catalogue() -> list[dict]:
    return [
        {"name": t.name, "summary": t.summary, "subject": t.subject,
         "from": t.sender, "source": t.source, "variables": sorted(t.defaults)}
        for t in registry().values()
    ]


def render(name: str, context: dict | None = None,
           domain: str = "tahidromos.test") -> dict:
    template = registry().get(name)
    if template is None:
        raise KeyError(name)
    return template.render(context, domain)
