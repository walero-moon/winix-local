"""Broker-neutral certificate enrollment and atomic installation creation.

The supported import profile is one RSA CA directly signing RSA leaf certificates.
No keys are stored in HA config entries or exported in public provisioning data.
"""

from datetime import datetime, timezone
import io
import ipaddress
import json
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping, Optional
import zipfile

from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .installation import InstallationConfig, load_bundle
from .pki import create_bundle, private_write, write_configuration
from .private_storage import restrict_private
from .wire import device_id, mac_id, topic, validate_model

MAX_PEM_BYTES = 32768
CERTIFICATE_FILES = (
    "ca.pem",
    "auth.pem",
    "auth.key",
    "broker.pem",
    "broker.key",
    "device.pem",
    "device.key",
)


def validate_settings(values: Mapping) -> dict:
    """Validate only explicit device endpoints; HA's MQTT integration owns its login."""
    settings: dict[str, Any] = {
        "mac": mac_id(values["mac"]),
        "model": validate_model(values["model"]),
    }
    for field in ("server_host", "mqtt_host"):
        host = values[field]
        if (
            not isinstance(host, str)
            or len(host) > 253
            or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host)
        ):
            raise ValueError(
                "Use a LAN IPv4 address or DNS hostname without a scheme or port"
            )
        settings[field] = host

    for field in ("http_port", "https_port", "mqtt_port"):
        port = values[field]
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("Use listener ports 1024..65535")
        settings[field] = port
    if settings["http_port"] == settings["https_port"]:
        raise ValueError("Registration listener ports must differ")
    return settings


def _pem(value: str) -> bytes:
    if not isinstance(value, str) or len(value) > MAX_PEM_BYTES:
        raise ValueError("Invalid certificate material")
    return value.encode("ascii")


def _certificate(value: str) -> x509.Certificate:
    data = _pem(value)
    if (
        data.count(b"-----BEGIN CERTIFICATE-----") != 1
        or data.count(b"-----BEGIN ") != 1
        or data.count(b"-----END ") != 1
    ):
        raise ValueError("Provide a single certificate, not a certificate chain")
    certificate = x509.load_pem_x509_certificate(data)
    now = datetime.now(timezone.utc)
    if not certificate.not_valid_before_utc <= now < certificate.not_valid_after_utc:
        raise ValueError("Certificate is outside its validity period")
    public_key = certificate.public_key()
    if not isinstance(public_key, rsa.RSAPublicKey) or public_key.key_size < 2048:
        raise ValueError(
            "The tested certificate profile requires RSA keys of at least 2048 bits"
        )
    return certificate


def _key(value: str) -> rsa.RSAPrivateKey:
    data = _pem(value)
    if data.count(b"-----BEGIN ") != 1 or data.count(b"-----END ") != 1:
        raise ValueError("Provide exactly one private key")
    key = serialization.load_pem_private_key(data, password=None)
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise ValueError("RSA private key required")
    return key


def _matching_key(certificate: x509.Certificate, key: rsa.RSAPrivateKey) -> None:
    if certificate.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ) != key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ):
        raise ValueError("Certificate and private key do not match")


def validate_ca(ca_pem: str, key_pem: Optional[str] = None):
    """Validate the import profile and, when supplied, the CA signing key."""
    ca = _certificate(ca_pem)
    if not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
        raise ValueError("CA certificate required")
    try:
        if not ca.extensions.get_extension_for_class(x509.KeyUsage).value.key_cert_sign:
            raise ValueError("CA does not permit certificate signing")
    except x509.ExtensionNotFound:
        pass

    key = _key(key_pem) if key_pem else None
    if key is not None:
        _matching_key(ca, key)
    return ca, key


def _verify_host(certificate: x509.Certificate, host: str) -> None:
    names = certificate.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if host.lower() not in [
            name.lower() for name in names.get_values_for_type(x509.DNSName)
        ]:
            raise ValueError(
                "Server certificate must contain the exact configured DNS name"
            )
    else:
        if address not in names.get_values_for_type(x509.IPAddress):
            raise ValueError(
                "Server certificate must contain the configured IP address"
            )


