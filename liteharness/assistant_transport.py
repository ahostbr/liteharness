"""Assistant-only fixed loopback transport; shared bridge helpers stay unchanged."""
import urllib.error
import urllib.request

ENDPOINT = "http://127.0.0.1:7423/v1/sentinel/assistant-message"


class RefuseAssistantRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "assistant redirect refused", headers, fp)

    def http_error_302(self, req, fp, code, msg, headers):
        # Refuse before urllib can normalize/resolve Location or construct a
        # replacement request, including same-origin redirects.
        raise urllib.error.HTTPError(req.full_url, code, "assistant redirect refused", headers, fp)

    http_error_301 = http_error_302
    http_error_303 = http_error_302
    http_error_307 = http_error_302
    http_error_308 = http_error_302


def send_assistant(payload, token):
    request = urllib.request.Request(
        ENDPOINT, data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), RefuseAssistantRedirect())
    with opener.open(request, timeout=5) as response:
        return response.status
