"""Configuration: one instance, many apps.

Each app in tahidromos.d/*.yml gets its own domain, its own SMTP credentials
and its own mailboxes, so a single tahidromos replaces the mail sink you would
otherwise run per docker-compose file.

With no config file present everything falls back to environment variables,
which is what the zero-setup quick start uses.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

CAPTURE_MAILBOX = "captured"
CONFIG_SUFFIXES = (".yml", ".yaml")


def env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def split_list(value: str) -> list[str]:
    return [part for part in re.split(r"[,;\s]+", value or "") if part]


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", str(value).strip().lower()).strip("-") or "app"


@dataclass
class Mailbox:
    localpart: str
    address: str
    password: str
    is_bot: bool = False
    is_capture: bool = False
    description: str = ""


@dataclass
class App:
    name: str
    domain: str
    description: str = ""
    smtp_username: str = ""
    smtp_password: str = ""
    mailboxes: list[Mailbox] = field(default_factory=list)

    @property
    def smtp_address(self) -> str:
        return f"{self.smtp_username}@{self.domain}" if self.smtp_username else ""


@dataclass
class Config:
    apps: list[App]
    domains: list[str]
    primary_domain: str
    hostname: str
    default_password: str
    capture_address: str
    sources: list[str]

    # -- lookups ---------------------------------------------------------

    def is_local(self, address: str) -> bool:
        domain = address.rsplit("@", 1)[-1].lower()
        return domain in {d.lower() for d in self.domains}

    def qualify(self, user: str) -> str:
        user = user.strip()
        return user if "@" in user else f"{user}@{self.primary_domain}"

    def password_for(self, address: str) -> str | None:
        address = address.lower()
        for app in self.apps:
            if app.smtp_address.lower() == address:
                return app.smtp_password
            for mailbox in app.mailboxes:
                if mailbox.address.lower() == address:
                    return mailbox.password
        return None

    def all_mailboxes(self) -> list[tuple[App, Mailbox]]:
        return [(app, box) for app in self.apps for box in app.mailboxes]

    def bots(self) -> list[Mailbox]:
        return [box for _, box in self.all_mailboxes() if box.is_bot]

    def as_dict(self) -> dict:
        return {
            "apps": [
                {
                    "name": app.name,
                    "domain": app.domain,
                    "description": app.description,
                    "smtp_address": app.smtp_address,
                    "smtp_password": app.smtp_password,
                    "mailboxes": [vars(box) for box in app.mailboxes],
                }
                for app in self.apps
            ],
            "domains": self.domains,
            "primary_domain": self.primary_domain,
            "hostname": self.hostname,
            "default_password": self.default_password,
            "capture_address": self.capture_address,
            "sources": self.sources,
        }


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------


def _config_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir()
                  if p.is_file() and p.suffix.lower() in CONFIG_SUFFIXES)


def _mailboxes(raw, domain: str, password: str, bot_names: set[str]) -> list[Mailbox]:
    """Accept a list of names, a list of mappings, or a mapping of name -> options."""
    if raw is None:
        entries: list[tuple[str, dict]] = []
    elif isinstance(raw, dict):
        entries = [(name, options or {}) for name, options in raw.items()]
    elif isinstance(raw, list):
        entries = []
        for item in raw:
            if isinstance(item, dict):
                entries.append((item.get("name") or item.get("localpart"), item))
            else:
                entries.append((str(item), {}))
    else:
        raise ValueError(f"mailboxes must be a list or a mapping, got {type(raw).__name__}")

    boxes = []
    for name, options in entries:
        if not name:
            continue
        localpart = str(name).split("@", 1)[0].strip()
        if not localpart:
            continue
        boxes.append(Mailbox(
            localpart=localpart,
            address=f"{localpart}@{domain}",
            password=str(options.get("password") or password),
            is_bot=bool(options.get("bot", localpart in bot_names)),
            description=str(options.get("description", "")),
        ))
    return boxes


def load(config_dir: str | None = None) -> Config:
    directory = Path(config_dir or env("TAHIDROMOS_CONFIG_DIR", "/etc/tahidromos/conf.d"))
    default_password = env("MAIL_DEFAULT_PASSWORD", "password")
    primary_domain = env("MAIL_DOMAIN", "tahidromos.test").lower()
    hostname = env("MAIL_HOSTNAME", f"mail.{primary_domain}")
    bot_names = set(split_list(env("BOT_ACCOUNTS", "echo,echo2")))

    merged: dict[str, dict] = {}
    sources: list[str] = []
    for path in _config_files(directory):
        import yaml

        with path.open() as handle:
            data = yaml.safe_load(handle) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path}: expected a mapping at the top level")
        sources.append(str(path))
        default_password = str(data.get("default_password", default_password))

        raw_apps = data.get("apps") or {}
        if isinstance(raw_apps, dict):
            entries = list(raw_apps.items())
        else:
            entries = [(item.get("name"), item) for item in raw_apps if isinstance(item, dict)]
        for name, spec in entries:
            if name:
                merged[slug(name)] = spec or {}

    apps: list[App] = []
    for name, spec in merged.items():
        domain = str(spec.get("domain") or f"{name}.test").strip().lower()
        smtp = spec.get("smtp") or {}
        app_password = str(spec.get("password") or default_password)
        app = App(
            name=name,
            domain=domain,
            description=str(spec.get("description", "")),
            smtp_username=str(smtp.get("username") or name),
            smtp_password=str(smtp.get("password") or app_password),
        )
        app.mailboxes = _mailboxes(spec.get("mailboxes"), domain, app_password, bot_names)
        apps.append(app)

    if not apps:
        # zero-config mode: one app assembled from the environment
        app = App(name="default", domain=primary_domain,
                  description="Configured from environment variables",
                  smtp_username="app", smtp_password=default_password)
        for entry in split_list(env("MAIL_ACCOUNTS",
                                    "alice,bob,carol,dave,support,sales,noreply,"
                                    "postmaster,echo,echo2")):
            localpart, _, password = entry.partition(":")
            localpart = localpart.split("@", 1)[0]
            app.mailboxes.append(Mailbox(
                localpart=localpart,
                address=f"{localpart}@{primary_domain}",
                password=password or default_password,
                is_bot=localpart in bot_names,
            ))
        apps.append(app)

    # The capture mailbox must exist: the router rewrites anything addressed
    # outside the local domains to it rather than letting it reach the internet.
    capture_app = next((a for a in apps if a.domain == primary_domain), apps[0])
    capture = next((b for b in capture_app.mailboxes if b.localpart == CAPTURE_MAILBOX), None)
    if capture is None:
        capture = Mailbox(
            localpart=CAPTURE_MAILBOX,
            address=f"{CAPTURE_MAILBOX}@{capture_app.domain}",
            password=default_password,
            is_capture=True,
            description="Mail addressed outside the local domains — never sent onward",
        )
        capture_app.mailboxes.append(capture)
    else:
        capture.is_capture = True

    domains: list[str] = []
    for domain in [primary_domain] + [a.domain for a in apps] + split_list(env("MAIL_LOCAL_DOMAINS")):
        if domain and domain not in domains:
            domains.append(domain)

    return Config(apps=apps, domains=domains, primary_domain=primary_domain,
                  hostname=hostname, default_password=default_password,
                  capture_address=capture.address, sources=sources)
