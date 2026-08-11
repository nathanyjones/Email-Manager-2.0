"""Configuration loaded from environment variables."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


def _csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _newlines(value: str) -> str:
    return value.replace("\\n", "\n").strip()


@dataclass(frozen=True)
class Settings:
    client_id: str
    openai_api_key: str
    model: str
    user_email: str
    database_path: Path
    token_cache_path: Path
    schedules: tuple[str, ...]
    lookback_hours: int
    digest_mode: str
    digest_recipients: tuple[str, ...]
    excluded_senders: tuple[str, ...]
    excluded_folders: tuple[str, ...]
    max_history_messages: int
    draft_tone: str
    draft_greeting: str
    draft_closing: str
    draft_signature: str
    no_reply_senders: tuple[str, ...]
    no_reply_domains: tuple[str, ...]
    folder_suggestions_enabled: bool
    folder_history_per_folder: int
    folder_min_examples: int
    folder_min_confidence: float

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        client_id = os.getenv("CLIENT_ID", "")
        openai_api_key = os.getenv("OPENAI_API_KEY", os.getenv("CHATGPT_API_KEY", ""))
        user_email = os.getenv("USER_EMAIL", "")
        missing = [name for name, value in {
            "CLIENT_ID": client_id,
            "OPENAI_API_KEY": openai_api_key,
            "USER_EMAIL": user_email,
        }.items() if not value]
        if missing:
            raise ValueError("Missing required environment variables: " + ", ".join(missing))

        digest_mode = os.getenv("DIGEST_MODE", "draft").lower()
        if digest_mode not in {"draft", "disabled"}:
            raise ValueError("DIGEST_MODE must be draft or disabled; this pilot never sends mail")

        return cls(
            client_id=client_id,
            openai_api_key=openai_api_key,
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            user_email=user_email.lower(),
            database_path=Path(os.getenv("DATABASE_PATH", "email-manager.db")),
            token_cache_path=Path(os.getenv("TOKEN_CACHE_PATH", "token_cache.bin")),
            schedules=_csv(os.getenv("SCHEDULES", "06:00,12:00,15:00")),
            lookback_hours=int(os.getenv("LOOKBACK_HOURS", "24")),
            digest_mode=digest_mode,
            digest_recipients=_csv(os.getenv("DIGEST_RECIPIENTS", user_email)),
            excluded_senders=tuple(value.lower() for value in _csv(os.getenv("EXCLUDED_SENDERS", ""))),
            excluded_folders=_csv(os.getenv("EXCLUDED_FOLDERS", "")),
            max_history_messages=int(os.getenv("MAX_HISTORY_MESSAGES", "40")),
            draft_tone=os.getenv("DRAFT_TONE", "concise, professional, and helpful").strip(),
            draft_greeting=os.getenv("DRAFT_GREETING", "Hi").strip(),
            draft_closing=os.getenv("DRAFT_CLOSING", "Best,").strip(),
            draft_signature=_newlines(os.getenv("DRAFT_SIGNATURE", "")),
            no_reply_senders=tuple(value.lower() for value in _csv(os.getenv("NO_REPLY_SENDERS", ""))),
            no_reply_domains=tuple(value.lower().lstrip("@") for value in _csv(os.getenv("NO_REPLY_DOMAINS", ""))),
            folder_suggestions_enabled=os.getenv("FOLDER_SUGGESTIONS_ENABLED", "true").lower() in {"1", "true", "yes"},
            folder_history_per_folder=int(os.getenv("FOLDER_HISTORY_PER_FOLDER", "50")),
            folder_min_examples=int(os.getenv("FOLDER_MIN_EXAMPLES", "2")),
            folder_min_confidence=float(os.getenv("FOLDER_MIN_CONFIDENCE", "0.80")),
        )