def validate_material(
    settings: Mapping, material: Mapping[str, str], mode: str
) -> None:
    """Reject wrong issuers, identities, purposes and keys before writing any files."""
    try:
        ca, _ = validate_ca(material["ca.pem"], material.get("ca.key"))
        if mode == "issuer":
            if not material.get("ca.key"):
                raise ValueError("CA signing key required")
            return
        if mode != "certificates":
            raise ValueError("Unknown certificate mode")

        for name in ("auth", "broker", "device"):
            certificate = _certificate(material[name + ".pem"])
            _matching_key(certificate, _key(material[name + ".key"]))
            certificate.verify_directly_issued_by(ca)
            if certificate.extensions.get_extension_for_class(
                x509.BasicConstraints
            ).value.ca:
                raise ValueError("Leaf certificate cannot be a CA")
            purpose = (
                ExtendedKeyUsageOID.CLIENT_AUTH
                if name == "device"
                else ExtendedKeyUsageOID.SERVER_AUTH
            )
            if (
                purpose
                not in certificate.extensions.get_extension_for_class(
                    x509.ExtendedKeyUsage
                ).value
            ):
                raise ValueError("Wrong certificate purpose")
            if name == "device":
                names = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
                if len(names) != 1 or names[0].value != settings["mac"]:
                    raise ValueError("Device certificate CN must be the normalized MAC")
            else:
                _verify_host(
                    certificate,
                    (
                        settings["server_host"]
                        if name == "auth"
                        else settings["mqtt_host"]
                    ),
                )
    except (
        ValueError,
        TypeError,
        KeyError,
        UnicodeError,
        x509.ExtensionNotFound,
        InvalidSignature,
        UnsupportedAlgorithm,
    ) as error:
        # Never echo supplied PEM or underlying parser exceptions into HA logs/UI.
        raise ValueError(
            "Certificate material does not match this installation"
        ) from error


def create_installation(
    directory: Path,
    settings: Mapping,
    mode: str,
    material: Mapping[str, str],
    retain_ca_key: bool,
) -> InstallationConfig:
    """Publish a complete new bundle or leave no bundle; never overwrite one."""
    settings = validate_settings(settings)
    if mode not in ("generate", "issuer", "certificates"):
        raise ValueError("Unknown certificate mode")
    if mode != "generate":
        validate_material(settings, material, mode)
    if retain_ca_key and mode == "certificates" and not material.get("ca.key"):
        raise ValueError("There is no signing key to retain")

    directory = Path(directory)
    if directory.exists() or directory.is_symlink():
        raise FileExistsError("Existing installation will not be replaced")
    directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".winix-enrollment-", dir=directory.parent
    ) as temporary:
        staging = Path(temporary) / "bundle"
        if mode == "certificates":
            staging.mkdir(mode=0o700)
            restrict_private(staging, directory=True)
            for name in CERTIFICATE_FILES:
                private_write(staging / name, _pem(material[name]))
            if retain_ca_key:
                private_write(staging / "ca.key", _pem(material["ca.key"]))
            ca = _certificate(material["ca.pem"])
            config = write_configuration(staging, ca=ca, **settings)
        else:
            issuer = (
                validate_ca(material["ca.pem"], material["ca.key"])
                if mode == "issuer"
                else None
            )
            config = create_bundle(
                staging, issuer=issuer, retain_ca_key=retain_ca_key, **settings
            )

        staging.rename(directory)
    return config


def broker_requirements(config: InstallationConfig) -> dict:
    """Express authentication and exact topic grants without a broker dialect."""
    identity = device_id(config["mac"], config["token"])
    return {
        "host": config["mqtt_host"],
        "port": config["mqtt_port"],
        "tls_minimum": "1.2",
        "require_client_certificate": True,
        "client_certificate_common_name": config["mac"],
        "device": {
            "subscribe": [topic("Control", identity, config["model"])],
            "publish": [
                topic(kind, identity, config["model"]) for kind in ("Conn", "Event")
            ],
        },
        "home_assistant": {
            "publish": [topic("Control", identity, config["model"])],
            "subscribe": [
                topic(kind, identity, config["model"]) for kind in ("Conn", "Event")
            ],
        },
    }


def export_artifact(directory: Path, kind: str) -> tuple[bytes, str, str]:
    """Export only the allowlisted artifact; never put a CA signing key in a ZIP."""
    config = load_bundle(directory)
    if kind == "provision":
        return (
            (directory / "provision.json").read_bytes(),
            "application/json",
            "provision.json",
        )
    if kind != "broker":
        raise ValueError("Unknown export")

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name in ("ca.pem", "broker.pem", "broker.key"):
            archive.writestr(name, (directory / name).read_bytes())
        archive.writestr(
            "requirements.json", json.dumps(broker_requirements(config), indent=2)
        )
        archive.writestr(
            "README.txt",
            "Install these files using your MQTT broker's TLS/client-certificate settings.\nTrust ca.pem for client authentication; use broker.pem and broker.key as the server identity.\nMap the verified client certificate CN to the device identity and apply requirements.json topic grants.\nHA's existing MQTT integration must connect to this same broker with its own credentials and grants.\nThis ZIP contains a private server key. Keep it private. It contains no CA signing key.\n",
        )
    return stream.getvalue(), "application/zip", "winix-broker.zip"
