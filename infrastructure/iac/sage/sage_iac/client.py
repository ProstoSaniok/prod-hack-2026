import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

BASE_URL = "https://sage.spirit.t-education.com"
TENANT = "hack-led-03"


class SageError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SageError("Sage redirected the request; refusing to forward credentials")


class Client:
    def __init__(self, token):
        if not token or any(c.isspace() for c in token):
            raise SageError(
                "Supply a nonempty Sage token through the environment or a private file"
            )
        self._token = token
        self._opener = build_opener(NoRedirect())

    @classmethod
    def from_environment(cls):
        filename = os.environ.get("SAGE_TOKEN_FILE")
        if filename:
            path = Path(filename)
            if path.stat().st_mode & 0o077:
                raise SageError(
                    "Token file must not be accessible to group/other users"
                )
            token = path.read_text().strip()
        else:
            token = os.environ.get("SAGE_TOKEN", "").strip()
        return cls(token)

    def request(self, method, path, *, query=None, body=None):
        if not path.startswith("/") or path.startswith("//") or "?" in path:
            raise SageError("API paths must be relative paths without query strings")
        url = BASE_URL + path + ("?" + urlencode(query) if query else "")
        data = None if body is None else json.dumps(body).encode()
        headers = {
            "Authorization": "Bearer " + self._token,
            "Accept": "application/json",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(3):
            try:
                with self._opener.open(
                    Request(url, data=data, headers=headers, method=method), timeout=30
                ) as response:
                    payload = response.read(4_194_305)
                    if len(payload) > 4_194_304:
                        raise SageError("Sage response exceeded the size limit")
                    if not payload:
                        return None
                    if response.headers.get_content_type() != "application/json":
                        raise SageError(
                            "Sage returned non-JSON content; check API compatibility"
                        )
                    try:
                        return json.loads(payload)
                    except ValueError:
                        raise SageError("Sage returned invalid JSON") from None
            except HTTPError as error:
                if (
                    method == "GET"
                    and error.code in (429, 502, 503, 504)
                    and attempt < 2
                ):
                    error.close()
                    time.sleep(2**attempt)
                    continue
                # Do not print response bodies, credentials, or request payloads.
                status = error.code
                error.close()
                raise SageError(
                    f"Sage {method} {path} returned HTTP {status}"
                ) from None
            except (URLError, TimeoutError):
                raise SageError(
                    f"Sage {method} {path} failed; remote outcome may be unknown, re-plan"
                ) from None

    def verify_tenant(self):
        session = self.request("GET", "/sauron/api/v1/permissions/current-session")
        if not isinstance(session, dict) or not isinstance(
            session.get("tenantPermissions"), dict
        ):
            raise SageError("Unexpected Sage session schema")
        permissions = session.get("tenantPermissions", {}).get(TENANT, [])
        if "sage.user" not in permissions:
            raise SageError(f"Authenticated session does not authorize tenant {TENANT}")

    def verify_group(self, name):
        groups = self.request(
            "GET",
            "/sauron/api/group",
            query={"myOnly": "true", "excludeConfig": "true"},
        )
        if not isinstance(groups, list) or any(not isinstance(g, dict) for g in groups):
            raise SageError("Unexpected Sage group schema")
        # TenantKey filtering was ignored by the live API. Always check responses.
        matches = [
            g
            for g in groups
            if g.get("name") == name
            and g.get("tenantKey") == TENANT
            and not g.get("isDeleted")
        ]
        if len(matches) != 1:
            raise SageError(
                f"Group {name!r} is not assigned to this principal in {TENANT}; ask the tenant administrator"
            )
