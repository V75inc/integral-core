"""Standalone loopback-only chat preview. Run explicitly with --enable-preview."""

import argparse
import json
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = "/public-web-chat-preview"
COOKIE = "integral_preview_visitor"
SESSION_TTL = 20 * 60
MAX_SESSIONS = 128
MAX_BODY_BYTES = 4096
MAX_MESSAGE_LENGTH = 1000
MAX_TURNS = 12
SESSION_STARTS_PER_MINUTE = 24
MESSAGES_PER_MINUTE = 6
ASSETS = {
    ROOT: ("index.html", "text/html"),
    ROOT + "/embed": ("index.html", "text/html"),
    ROOT + "/embed-demo": ("embed-demo.html", "text/html"),
    ROOT + "/preview.css": ("preview.css", "text/css"),
    ROOT + "/preview.js": ("preview.js", "text/javascript"),
}


class PreviewError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


def unavailable():
    return PreviewError(HTTPStatus.NOT_FOUND, "Preview unavailable.")


def check_rate(events, now, limit):
    while events and now - events[0] >= 60:
        events.popleft()
    if len(events) >= limit:
        raise PreviewError(HTTPStatus.TOO_MANY_REQUESTS, "Please wait and try again.")
    events.append(now)


@dataclass
class VisitorSession:
    expires_at: float
    conversation: str = field(default_factory=lambda: secrets.token_urlsafe(18))
    # Every visitor has a distinct in-memory conversation pinned to the one
    # synthetic preview profile. Profiles and workspace IDs never come from JSON.
    messages: list[dict[str, str]] = field(default_factory=list)
    turns: deque[float] = field(default_factory=deque)

    def history(self):
        return {"conversation": self.conversation, "messages": self.messages}


class PreviewChannel:
    def __init__(self, *, enabled=False, clock=time.monotonic):
        self.enabled = enabled
        self.clock = clock
        self.sessions: dict[str, VisitorSession] = {}
        self.starts: deque[float] = deque()

    def require_enabled(self):
        if not self.enabled:
            raise unavailable()

    def session(self, token):
        self.require_enabled()
        session = self.sessions.get(token)
        if session is None or session.expires_at <= self.clock():
            self.sessions.pop(token, None)
            raise PreviewError(HTTPStatus.UNAUTHORIZED, "Start a new preview chat.")
        return session

    def start(self, previous_token):
        self.require_enabled()
        now = self.clock()
        check_rate(self.starts, now, SESSION_STARTS_PER_MINUTE)
        self.sessions = {
            token: session
            for token, session in self.sessions.items()
            if session.expires_at > now and token != previous_token
        }
        if len(self.sessions) >= MAX_SESSIONS:
            raise PreviewError(
                HTTPStatus.SERVICE_UNAVAILABLE, "Try again in a few minutes."
            )
        token = secrets.token_urlsafe(32)
        self.sessions[token] = VisitorSession(now + SESSION_TTL)
        return token

    def send(self, token, body):
        session = self.session(token)
        message = body.get("message")
        if (
            set(body) != {"conversation", "message"}
            or not isinstance(message, str)
            or not message.strip()
            or len(message) > MAX_MESSAGE_LENGTH
        ):
            raise PreviewError(
                HTTPStatus.BAD_REQUEST,
                f"Enter a message of 1–{MAX_MESSAGE_LENGTH} characters.",
            )
        if body["conversation"] != session.conversation:
            raise PreviewError(
                HTTPStatus.PRECONDITION_FAILED, "This chat changed. Start a new chat."
            )
        if len(session.messages) >= MAX_TURNS * 2:
            raise PreviewError(HTTPStatus.CONFLICT, "Start a new chat to continue.")
        check_rate(session.turns, self.clock(), MESSAGES_PER_MINUTE)
        display = message.strip()
        if display.casefold() in {"hello", "hi", "hey"}:
            reply = "Hello! You’re chatting with the local preview assistant."
        else:
            reply = "Thanks for trying the preview. This reply was generated locally using sample responses."
        session.messages.extend(
            ({"role": "visitor", "text": display}, {"role": "assistant", "text": reply})
        )
        return session.history()


