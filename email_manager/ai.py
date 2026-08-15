"""LLM-backed email assessment with strict structured output."""

from __future__ import annotations

import json
from openai import OpenAI

from .models import Assessment, ContactProfile, Email, FolderProfile, FolderSuggestion


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

    def answer_question(self, email: Email, question: str, local_summary: str = "") -> str:
        """Answer one explicit question about one selected message; never takes an email action."""
        prompt = """You are a careful email assistant answering a user's question about one selected email.
Use only the supplied email and local decision summary. Be concise and practical. Do not claim actions were taken,
do not invent facts, commitments, dates, or people, and do not produce or send a reply draft. If the email does not
provide enough information, say what needs confirming. Return plain text, no markdown heading."""
        content = (
            f"Local decision summary: {local_summary or 'No prior local decision is available.'}\n\n"
            f"Email:\nFrom: {email.sender_name} <{email.sender_email}>\nSubject: {email.subject}\n"
            f"Received: {email.received_at}\nBody:\n{email.body[:12000]}\n\nQuestion: {question[:1000]}"
        )
        response = self.client.chat.completions.create(
            model=self.model, temperature=0.1,
            messages=[{"role": "system", "content": prompt}, {"role": "user", "content": content}],
        )
        return (response.choices[0].message.content or "I couldn't produce an answer for this email.").strip()[:4000]

    def build_folder_profile(self, folder_name: str, folder_id: str, emails: list[Email]) -> FolderProfile:
        """Summarize a bounded set of filed messages into a compact local folder profile."""
        samples = "\n\n".join(
            f"From: {email.sender_email}\nTo: {', '.join(email.to_recipients)}\nCC: {', '.join(email.cc_recipients)}\n"
            f"Subject: {email.subject}\nPreview: {email.body_preview[:800]}"
            for email in emails
        )
        prompt = """You describe the practical purpose of an email folder from its sample messages. Return only JSON.
Never include sensitive details, names, addresses, or verbatim email text in the result. Generalize participants by role,
organization type, or domain pattern where possible.
Schema: {"purpose":"one concise sentence","topics":["short topic"],"participant_signals":["general signal"]}"""
        response = self.client.chat.completions.create(
            model=self.model, temperature=0, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": prompt}, {"role": "user", "content": f"Folder: {folder_name}\nSamples:\n{samples}"}],
        )
        try:
            data = json.loads(response.choices[0].message.content or "{}")
        except json.JSONDecodeError as error:
            raise ValueError(f"Model returned invalid folder profile for {folder_name}") from error
        return FolderProfile(
            folder_id=folder_id, folder_name=folder_name, purpose=str(data.get("purpose", "General correspondence."))[:500],
            topics=tuple(str(item)[:120] for item in data.get("topics", [])[:8]),
            participant_signals=tuple(str(item)[:160] for item in data.get("participant_signals", [])[:8]),
            examples_seen=len(emails),
        )

    def suggest_semantic_folder(self, email: Email, folder_profiles: list[FolderProfile]) -> FolderSuggestion | None:
        if not folder_profiles:
            return None
        profiles = "\n".join(
            f"ID: {profile.folder_id}\nName: {profile.folder_name}\nPurpose: {profile.purpose}\n"
            f"Topics: {', '.join(profile.topics)}\nParticipant signals: {', '.join(profile.participant_signals)}\n"
            for profile in folder_profiles
        )
        prompt = """Choose the best existing folder for a message. Use the message subject/body as the primary signal;
sender, To, and CC are secondary signals. Return only JSON. If no folder clearly fits, use null.
Schema: {"folder_id":"an offered ID or null","confidence":0.0,"rationale":"brief reason"}"""
        message = (
            f"Folders:\n{profiles}\nMessage:\nFrom: {email.sender_email}\nTo: {', '.join(email.to_recipients)}\n"
            f"CC: {', '.join(email.cc_recipients)}\nSubject: {email.subject}\nBody: {email.body[:5000]}"
        )
        response = self.client.chat.completions.create(
            model=self.model, temperature=0, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": prompt}, {"role": "user", "content": message}],
        )
        try:
            data = json.loads(response.choices[0].message.content or "{}")
        except json.JSONDecodeError as error:
            raise ValueError("Model returned invalid semantic folder suggestion") from error
        profile = {profile.folder_id: profile for profile in folder_profiles}.get(data.get("folder_id"))
        if not profile:
            return None
        try:
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0))))
        except (TypeError, ValueError):
            return None
        return FolderSuggestion(profile.folder_id, profile.folder_name, profile.examples_seen, confidence, "semantic")
