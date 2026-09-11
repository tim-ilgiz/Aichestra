"""Loopback-only, unauthenticated HTTP for local inference discovery."""

from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener


def validate_local_endpoint(endpoint: str) -> str:
    raw = str(endpoint).strip().rstrip('/')
    try:
        url = urlsplit(raw)
        port = url.port
    except ValueError as exc:
        raise ValueError('Invalid local inference endpoint') from exc
    if (
        url.scheme not in {'http', 'https'}
        or url.hostname not in {'localhost', '127.0.0.1', '::1'}
        or url.username is not None or url.password is not None
        or url.query or url.fragment
        or any(ord(c) < 33 for c in raw)
    ):
        raise ValueError('Local inference endpoint must use localhost, 127.0.0.1, or ::1 without credentials')
    # Pin localhost to a literal loopback address, independent of DNS/hosts.
    host = '[::1]' if url.hostname == '::1' else '127.0.0.1'
    return urlunsplit((url.scheme, host + (f':{port}' if port is not None else ''), url.path, '', ''))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def local_urlopen(request, *, timeout):
    url = request.full_url if hasattr(request, 'full_url') else request
    canonical = validate_local_endpoint(url)
    if hasattr(request, 'full_url'):
        request.full_url = canonical
    else:
        request = canonical
    # Never let environment proxies or redirects leave the loopback boundary.
    return build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout)
