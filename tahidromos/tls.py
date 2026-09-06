"""A self-signed certificate, generated on first run.

It exists so you can exercise the SMTPS (465) and IMAPS (993) code paths in
your client. Nothing verifies it — clients must skip verification — which is
exactly right for development and exactly wrong for anything else.
"""

from __future__ import annotations

import datetime
import logging
import ssl
from pathlib import Path

log = logging.getLogger("tahidromos.tls")


def ensure_certificate(directory: Path, hostname: str) -> tuple[Path, Path] | None:
    certificate = directory / "tahidromos.crt"
    key = directory / "tahidromos.key"
    if certificate.is_file() and key.is_file():
        return certificate, key

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        log.warning("cryptography is not installed; TLS ports will be disabled")
        return None

    directory.mkdir(parents=True, exist_ok=True)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, hostname),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "tahidromos development"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName(hostname),
                x509.DNSName("localhost"),
                x509.DNSName("mailserver"),
                x509.DNSName("tahidromos"),
            ]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    )
    certificate_object = builder.sign(private_key, hashes.SHA256())

    key.write_bytes(private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    certificate.write_bytes(certificate_object.public_bytes(serialization.Encoding.PEM))
    log.info("generated a self-signed certificate for %s", hostname)
    return certificate, key


def build_context(directory: Path, hostname: str) -> ssl.SSLContext | None:
    pair = ensure_certificate(directory, hostname)
    if pair is None:
        return None
    certificate, key = pair
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(certificate), keyfile=str(key))
    context.check_hostname = False
    return context
