"""Public-only urllib connections without process-global DNS overrides.

Each connection resolves once, validates the complete answer, then connects to
one of those numeric socket addresses. Redirects create new connections and
therefore receive the same check. HTTP Host and HTTPS SNI/certificate handling
remain with the standard library, using the original hostname.
"""

import ipaddress
import socket
import sys
from http.client import HTTPConnection, HTTPSConnection
from urllib.request import HTTPHandler, HTTPSHandler

from app.exceptions import BadRequestError
from app.services.url_safety import _is_disallowed_ip


def _public_addresses(host: str, port: int) -> list:
    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise BadRequestError(message="Could not resolve link preview host") from exc
    if not records:
        raise BadRequestError(message="Could not resolve link preview host")
    for record in records:
        try:
            family, socktype, protocol, _canonical, address = record
            ip = ipaddress.ip_address(address[0])
            valid = (
                isinstance(address, tuple)
                and isinstance(address[0], str)
                and family in (socket.AF_INET, socket.AF_INET6)
                and socktype == socket.SOCK_STREAM
                and protocol in (0, socket.IPPROTO_TCP)
                and ip.version == (4 if family == socket.AF_INET else 6)
                and len(address) == (2 if family == socket.AF_INET else 4)
                and address[1] == port
                and not _is_disallowed_ip(str(ip))
            )
        except (ValueError, TypeError, IndexError):
            valid = False
        if not valid:
            raise BadRequestError(
                message="Refusing to fetch: host does not resolve exclusively to public addresses"
            )
    return records


class PublicHTTPConnection(HTTPConnection):
    """Connect directly to an address checked for this connection only."""

    def connect(self):
        sys.audit("http.client.connect", self, self.host, self.port)
        if self._tunnel_host:
            raise BadRequestError(
                message="Link preview proxy tunnels are not supported"
            )
        # Validate the entire answer before attempting even its first address;
        # a mixed public/private response must never permit private fallback.
        records = _public_addresses(self.host, self.port)
        last_error = None
        for family, socktype, protocol, _canonical, address in records:
            sock = socket.socket(family, socktype, protocol)
            try:
                if self.timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                    sock.settimeout(self.timeout)
                if self.source_address:
                    sock.bind(self.source_address)
                sock.connect(address)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError as exc:
                sock.close()
                last_error = exc
                continue
            except BaseException:
                sock.close()
                raise
            self.sock = sock
            return
        assert last_error is not None
        raise last_error


class PublicHTTPSConnection(HTTPSConnection, PublicHTTPConnection):
    """Retain stdlib verified TLS over the public-only socket connection."""


class PublicHTTPHandler(HTTPHandler):
    """Use the public-only transport on every HTTP redirect hop."""

    def http_open(self, request):
        return self.do_open(PublicHTTPConnection, request)


class PublicHTTPSHandler(HTTPSHandler):
    """Use the public-only transport while retaining stdlib TLS defaults."""

    def https_open(self, request):
        return self.do_open(PublicHTTPSConnection, request, context=self._context)
