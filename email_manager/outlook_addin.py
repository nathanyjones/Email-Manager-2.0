"""Local HTTPS bridge for the private Outlook task-pane proof of concept.

Every route is localhost-only. Message reads and LLM questions occur only after an
explicit click in the Outlook panel; this server never scans, sends, moves, or deletes mail.
"""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import ssl
from urllib.parse import unquote, urlsplit

from .ai import EmailAssistant
from .config import Settings
from .graph import GraphClient
from .service import EmailManager
from .store import FEEDBACK_TYPES, Store


ASSET_ROOT = Path(__file__).resolve().parent.parent / "outlook_addin"
MAX_BODY_BYTES = 20_000


def _message_payload(message_id: str, store: Store) -> dict[str, object]:
    message = store.get_processed_message(message_id)
    if message is None:
        return {"status": "not_processed", "message_id": message_id}
    return {
        "status": "available", "message_id": message.message_id, "sender_email": message.sender_email,
        "subject": message.subject, "category": message.category, "needs_response": message.needs_response,
        "needs_action": message.needs_action, "has_draft": message.has_draft, "summary": message.summary,
        "suggested_folder": message.suggested_folder, "draft_web_link": message.draft_web_link,
        "draft_reason": message.draft_reason, "feedback_type": message.feedback_type,
        "priority": message.priority, "action_items": message.action_items,
        "suggested_followup_time": message.suggested_followup_time, "confidence": message.confidence,
        "rationale": message.rationale,
    }


def _local_summary(payload: dict[str, object]) -> str:
    if payload["status"] != "available":
        return "No prior local Email Manager decision is available for this message."
    return (
        f"Category: {payload['category']}. Summary: {payload['summary']}. "
        f"Draft status: {'created' if payload['has_draft'] else 'not created'}. "
        f"Draft reason: {payload['draft_reason']}"
    )


def serve_outlook_addin(settings: Settings, port: int, certificate: Path, private_key: Path) -> None:
    """Serve the add-in UI and same-origin local API over HTTPS."""
    if not certificate.is_file() or not private_key.is_file():
        raise ValueError("Outlook add-in requires existing HTTPS certificate and key files")

    class Handler(BaseHTTPRequestHandler):
        server_version = "EmailManagerOutlookPOC/0.1"

        def log_message(self, format: str, *args: object) -> None:
            print("Outlook add-in:", format % args)

        def _send_json(self, status: int, payload: dict[str, object]) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _read_json(self) -> dict[str, object] | None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY_BYTES:
                    return None
                data = json.loads(self.rfile.read(length))
                return data if isinstance(data, dict) else None
            except (ValueError, json.JSONDecodeError):
                return None

        def _store(self) -> Store:
            return Store(settings.database_path)

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path == "/api/health":
                self._send_json(HTTPStatus.OK, {"status": "ok", "scope": "localhost-only"})
                return
            if path.startswith("/api/message/"):
                message_id = unquote(path.removeprefix("/api/message/"))
                if not message_id or len(message_id) > 2048:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid message ID."})
                    return
                store = self._store()
                try:
                    self._send_json(HTTPStatus.OK, _message_payload(message_id, store))
                finally:
                    store.close()
                return
            asset = "taskpane.html" if path == "/" else path.lstrip("/")
            if asset not in {"taskpane.html", "taskpane.js", "taskpane.css", "icon.svg"}:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            content = (ASSET_ROOT / asset).read_bytes()
            content_type = {"taskpane.html": "text/html; charset=utf-8", "taskpane.js": "application/javascript; charset=utf-8", "taskpane.css": "text/css; charset=utf-8", "icon.svg": "image/svg+xml"}[asset]
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def do_POST(self) -> None:  # noqa: N802
            payload = self._read_json()
            if payload is None:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Expected a small JSON request."})
                return
            message_id = str(payload.get("message_id", ""))
            if not message_id or len(message_id) > 2048:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid message ID."})
                return
            if urlsplit(self.path).path == "/api/feedback":
                feedback_type = str(payload.get("feedback_type", ""))
                if feedback_type not in FEEDBACK_TYPES:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Unknown feedback choice."})
                    return
                store = self._store()
                try:
                    feedback = store.record_feedback(message_id, feedback_type, str(payload.get("note", "")))
                    self._send_json(HTTPStatus.OK, {"status": "saved", "feedback_type": feedback.feedback_type})
                except ValueError as error:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                finally:
                    store.close()
                return
            if urlsplit(self.path).path == "/api/analyze":
                store = self._store()
                try:
                    graph = GraphClient(settings.client_id, settings.token_cache_path)
                    manager = EmailManager(settings, graph, EmailAssistant(settings.openai_api_key, settings.model), store)
                    manager.process_message(message_id)
                    self._send_json(HTTPStatus.OK, _message_payload(message_id, store))
                except (ValueError, RuntimeError) as error:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                except Exception as error:
                    self._send_json(HTTPStatus.BAD_GATEWAY, {"error": f"Couldn't analyze this email: {error}"})
                finally:
                    store.close()
                return
            if urlsplit(self.path).path == "/api/question":
                question = str(payload.get("question", "")).strip()
                if not question or len(question) > 1000:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Ask one question of up to 1,000 characters."})
                    return
                store = self._store()
                try:
                    local = _message_payload(message_id, store)
                finally:
                    store.close()
                try:
                    email = GraphClient(settings.client_id, settings.token_cache_path).get_message(message_id)
                    if email is None:
                        self._send_json(HTTPStatus.NOT_FOUND, {"error": "This email is no longer available in the signed-in mailbox."})
                        return
                    answer = EmailAssistant(settings.openai_api_key, settings.model).answer_question(email, question, _local_summary(local))
                    self._send_json(HTTPStatus.OK, {"answer": answer})
                except Exception as error:
                    self._send_json(HTTPStatus.BAD_GATEWAY, {"error": f"Couldn't answer this question: {error}"})
                return
            self.send_error(HTTPStatus.NOT_FOUND)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, private_key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    print(f"Email Manager Outlook add-in: https://localhost:{port}/taskpane.html (localhost only; press Ctrl+C to stop)")
    try:
        server.serve_forever()
    finally:
        server.server_close()
