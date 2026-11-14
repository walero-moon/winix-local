"""Check the selected local endpoints before changing a purifier's settings."""

import hashlib
import http.client
import socket
import ssl

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from .installation import ProvisioningConfig

ENDPOINT_TIMEOUT_SECONDS = 5
MAX_CA_BYTES = 65536


class EndpointCheckError(ValueError):
    """A configured local endpoint is not ready for provisioning."""


def verify_endpoints(config: ProvisioningConfig) -> None:
    """Verify the pinned CA, registration TLS and broker TCP reachability.

    Broker reachability is a TCP check, not proof of device MQTT authentication.
    No registration credentials are requested and no cloud services are called.
    """
    fingerprint = config.get("ca_sha256")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise EndpointCheckError("Use provision.json with its generated CA fingerprint")

    connection = http.client.HTTPConnection(
        config["server_host"], config["http_port"], timeout=ENDPOINT_TIMEOUT_SECONDS
    )
    try:
        connection.request("GET", "/ca.pem")
        response = connection.getresponse()
        if response.status != 200:
            raise EndpointCheckError("The registration service did not serve /ca.pem")

        ca_pem = response.read(MAX_CA_BYTES + 1)
        if len(ca_pem) > MAX_CA_BYTES:
            raise EndpointCheckError("The registration CA response is too large")
    except (OSError, http.client.HTTPException) as error:
        raise EndpointCheckError(
            "The local registration HTTP listener is unreachable"
        ) from error
    finally:
        connection.close()

    try:
        certificate = x509.load_pem_x509_certificate(ca_pem)
        ca_der = certificate.public_bytes(serialization.Encoding.DER)
        if hashlib.sha256(ca_der).hexdigest() != fingerprint.lower():
            raise EndpointCheckError("The served CA does not match this installation")

        context = ssl.create_default_context(cadata=ca_pem.decode("ascii"))
        try:
            certificate.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)
        except x509.ExtensionNotFound:
            # Older installation CAs predate our key-identifier extensions. The
            # exact CA pin was checked above; retain chain/hostname/date checks
            # while allowing that legacy profile on Python 3.13+.
            context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        with socket.create_connection(
            (config["server_host"], config["https_port"]),
            timeout=ENDPOINT_TIMEOUT_SECONDS,
        ) as transport:
            with context.wrap_socket(transport, server_hostname=config["server_host"]):
                pass
    except EndpointCheckError:
        raise
    except (OSError, ValueError, UnicodeError) as error:
        raise EndpointCheckError(
            "Local registration TLS verification failed"
        ) from error

    try:
        with socket.create_connection(
            (config["mqtt_host"], config["mqtt_port"]), timeout=ENDPOINT_TIMEOUT_SECONDS
        ):
            pass
    except OSError as error:
        raise EndpointCheckError(
            "The configured MQTT listener is unreachable"
        ) from error
