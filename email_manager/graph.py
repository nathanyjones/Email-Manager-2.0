"""Minimal Microsoft Graph client for safe Outlook triage."""

from __future__ import annotations

from datetime import datetime, timezone
import html
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

import msal
import requests

from .models import Email, MailFolder


GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
SCOPES = ["User.Read", "Mail.ReadWrite", "MailboxSettings.ReadWrite"]


class GraphClient:
    def __init__(self, client_id: str, cache_path: Path) -> None:
        self.client_id = client_id
        self.cache_path = cache_path
        self._token: str | None = None

    def _access_token(self) -> str:
        if self._token:
            return self._token
        cache = msal.SerializableTokenCache()
        if self.cache_path.exists():
            cache.deserialize(self.cache_path.read_text())
        app = msal.PublicClientApplication(
            self.client_id, authority="https://login.microsoftonline.com/common", token_cache=cache
        )
        accounts = app.get_accounts()
        result = app.acquire_token_silent(SCOPES, account=accounts[0]) if accounts else None
        if not result:
            flow = app.initiate_device_flow(scopes=SCOPES)
            if "user_code" not in flow:
                raise RuntimeError("Unable to start Microsoft device login")
            print(flow["message"])
            result = app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            raise RuntimeError(f"Microsoft login failed: {result.get('error_description', result)}")
        self.cache_path.write_text(cache.serialize())
        try:
            os.chmod(self.cache_path, 0o600)
        except OSError:
            pass
        self._token = result["access_token"]
        return self._token

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        response = requests.request(
            method, GRAPH_ROOT + path,
            headers={"Authorization": f"Bearer {self._access_token()}"}, timeout=30, **kwargs,
        )
        response.raise_for_status()
        return response

    @staticmethod
    def _email(item: dict[str, Any]) -> Email:
        address = item.get("from", {}).get("emailAddress", {})
        recipients = tuple(
            recipient.get("emailAddress", {}).get("address", "").lower()
            for recipient in item.get("toRecipients", [])
            if recipient.get("emailAddress", {}).get("address")
        )
        body = item.get("body", {})
        content = body.get("content", "")
        if body.get("contentType") == "HTML":
            content = html.unescape(content).replace("<br>", "\n").replace("<br/>", "\n")
        return Email(
            id=item["id"], subject=item.get("subject") or "(no subject)",
            sender_name=address.get("name", "Unknown"), sender_email=address.get("address", "").lower(),
            received_at=item.get("receivedDateTime", ""), body_preview=item.get("bodyPreview", ""),
            body=content, to_recipients=recipients, categories=tuple(item.get("categories", [])),
            web_link=item.get("webLink", ""),
        )

    def _list_messages(self, folder: str, since: datetime) -> list[Email]:
        since_utc = since.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        params: dict[str, Any] = {
            "$select": "id,subject,from,toRecipients,receivedDateTime,bodyPreview,body,categories,webLink",
            "$filter": f"receivedDateTime ge {since_utc}",
            "$orderby": "receivedDateTime desc",
            "$top": "50",
        }
        messages: list[Email] = []
        path = f"/me/mailFolders/{folder}/messages"
        while path:
            response = self._request("GET", path, params=params)
            data = response.json()
            messages.extend(self._email(item) for item in data.get("value", []))
            next_link = data.get("@odata.nextLink")
            path = next_link.replace(GRAPH_ROOT, "") if next_link else ""
            params = {}
        return messages

    def list_recent_messages(self, since: datetime) -> list[Email]:
        return self._list_messages("inbox", since)

    def list_recent_sent_messages(self, since: datetime) -> list[Email]:
        return self._list_messages("sentitems", since)

    def list_user_folders(self) -> list[MailFolder]:
        """Return the complete visible mailbox folder tree without changing mail."""
        folders: list[MailFolder] = []
        seen: set[str] = set()

        def collect(path: str) -> None:
            response = self._request("GET", path, params={"$select": "id,displayName", "$top": "100"})
            for item in response.json().get("value", []):
                folder_id = item["id"]
                if folder_id in seen:
                    continue
                seen.add(folder_id)
                folders.append(MailFolder(folder_id, item.get("displayName", "Unnamed folder")))
                collect(f"/me/mailFolders/{quote(folder_id, safe='')}/childFolders")

        collect("/me/mailFolders")
        return folders

    def list_folder_messages(self, folder_id: str, limit: int) -> list[Email]:
        path = f"/me/mailFolders/{quote(folder_id, safe='')}/messages"
        response = self._request("GET", path, params={
            "$select": "id,subject,from,toRecipients,receivedDateTime,bodyPreview,body,categories,webLink",
            "$orderby": "receivedDateTime desc", "$top": str(limit),
        })
        return [self._email(item) for item in response.json().get("value", [])]

    def ensure_category(self, name: str, color: str = "preset0") -> None:
        response = self._request("GET", "/me/outlook/masterCategories")
        if any(category.get("displayName") == name for category in response.json().get("value", [])):
            return
        self._request("POST", "/me/outlook/masterCategories", json={"displayName": name, "color": color})

    def categorize(self, email: Email, category: str, flagged: bool = False) -> None:
        categories = list(dict.fromkeys((*email.categories, category)))
        update: dict[str, Any] = {"categories": categories}
        if flagged:
            update["flag"] = {"flagStatus": "flagged"}
        self._request("PATCH", f"/me/messages/{email.id}", json=update)

    def create_reply_draft(self, email_id: str, body: str) -> str:
        draft = self._request("POST", f"/me/messages/{email_id}/createReply").json()
        draft_id = draft["id"]
        self._request("PATCH", f"/me/messages/{draft_id}", json={"body": {"contentType": "Text", "content": body}})
        return draft_id

    def create_digest_draft(self, recipients: tuple[str, ...], subject: str, body: str) -> str:
        message = self._request("POST", "/me/messages", json={
            "subject": subject,
            "toRecipients": [{"emailAddress": {"address": address}} for address in recipients],
            "body": {"contentType": "Text", "content": body},
        }).json()
        return message["id"]
