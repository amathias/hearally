"""Plaud sign-in (experimental): reach a Plaud account through Plaud's hosted MCP.

A test of the "Connect Plaud" route in ARCHITECTURE.md section 2. The account holder
signs in to Plaud once, on Plaud's own consent page, and Hearally's servers can
then list their recordings and get a 24-hour audio link for each, with no
computer and no export. Plaud describes MCP as connecting "your personal Plaud
data" to your own tools and publishes no terms for a service using it, so this
is a prototype pending Plaud's permission, tried on the operator's own account.

How it works (OAuth 2.1 with PKCE, a public client, as the MCP spec describes):

1. ``start``: a random state and PKCE verifier are stored for ten minutes under
   the signed-in account, and the browser is sent to Plaud's authorize page.
2. Plaud sends the browser back to /dashboard/integrations/plaud/ with a code.
   That page, still signed in to Hearally, posts the code and state to
   ``finish``. The state must be one this account started, unused and
   unexpired, so a code can only ever land on the account that asked for it.
3. ``finish`` trades the code for tokens and stores them under ``PLAUD#<sub>``,
   a partition the dashboard's read grant (``USER#*``) cannot reach.
4. ``check`` talks MCP to Plaud: lists recordings and asks for the newest
   one's audio link, then reports what came back. It never downloads audio
   beyond one byte, never asks for Plaud's transcripts or summaries, and never
   returns or logs a token or a link.

Hearally is registered with Plaud's authorization server by dynamic client
registration (RFC 7591), which returned a public client id bound to the one
redirect URI below. Nothing here is secret: a public client has no secret, and
PKCE plus the stored state protect the exchange.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

AUTHORIZE_URL = "https://mcp.plaud.ai/authorize"
TOKEN_URL = "https://mcp.plaud.ai/token"  # noqa: S105 - an endpoint, not a secret
MCP_URL = "https://mcp.plaud.ai/mcp"
REDIRECT_URI = "https://hearally.com/dashboard/integrations/plaud/"
# Issued by POST https://mcp.plaud.ai/register on 2026-09-27 for REDIRECT_URI.
CLIENT_ID = (
    "eyJ2IjoyLCJuIjoiSGVhcmFsbHkgKHByb3RvdHlwZSkiLCJoIjpbImhlYXJhbGx5LmNvbSJdLCJyIjoiUjNTcm5STjNHZXMifQ"
    ".qpokbXZ55HFRoM57CsEH1g"
)
MCP_PROTOCOL_VERSION = "2025-06-18"
STATE_SECONDS = 10 * 60
TIMEOUT = 12.0
LATEST = 5


class PlaudLinkError(Exception):
    """Something went wrong talking to Plaud. The message is safe to show."""


class NotConnectedError(Exception):
    pass


class StateError(Exception):
    """Unknown, used or expired sign-in state: all look the same from outside."""


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


Http = Callable[[str, str, Mapping[str, str], bytes | None], HttpResponse]


def urllib_http(
    method: str, url: str, headers: Mapping[str, str], body: bytes | None
) -> HttpResponse:
    if not url.startswith("https://"):
        raise PlaudLinkError("refusing a link that is not HTTPS")
    request = urllib.request.Request(url, data=body, method=method, headers=dict(headers))  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as r:  # noqa: S310 - HTTPS checked
            return HttpResponse(r.status, {k.lower(): v for k, v in r.headers.items()}, r.read())
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read())
    except (urllib.error.URLError, TimeoutError) as exc:
        raise PlaudLinkError("Plaud could not be reached") from exc


class PlaudLinkRepository(Protocol):
    def save_state(self, sub: str, state: str, verifier: str, expires: int) -> None: ...
    def take_state(self, sub: str, state: str, now: int) -> str | None: ...
    def save_link(self, sub: str, link: dict[str, Any]) -> None: ...
    def get_link(self, sub: str) -> dict[str, Any] | None: ...
    def delete_link(self, sub: str) -> bool: ...
    def register(self, sub: str) -> None: ...
    def connected_accounts(self) -> list[str]: ...
    def imported_ids(self, sub: str) -> set[str]: ...
    def mark_imported(self, sub: str, file_id: str, event_id: str, now: int) -> None: ...


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class InMemoryPlaudLinkRepository:
    def __init__(self) -> None:
        self.states: dict[tuple[str, str], tuple[str, int]] = {}
        self.links: dict[str, dict[str, Any]] = {}
        self.registry: set[str] = set()
        self.imported: dict[str, dict[str, str]] = {}

    def save_state(self, sub: str, state: str, verifier: str, expires: int) -> None:
        self.states[(sub, _digest(state))] = (verifier, expires)

    def take_state(self, sub: str, state: str, now: int) -> str | None:
        found = self.states.pop((sub, _digest(state)), None)
        return found[0] if found and found[1] > now else None

    def save_link(self, sub: str, link: dict[str, Any]) -> None:
        self.links[sub] = dict(link)
        self.registry.add(sub)

    def get_link(self, sub: str) -> dict[str, Any] | None:
        return dict(self.links[sub]) if sub in self.links else None

    def delete_link(self, sub: str) -> bool:
        self.registry.discard(sub)
        return self.links.pop(sub, None) is not None

    def register(self, sub: str) -> None:
        self.registry.add(sub)

    def connected_accounts(self) -> list[str]:
        return sorted(self.registry)

    def imported_ids(self, sub: str) -> set[str]:
        return set(self.imported.get(sub, {}))

    def mark_imported(self, sub: str, file_id: str, event_id: str, now: int) -> None:
        self.imported.setdefault(sub, {})[file_id] = event_id


class DynamoPlaudLinkRepository:
    """``PLAUD#<sub>``: ``STATE#<sha256>`` for a pending sign-in (TTL ten
    minutes, taken once), ``LINK`` for the tokens, and ``IMPORTED#<file_id>``
    for each recording already imported. ``PLAUDLINKS`` / ``ACCOUNT#<sub>``
    lists the connected accounts, so the importer never scans the table."""

    def __init__(self, table: Any) -> None:
        self._table = table

    def save_state(self, sub: str, state: str, verifier: str, expires: int) -> None:
        self._table.put_item(
            Item={
                "pk": f"PLAUD#{sub}",
                "sk": f"STATE#{_digest(state)}",
                "verifier": verifier,
                "expires_epoch": expires,
                "ttl_epoch": expires,
            }
        )

    def take_state(self, sub: str, state: str, now: int) -> str | None:
        try:
            old = self._table.delete_item(
                Key={"pk": f"PLAUD#{sub}", "sk": f"STATE#{_digest(state)}"},
                ConditionExpression="attribute_exists(pk) AND expires_epoch > :now",
                ExpressionAttributeValues={":now": now},
                ReturnValues="ALL_OLD",
            )
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            return None
        return str(old.get("Attributes", {}).get("verifier") or "") or None

    def save_link(self, sub: str, link: dict[str, Any]) -> None:
        self._table.put_item(Item={"pk": f"PLAUD#{sub}", "sk": "LINK", **link})
        self.register(sub)

    def register(self, sub: str) -> None:
        self._table.put_item(Item={"pk": "PLAUDLINKS", "sk": f"ACCOUNT#{sub}"})

    def connected_accounts(self) -> list[str]:
        subs: list[str] = []
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": "pk = :pk AND begins_with(sk, :sk)",
            "ExpressionAttributeValues": {":pk": "PLAUDLINKS", ":sk": "ACCOUNT#"},
        }
        while True:
            page = self._table.query(**kwargs)
            subs += [str(i["sk"]).split("#", 1)[1] for i in page.get("Items", [])]
            if "LastEvaluatedKey" not in page:
                return subs
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def imported_ids(self, sub: str) -> set[str]:
        ids: set[str] = set()
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": "pk = :pk AND begins_with(sk, :sk)",
            "ExpressionAttributeValues": {":pk": f"PLAUD#{sub}", ":sk": "IMPORTED#"},
            "ProjectionExpression": "sk",
        }
        while True:
            page = self._table.query(**kwargs)
            ids |= {str(i["sk"]).split("#", 1)[1] for i in page.get("Items", [])}
            if "LastEvaluatedKey" not in page:
                return ids
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def mark_imported(self, sub: str, file_id: str, event_id: str, now: int) -> None:
        self._table.put_item(
            Item={
                "pk": f"PLAUD#{sub}",
                "sk": f"IMPORTED#{file_id}",
                "event_id": event_id,
                "imported_epoch": now,
            }
        )

    def get_link(self, sub: str) -> dict[str, Any] | None:
        item = self._table.get_item(
            Key={"pk": f"PLAUD#{sub}", "sk": "LINK"}, ConsistentRead=True
        ).get("Item")
        if not item:
            return None
        return {
            k: (int(v) if k.endswith("_epoch") else v)
            for k, v in item.items()
            if k not in {"pk", "sk"}
        }

    def delete_link(self, sub: str) -> bool:
        self._table.delete_item(Key={"pk": "PLAUDLINKS", "sk": f"ACCOUNT#{sub}"})
        old = self._table.delete_item(
            Key={"pk": f"PLAUD#{sub}", "sk": "LINK"}, ReturnValues="ALL_OLD"
        )
        return bool(old.get("Attributes"))


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _json_body(response: HttpResponse) -> dict[str, Any]:
    try:
        data = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _rpc_message(response: HttpResponse, request_id: int) -> dict[str, Any]:
    """The JSON-RPC reply to ``request_id``, from a JSON or an SSE response."""
    if "text/event-stream" in response.headers.get("content-type", ""):
        for block in response.body.decode("utf-8", errors="replace").split("\n\n"):
            data = "\n".join(
                line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")
            )
            if not data:
                continue
            try:
                message = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict) and message.get("id") == request_id:
                return message
        return {}
    message = _json_body(response)
    return message if message.get("id") == request_id else {}


def _tool_payload(result: dict[str, Any]) -> Any:
    """What a tool returned: its structured content, or its text parsed as
    JSON when it is JSON, or the text itself."""
    if "structuredContent" in result:
        return result["structuredContent"]
    texts = [
        c.get("text", "")
        for c in result.get("content") or []
        if isinstance(c, dict) and c.get("type") == "text"
    ]
    joined = "\n".join(texts)
    try:
        return json.loads(joined)
    except json.JSONDecodeError:
        pass
    # Plaud wraps its data in a sentence and an <untrusted-data-...> tag, a
    # guard against prompt injection for AI clients. Take the JSON inside.
    decoder = json.JSONDecoder()
    for text in (joined, _jsonish(joined)):
        for i, char in enumerate(text):
            if char in "{[":
                try:
                    return decoder.raw_decode(text, i)[0]
                except json.JSONDecodeError:
                    continue
    return joined


_PY_LITERAL = re.compile(r"(?<=[:\[,\s])(None|True|False)(?=\s*[,}\]])")


def _jsonish(text: str) -> str:
    """Python's None, True and False as JSON's null, true and false, for a
    server that writes its data with Python's repr rather than JSON."""
    return _PY_LITERAL.sub(
        lambda m: {"None": "null", "True": "true", "False": "false"}[m.group(1)], text
    )


