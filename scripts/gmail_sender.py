from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from typing import Iterable


TOKEN_URL = "https://oauth2.googleapis.com/token"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


class GmailSendError(RuntimeError):
    pass


def _http_json(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None) -> dict:
    request = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise GmailSendError(f"HTTP {exc.code} for {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise GmailSendError(f"Network error for {url}: {exc}") from exc


def fetch_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    payload = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    result = _http_json(
        TOKEN_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    access_token = result.get("access_token")
    if not access_token:
        raise GmailSendError(f"Missing access_token in Gmail OAuth response: {result}")
    return access_token


def build_message(sender: str, recipients: Iterable[str], subject: str, body_text: str) -> str:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(body_text)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    return raw


def send_message(
    *,
    sender: str,
    recipients: list[str],
    subject: str,
    body_text: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> dict:
    access_token = fetch_access_token(client_id, client_secret, refresh_token)
    raw = build_message(sender, recipients, subject, body_text)
    payload = json.dumps({"raw": raw}).encode("utf-8")
    return _http_json(
        SEND_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
    )
