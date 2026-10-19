"""Local certificate authority for HTTPS on the LAN.

Phones only allow live camera access (getUserMedia) on secure origins. ``localhost`` is exempt but a
LAN address like ``https://192.168.1.20:8443`` is not, so we create a private CA once, the user
trusts it on the phone once, and the server certificate is re-issued automatically whenever the
Mac's LAN address changes.

Apple requirements honoured: SAN present (CN is ignored), EKU serverAuth, SHA-256, server
certificate validity <= 397 days, CA with basicConstraints CA:TRUE.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from gobot.netinfo import local_hostname

SERVER_DAYS = 397
CA_DAYS = 3650


def _write_private(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    os.chmod(path, 0o600)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _make_ca(certs_dir: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, f"gobot local CA ({local_hostname()})"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "gobot (local only)"),
        ]
    )
    ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_now() - dt.timedelta(days=1))
        .not_valid_after(_now() + dt.timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(ski, critical=False)
        .sign(key, hashes.SHA256())
    )
    certs_dir.mkdir(parents=True, exist_ok=True)
    _write_private(
        certs_dir / "ca.key",
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ),
    )
    (certs_dir / "ca.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (certs_dir / "ca.der").write_bytes(cert.public_bytes(serialization.Encoding.DER))


def _san_names(ip: str | None) -> tuple[list[str], list[str]]:
    dns = sorted({local_hostname(), "localhost"})
    ips = sorted({"127.0.0.1", *([ip] if ip else [])})
    return dns, ips


def _make_server(certs_dir: Path, ip: str | None) -> None:
    ca_key = serialization.load_pem_private_key((certs_dir / "ca.key").read_bytes(), password=None)
    ca_cert = x509.load_pem_x509_certificate((certs_dir / "ca.pem").read_bytes())
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    dns, ips = _san_names(ip)
    san = x509.SubjectAlternativeName(
        [x509.DNSName(d) for d in dns] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ip or local_hostname())]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_now() - dt.timedelta(days=1))
        .not_valid_after(_now() + dt.timedelta(days=SERVER_DAYS))
        .add_extension(san, critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key()),  # type: ignore[arg-type]
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())  # type: ignore[arg-type]
    )
    _write_private(
        certs_dir / "server.key",
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ),
    )
    (certs_dir / "server.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (certs_dir / "server.json").write_text(json.dumps({"dns": dns, "ips": ips}) + "\n")


def ensure_certs(certs_dir: Path, ip: str | None, force_ca: bool = False) -> dict[str, Path]:
    certs_dir.mkdir(parents=True, exist_ok=True)
    if force_ca or not (certs_dir / "ca.key").is_file() or not (certs_dir / "ca.pem").is_file():
        _make_ca(certs_dir)
        for f in ("server.pem", "server.key", "server.json"):
            (certs_dir / f).unlink(missing_ok=True)
    need = True
    meta = certs_dir / "server.json"
    if (certs_dir / "server.pem").is_file() and meta.is_file():
        dns, ips = _san_names(ip)
        try:
            saved = json.loads(meta.read_text())
            cert = x509.load_pem_x509_certificate((certs_dir / "server.pem").read_bytes())
            fresh = cert.not_valid_after_utc - _now() > dt.timedelta(days=30)
            need = not (saved == {"dns": dns, "ips": ips} and fresh)
        except (ValueError, OSError):
            need = True
    if need:
        _make_server(certs_dir, ip)
    return {
        "ca_pem": certs_dir / "ca.pem",
        "ca_der": certs_dir / "ca.der",
        "cert": certs_dir / "server.pem",
        "key": certs_dir / "server.key",
    }