def parse_error(text: str) -> dict[str, Any] | None:
    """Where strict JSON parsing of the first object in ``text`` fails, with
    a masked excerpt around it: enough to fix the parser, nothing said."""
    start = next((i for i, c in enumerate(text) if c in "{["), None)
    if start is None:
        return {"error": "no JSON object found"}
    try:
        json.JSONDecoder().raw_decode(text, start)
    except json.JSONDecodeError as exc:
        return {
            "error": exc.msg,
            "at": exc.pos - start,
            "near": mask(text[max(start, exc.pos - 80) : exc.pos + 40]),
        }
    return None


def records_in(payload: Any) -> list[dict[str, Any]]:
    """The list of recordings inside a list_files payload, wherever it sits."""
    if isinstance(payload, list):
        return [p for p in payload if isinstance(p, dict)]
    if isinstance(payload, dict):
        for key in ("files", "items", "data", "recordings", "results", "list"):
            if isinstance(payload.get(key), list):
                return records_in(payload[key])
            if isinstance(payload.get(key), dict):
                found = records_in(payload[key])
                if found:
                    return found
    return []


def first_of(record: Mapping[str, Any], *keys: str) -> Any:
    return next((record[k] for k in keys if record.get(k) not in (None, "")), None)


def seconds_of(record: Mapping[str, Any]) -> float | None:
    """A recording's length in seconds. Plaud counts ``duration`` in
    milliseconds: a 90-second test recording listed as 90360."""
    value = record.get("duration")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(value / 1000, 1)


