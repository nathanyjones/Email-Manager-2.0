/* global Office */
"use strict";

let messageId = "";
let decision = null;
const $ = (id) => document.getElementById(id);

function setStatus(text) { $("status").textContent = text; }

async function request(path, options = {}) {
  const response = await fetch(path, {cache: "no-store", ...options});
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "The local Email Manager service did not respond.");
  return payload;
}

function renderFeedback(item) {
  const choices = item.has_draft
    ? [["draft_sent", "Sent it"], ["draft_edited", "Edited it"], ["unneeded", "Didn’t need it"]]
    : [["no_draft_correct", "Yes, correct"], ["manual_draft_requested", "No, I needed one"]];
  $("feedback-title").textContent = item.has_draft ? "Was this draft useful?" : "Was it correct not to draft?";
  $("feedback").replaceChildren(...choices.map(([value, label]) => {
    const button = document.createElement("button");
    button.type = "button"; button.textContent = label; button.className = "button";
    button.addEventListener("click", () => value === "unneeded" ? showUnneededChoices() : saveFeedback(value));
    return button;
  }));
}

function showUnneededChoices() {
  const target = $("feedback-followup"); target.hidden = false; target.replaceChildren();
  [["draft_not_needed_once", "Only this email"], ["never_draft_like_this", "Avoid similar"]].forEach(([value, label]) => {
    const button = document.createElement("button"); button.type = "button"; button.className = "button"; button.textContent = label;
    button.addEventListener("click", () => saveFeedback(value)); target.append(button);
  });
}

function renderDecision(item) {
  decision = item;
  $("empty").hidden = true; $("decision").hidden = false;
  $("category").textContent = `${item.category} · ${item.needs_action ? "Action requested" : "For awareness"}`;
  $("priority").textContent = `${item.priority || "medium"} priority · ${Math.round((item.confidence || 0) * 100)}% confidence`;
  $("subject").textContent = item.subject || "(no subject)";
  $("sender").textContent = item.sender_email || "Sender unavailable";
  $("summary").textContent = item.summary || "No summary was recorded.";
  const steps = item.action_items || []; $("next-steps").hidden = !steps.length && item.suggested_followup_time === "none";
  $("action-items").replaceChildren(...steps.map(step => { const li = document.createElement("li"); li.textContent = step; return li; }));
  $("followup").textContent = item.suggested_followup_time && item.suggested_followup_time !== "none" ? `Suggested timing: ${item.suggested_followup_time.replace("_", " ")}` : "";
  const draftReason = item.has_draft ? "A reply draft is ready for your review." : (item.draft_reason || "No reply draft was created.");
  $("reason").textContent = item.rationale ? `${draftReason} Why: ${item.rationale}` : draftReason;
  $("draft-actions").hidden = !item.draft_web_link;
  if (item.draft_web_link) $("draft-link").href = item.draft_web_link;
  renderFeedback(item);
  setStatus("Ready for this selected email");
}

async function saveFeedback(feedbackType) {
  try {
    await request("/api/feedback", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({message_id: messageId, feedback_type: feedbackType})});
    $("feedback-result").textContent = "Feedback saved locally. Future drafting changes only when your explicit feedback supports it.";
    $("feedback-result").hidden = false;
  } catch (error) { $("feedback-result").textContent = error.message; $("feedback-result").hidden = false; }
}

async function loadDecision() {
  const item = await request(`/api/message/${encodeURIComponent(messageId)}`);
  if (item.status !== "available") { $("empty").hidden = false; $("decision").hidden = true; $("empty-title").textContent = "Ready when you are"; $("empty-copy").textContent = "This email has no local decision. Analyze it only if you want the configured AI review; selecting it did nothing."; $("analyze").hidden = false; setStatus("No local decision — no analysis was run"); return; }
  $("analyze").hidden = true;
  renderDecision(item);
}

async function selectedMessageId() {
  const item = Office.context.mailbox.item;
  if (!item || !item.itemId) throw new Error("Open or select an email first.");
  try { return Office.context.mailbox.convertToRestId(item.itemId, Office.MailboxEnums.RestVersion.v2_0); }
  catch (_) { return item.itemId; }
}

Office.onReady(async (info) => {
  if (info.host !== Office.HostType.Outlook) { setStatus("This panel must be opened from Outlook."); return; }
  try { messageId = await selectedMessageId(); await loadDecision(); }
  catch (error) { setStatus(error.message); }
  Office.context.mailbox.addHandlerAsync(Office.EventType.ItemChanged, async () => {
    try { messageId = await selectedMessageId(); $("answer").hidden = true; $("feedback-result").hidden = true; await loadDecision(); }
    catch (error) { setStatus(error.message); }
  });
});

$("analyze").addEventListener("click", async () => {
  if (!messageId) return; const button = $("analyze"); button.disabled = true; button.textContent = "Analyzing…";
  try { renderDecision(await request("/api/analyze", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({message_id: messageId})})); }
  catch (error) { setStatus(error.message); } finally { button.disabled = false; button.textContent = "Analyze this email"; }
});

$("question-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = $("question").value.trim(); if (!question || !messageId) return;
  const button = event.currentTarget.querySelector("button"); button.disabled = true; button.textContent = "Thinking…";
  try {
    const result = await request("/api/question", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({message_id: messageId, question})});
    $("answer").textContent = result.answer; $("answer").hidden = false;
  } catch (error) { $("answer").textContent = error.message; $("answer").hidden = false; }
  finally { button.disabled = false; button.textContent = "Ask →"; }
});

document.querySelectorAll("[data-question]").forEach(button => button.addEventListener("click", () => { $("question").value = button.dataset.question; $("question-form").requestSubmit(); }));
