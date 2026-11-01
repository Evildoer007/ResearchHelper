"""OH-style portable HTTPS trust for model, supplier and filing transports.

Use the CA resource collected with the App, never the build machine's OpenSSL
paths. RH keeps its existing process cancellation, deadlines and call ledger.
"""
import ssl
from urllib.request import urlopen, build_opener, HTTPSHandler, HTTPRedirectHandler
from urllib.error import HTTPError

import certifi


def bundled_ca_file():
    return certifi.where()


def verified_ssl_context():
    """Certificate and hostname verification are never relaxed or retried."""
    return ssl.create_default_context(cafile=bundled_ca_file())


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise HTTPError(request.full_url, code, '数据源跳转未获准', headers, response)


def open_verified_https(request, *, timeout, opener=urlopen, follow_redirects=True):
    """Explicit test/host openers keep their interface, as in OH transport."""
    if opener is not urlopen:
        return opener(request, timeout=timeout)
    if not follow_redirects:
        return build_opener(HTTPSHandler(context=verified_ssl_context()), _NoRedirect()).open(request, timeout=timeout)
    return opener(request, timeout=timeout, context=verified_ssl_context())