def find_url(payload: Any) -> str | None:
    """The first HTTPS link in a get_file payload: the audio download link."""
    if isinstance(payload, str):
        return payload if payload.startswith("https://") else None
    if isinstance(payload, dict):
        for key in ("url", "audio_url", "download_url", "presigned_url", "file_url"):
            if isinstance(payload.get(key), str) and payload[key].startswith("https://"):
                return str(payload[key])
        for value in payload.values():
            found = find_url(value)
            if found:
                return found
    if isinstance(payload, list):
        for value in payload:
            found = find_url(value)
            if found:
                return found
    return None


def _id_argument(schema: Mapping[str, Any]) -> str:
    """The name get_file takes the recording's id under, from its schema."""
    required = [str(r) for r in schema.get("required") or []]
    properties = [str(p) for p in (schema.get("properties") or {})]
    for name in (*required, *properties):
        if "id" in name.lower():
            return name
    return required[0] if required else "file_id"


# Only quoted keys, as in JSON. An unquoted word before a colon may be part
# of a recording's name ("Briefing:"), so it is masked like everything else.
_KEY = re.compile(r'"[a-z_]{2,30}"\s*:')


def mask(text: str) -> str:
    """Text with every letter as ``a`` and digit as ``9``, keeping punctuation
    and quoted field names: the outline of a reply, with nothing it says."""

    def hide(part: str) -> str:
        return re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "9", part))

    out, i = [], 0
    for m in _KEY.finditer(text):
        out.append(hide(text[i : m.start()]))
        out.append(m.group(0))
        i = m.end()
    out.append(hide(text[i:]))
    return "".join(out)