class PreviewServer(HTTPServer):
    def __init__(self, port, channel):
        self.channel = channel
        super().__init__(("127.0.0.1", port), PreviewHandler)
        self.authority = f"127.0.0.1:{self.server_port}"


class PreviewHandler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(2)
        super().setup()

    def log_message(self, _format, *_args):
        # Keep browser messages and transport details out of persistent logs.
        pass

    def _send(self, status, content, kind, *, token=None, document_path=""):
        self.send_response(status)
        self.send_header("Content-Type", f"{kind}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        frame_sources = (
            "frame-src 'self'; " if document_path == ROOT + "/embed-demo" else ""
        )
        frame_ancestors = "'self'" if document_path == ROOT + "/embed" else "'none'"
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            f"{frame_sources}frame-ancestors {frame_ancestors}; base-uri 'none'; form-action 'none'",
        )
        if token:
            self.send_header(
                "Set-Cookie",
                f"{COOKIE}={token}; Path={ROOT}; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL}",
            )
        self.end_headers()
        self.wfile.write(content)

    def _json(self, status, body, *, token=None):
        self._send(status, json.dumps(body).encode(), "application/json", token=token)

    def _token(self):
        try:
            return SimpleCookie(self.headers.get("Cookie", ""))[COOKIE].value
        except (CookieError, KeyError):
            return ""

    def _body(self):
        if self.headers.get_content_type() != "application/json":
            raise PreviewError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Use JSON.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length > MAX_BODY_BYTES:
            raise PreviewError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Message too large."
            )
        if length < 1 or self.headers.get("Transfer-Encoding"):
            raise PreviewError(HTTPStatus.BAD_REQUEST, "Invalid request.")
        try:
            payload = json.loads(self.rfile.read(length))
        except (UnicodeError, ValueError, RecursionError, TimeoutError):
            raise PreviewError(HTTPStatus.BAD_REQUEST, "Invalid request.") from None
        if not isinstance(payload, dict):
            raise PreviewError(HTTPStatus.BAD_REQUEST, "Invalid request.")
        return payload

    def _dispatch(self, *, post=False):
        channel = self.server.channel
        try:
            if self.headers.get_all("Host") != [self.server.authority] or any(
                name.lower() == "forwarded" or name.lower().startswith("x-forwarded-")
                for name in self.headers
            ):
                raise unavailable()
            channel.require_enabled()
            if post:
                if self.headers.get_all("Origin") != [
                    "http://" + self.server.authority
                ]:
                    raise PreviewError(HTTPStatus.FORBIDDEN, "Request unavailable.")
                if self.path == ROOT + "/session":
                    if self._body():
                        raise PreviewError(HTTPStatus.BAD_REQUEST, "Invalid request.")
                    token = channel.start(self._token())
                    self._json(
                        HTTPStatus.CREATED,
                        channel.session(token).history(),
                        token=token,
                    )
                    return
                if self.path == ROOT + "/message":
                    self._json(HTTPStatus.OK, channel.send(self._token(), self._body()))
                    return
            elif self.path in ASSETS:
                name, kind = ASSETS[self.path]
                content = Path(__file__).with_name(name).read_bytes()
                if self.path == ROOT + "/embed":
                    content = content.replace(b"<body>", b'<body class="embedded">', 1)
                self._send(HTTPStatus.OK, content, kind, document_path=self.path)
                return
            elif self.path == ROOT + "/session":
                self._json(HTTPStatus.OK, channel.session(self._token()).history())
                return
            raise unavailable()
        except PreviewError as error:
            self._json(error.status, {"error": error.message})

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch(post=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9147)
    parser.add_argument("--enable-preview", action="store_true")
    args = parser.parse_args()
    if not args.enable_preview:
        parser.error(
            "The preview is disabled. Pass --enable-preview for a local walkthrough."
        )
    with PreviewServer(args.port, PreviewChannel(enabled=True)) as server:
        print(f"Local preview ready at http://{server.authority}{ROOT}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
