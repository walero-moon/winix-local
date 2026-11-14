"""Private installation bundle. Never commit generated files or rotate CA implicitly."""

import ipaddress
import json
import secrets
import string
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .installation import InstallationConfig
from .private_storage import restrict_private
from .wire import mac_id, validate_model

# Preserve the established validity window. A device still needs a correct clock;
# these dates do not bypass certificate verification or implement renewal.
CERTIFICATE_NOT_BEFORE = datetime(2020, 1, 1, tzinfo=timezone.utc)
CERTIFICATE_NOT_AFTER = datetime(2037, 12, 31, tzinfo=timezone.utc)
RSA_PUBLIC_EXPONENT = 65537
RSA_KEY_SIZE_BITS = 2048
REGISTRATION_TOKEN_LENGTH = 10


def private_write(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        restrict_private(path, directory=False)
        stream.write(data)


def create_bundle(
    directory: Union[str, Path],
    mac: str,
    server_host: str,
    mqtt_host: str,
    mqtt_port: int = 8883,
    http_port: int = 18080,
    https_port: int = 18443,
    *,
    model: str,
    issuer: Optional[tuple[x509.Certificate, rsa.RSAPrivateKey]] = None,
    retain_ca_key: bool = True,
) -> InstallationConfig:
    """Create once. Caller validates MAC/hosts and runs this in an executor."""
    model = validate_model(model)
    mac = mac_id(mac)

    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise ValueError(
            "Bundle directory must be empty; reuse existing bundle instead"
        )

    restrict_private(directory, directory=True)

    if issuer is None:
        ca_key = rsa.generate_private_key(
            public_exponent=RSA_PUBLIC_EXPONENT, key_size=RSA_KEY_SIZE_BITS
        )
        ca_name = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "Winix Local installation CA")]
        )
        ca = (
            x509.CertificateBuilder()
            .subject_name(ca_name)
            .issuer_name(ca_name)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(CERTIFICATE_NOT_BEFORE)
            .not_valid_after(CERTIFICATE_NOT_AFTER)
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
                critical=False,
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                critical=False,
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=False,
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
            .sign(ca_key, hashes.SHA256())
        )
    else:
        ca, ca_key = issuer
        ca_name = ca.subject

    def serialize_private_key(key):
        return key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    if retain_ca_key:
        private_write(directory / "ca.key", serialize_private_key(ca_key))
    private_write(directory / "ca.pem", ca.public_bytes(serialization.Encoding.PEM))

    for name, common_name, hosts, extended_usage in (
        ("auth", server_host, [server_host], ExtendedKeyUsageOID.SERVER_AUTH),
        ("broker", mqtt_host, [mqtt_host], ExtendedKeyUsageOID.SERVER_AUTH),
        ("device", mac, [], ExtendedKeyUsageOID.CLIENT_AUTH),
    ):
        key = rsa.generate_private_key(
            public_exponent=RSA_PUBLIC_EXPONENT, key_size=RSA_KEY_SIZE_BITS
        )
        certificate_builder = (
            x509.CertificateBuilder()
            .subject_name(
                x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
            )
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(max(CERTIFICATE_NOT_BEFORE, ca.not_valid_before_utc))
            .not_valid_after(min(CERTIFICATE_NOT_AFTER, ca.not_valid_after_utc))
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None), critical=True
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                critical=False,
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                critical=False,
            )
            .add_extension(x509.ExtendedKeyUsage([extended_usage]), critical=False)
        )

        if hosts:
            alternative_names: list[x509.GeneralName] = []
            for host in hosts:
                try:
                    alternative_names.append(x509.IPAddress(ipaddress.ip_address(host)))
                except ValueError:
                    alternative_names.append(x509.DNSName(host))
            certificate_builder = certificate_builder.add_extension(
                x509.SubjectAlternativeName(alternative_names), critical=False
            )

        certificate = certificate_builder.sign(ca_key, hashes.SHA256())
        private_write(directory / (name + ".key"), serialize_private_key(key))
        private_write(
            directory / (name + ".pem"),
            certificate.public_bytes(serialization.Encoding.PEM),
        )

    return write_configuration(
        directory,
        mac,
        model,
        server_host,
        mqtt_host,
        mqtt_port,
        http_port,
        https_port,
        ca,
    )


def write_configuration(
    directory: Path,
    mac: str,
    model: str,
    server_host: str,
    mqtt_host: str,
    mqtt_port: int,
    http_port: int,
    https_port: int,
    ca: x509.Certificate,
) -> InstallationConfig:
    """Write matching private registration settings and public provisioning data."""
    token = "".join(
        secrets.choice(string.ascii_letters + string.digits)
        for _ in range(REGISTRATION_TOKEN_LENGTH)
    )
    config: InstallationConfig = dict(
        mac=mac,
        token=token,
        server_host=server_host,
        mqtt_host=mqtt_host,
        mqtt_port=mqtt_port,
        http_port=http_port,
        https_port=https_port,
        model=model,
    )
    private_write(
        directory / "bundle.json", (json.dumps(config, indent=2) + "\n").encode()
    )

    # Export is deliberately public: no token, Wi-Fi credentials or private keys.
    public = {k: v for k, v in config.items() if k != "token"}
    public["ca_sha256"] = ca.fingerprint(hashes.SHA256()).hex()
    private_write(
        directory / "provision.json", (json.dumps(public, indent=2) + "\n").encode()
    )

    return config