class McpSession:
    """Just enough of MCP over streamable HTTP: initialize, then tool calls."""

    def __init__(self, http: Http, access_token: str) -> None:
        self._http = http
        self._token = access_token
        self._session: str | None = None
        self._next = 0

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        }
        if self._session:
            headers["Mcp-Session-Id"] = self._session
        return headers

    def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._next += 1
        body = {"jsonrpc": "2.0", "id": self._next, "method": method, "params": params}
        response = self._http("POST", MCP_URL, self._headers(), json.dumps(body).encode())
        if response.status == 401:
            raise NotConnectedError
        if response.status >= 400:
            raise PlaudLinkError(f"Plaud's MCP server answered {response.status} to {method}")
        self._session = response.headers.get("mcp-session-id", self._session)
        message = _rpc_message(response, self._next)
        if "error" in message:
            error = message["error"] if isinstance(message["error"], dict) else {}
            raise PlaudLinkError(f"{method} failed: {str(error.get('message', ''))[:120]}")
        if "result" not in message:
            raise PlaudLinkError(f"{method} returned nothing Hearally could read")
        return message["result"] if isinstance(message["result"], dict) else {}

    def open(self) -> dict[str, Any]:
        result = self._call(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "hearally", "version": "0.1"},
            },
        )
        note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        self._http("POST", MCP_URL, self._headers(), json.dumps(note).encode())
        return result

    def tools(self) -> list[dict[str, Any]]:
        tools = self._call("tools/list", {}).get("tools") or []
        return [t for t in tools if isinstance(t, dict)]

    def tool(self, name: str, arguments: dict[str, Any]) -> Any:
        result = self._call("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise PlaudLinkError(f"{name} failed: {str(_tool_payload(result))[:120]}")
        return _tool_payload(result)


class PlaudLinkService:
    def __init__(
        self,
        repository: PlaudLinkRepository,
        http: Http = urllib_http,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repo = repository
        self._http = http
        self._clock = clock

    def start(self, sub: str) -> str:
        """The Plaud authorize URL to send the browser to."""
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        self._repo.save_state(sub, state, verifier, int(self._clock()) + STATE_SECONDS)
        query = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
                "state": state,
                "code_challenge": _pkce_challenge(verifier),
                "code_challenge_method": "S256",
                "resource": MCP_URL,
            }
        )
        return f"{AUTHORIZE_URL}?{query}"

    def _token_request(self, fields: dict[str, str]) -> dict[str, Any]:
        response = self._http(
            "POST",
            TOKEN_URL,
            {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            urllib.parse.urlencode(fields).encode(),
        )
        data = _json_body(response)
        if response.status >= 400 or not isinstance(data.get("access_token"), str):
            # OAuth error codes are fixed words, safe to show; never the body.
            code = str(data.get("error", ""))[:40]
            raise PlaudLinkError(f"Plaud refused the sign-in ({response.status} {code})".strip())
        return data

    def _store(
        self, sub: str, tokens: dict[str, Any], previous: dict[str, Any] | None
    ) -> dict[str, Any]:
        now = int(self._clock())
        link = {
            "access_token": tokens["access_token"],
            # A refresh may or may not rotate the refresh token.
            "refresh_token": tokens.get("refresh_token")
            or (previous or {}).get("refresh_token", ""),
            "access_expires_epoch": now + int(tokens.get("expires_in") or 3600),
            "connected_epoch": (previous or {}).get("connected_epoch", now),
        }
        self._repo.save_link(sub, link)
        return link

    def finish(self, sub: str, code: str, state: str) -> None:
        verifier = self._repo.take_state(sub, state, int(self._clock()))
        if not verifier:
            raise StateError
        tokens = self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID,
                "code_verifier": verifier,
                "resource": MCP_URL,
            }
        )
        self._store(sub, tokens, None)

    def access_token(self, sub: str, *, force: bool = False) -> str:
        link = self._repo.get_link(sub)
        if not link:
            raise NotConnectedError
        if not force and link.get("access_expires_epoch", 0) > self._clock() + 60:
            return str(link["access_token"])
        if not link.get("refresh_token"):
            raise NotConnectedError
        try:
            tokens = self._token_request(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": link["refresh_token"],
                    "client_id": CLIENT_ID,
                    "resource": MCP_URL,
                }
            )
        except PlaudLinkError as exc:
            raise NotConnectedError from exc
        return str(self._store(sub, tokens, link)["access_token"])

    def status(self, sub: str) -> dict[str, Any]:
        link = self._repo.get_link(sub)
        return {
            "connected": bool(link),
            "connected_epoch": (link or {}).get("connected_epoch"),
            "imported": len(self._repo.imported_ids(sub)) if link else 0,
        }

    def check(self, sub: str) -> dict[str, Any]:
        """Ask Plaud what it will share, and report it without tokens or links."""
        try:
            return self._check(sub, self.access_token(sub))
        except NotConnectedError:
            # An expired access token that looked fresh: refresh once, retry.
            return self._check(sub, self.access_token(sub, force=True))

    def _check(self, sub: str, token: str) -> dict[str, Any]:
        mcp = McpSession(self._http, token)
        server = mcp.open().get("serverInfo") or {}
        tools = mcp.tools()
        names = [str(t.get("name")) for t in tools]
        self._repo.register(sub)
        report: dict[str, Any] = {
            "connected": True,
            "connected_epoch": (self._repo.get_link(sub) or {}).get("connected_epoch"),
            "imported": len(self._repo.imported_ids(sub)),
            "server": {
                "name": str(server.get("name", ""))[:60],
                "version": str(server.get("version", ""))[:20],
            },
            "tools": names,
        }
        if "list_files" not in names:
            report["recordings"] = None
            return report
        payload = mcp.tool("list_files", {})
        records = records_in(payload)
        if not records:
            # Not a shape this code knows yet. Show its outline, with every
            # word and number masked, so the parser can be fixed without
            # anyone seeing a recording's name.
            schemas = {
                str(t.get("name")): t.get("inputSchema")
                for t in tools
                if t.get("name") in {"list_files", "get_file"}
            }
            text = payload if isinstance(payload, str) else json.dumps(payload)
            report["diagnostic"] = {
                "list_files_outline": mask(text)[:1200],
                "parse_error": parse_error(text),
                "schemas": schemas,
            }
        latest = []
        for r in records[:LATEST]:
            latest.append(
                {
                    "name": str(first_of(r, "name", "filename", "title") or "")[:120],
                    "created_at": first_of(r, "start_at", "created_at", "start_time"),
                    "duration_s": seconds_of(r),
                }
            )
        report["recordings"] = {"listed": len(records), "latest": latest}
        if records and "get_file" in names:
            schema = next(t.get("inputSchema") or {} for t in tools if t.get("name") == "get_file")
            report["audio"] = self._audio_check(mcp, records[0], _id_argument(schema))
        return report

    def _audio_check(
        self, mcp: McpSession, record: Mapping[str, Any], argument: str
    ) -> dict[str, Any]:
        """Is a download link offered for the newest recording, and does it
        work? Fetches one byte to prove it; the link itself goes nowhere."""
        file_id = first_of(record, "id", "file_id", "fileId")
        if file_id is None:
            return {"available": False, "reason": "no id in the listing"}
        url = find_url(mcp.tool("get_file", {argument: file_id}))
        if not url:
            return {"available": False, "reason": "get_file gave no link"}
        response = self._http("GET", url, {"Range": "bytes=0-0"}, None)
        total = response.headers.get("content-range", "").rpartition("/")[2]
        return {
            "available": response.status in (200, 206),
            "status": response.status,
            "bytes": int(total) if total.isdigit() else None,
            "type": response.headers.get("content-type", "")[:40],
        }

    def disconnect(self, sub: str) -> bool:
        return self._repo.delete_link(sub)
