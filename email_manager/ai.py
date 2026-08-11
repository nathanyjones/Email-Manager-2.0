"""LLM-backed email assessment with strict structured output."""

from __future__ import annotations

import json
from openai import OpenAI

from .models import Assessment, ContactProfile, Email


SYSTEM_PROMPT = """You are a careful executive email assistant. Return only a JSON object.
Classify the message as action, informational, marketing, or spam. Never draft a reply to marketing or spam.
Set needs_response only when the mailbox owner should personally reply. Set needs_action for any concrete follow-up,
including a response. Drafts must be concise, professional, and contain no invented commitments, dates, prices, facts,
or confidential information. Return only the reply body: do not include a greeting, sign-off, or signature. If context is
insufficient, set draft_reply to null and explain why in rationale.
Schema: {"needs_response":boolean,"needs_action":boolean,"priority":"high|medium|low","category":"action|informational|marketing|spam","summary":"string","action_items":["string"],"draft_reply":"string or null","suggested_followup_time":"today|this_week|later|none","confidence":number,"rationale":"string"}"""


class EmailAssistant:
    def __init__(self, api_key: str, model: str) -> None:
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def assess(self, email: Email, profile: ContactProfile, draft_tone: str = "concise, professional, and helpful") -> Assessment:
        profile_text = (
            f"Relationship: {profile.relationship_notes or 'Unknown'}\n"
            f"Writing style: {profile.style_notes or 'Use concise professional tone'}\n"
            f"Recurring topics: {', '.join(profile.recurring_topics) or 'None'}\n"
            f"Response preferences: {profile.response_preferences or 'None'}\n"
            f"Contact style notes: {profile.style_notes or 'None'}\n"
            f"Requested draft tone: {draft_tone}"
        )
        content = (
            f"Contact profile:\n{profile_text}\n\n"
            f"Email ID: {email.id}\nFrom: {email.sender_name} <{email.sender_email}>\n"
            f"Received: {email.received_at}\nSubject: {email.subject}\n\nBody:\n{email.body[:12000]}"
        )
        response = self.client.chat.completions.create(
            model=self.model, temperature=0.1, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}],
        )
        raw = response.choices[0].message.content or "{}"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError("Model returned invalid JSON") from error
        category = data.get("category", "informational")
        if category not in {"action", "informational", "marketing", "spam"}:
            category = "informational"
        draft = data.get("draft_reply") if data.get("needs_response") and category not in {"marketing", "spam"} else None
        return Assessment(
            needs_response=bool(data.get("needs_response")), needs_action=bool(data.get("needs_action")),
            priority=data.get("priority") if data.get("priority") in {"high", "medium", "low"} else "medium",
            category=category, summary=str(data.get("summary", ""))[:1000],
            action_items=tuple(str(item)[:500] for item in data.get("action_items", [])[:10]),
            draft_reply=str(draft)[:6000] if draft else None,
            suggested_followup_time=data.get("suggested_followup_time") if data.get("suggested_followup_time") in {"today", "this_week", "later", "none"} else "none",
            confidence=max(0.0, min(1.0, float(data.get("confidence", 0.0)))), rationale=str(data.get("rationale", ""))[:1000],
        )
