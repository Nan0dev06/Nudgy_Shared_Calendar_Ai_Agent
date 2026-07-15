/**
 * Orbi — Google Calendar Add-on.
 *
 * This is a thin client: ALL reasoning, tool calls, and DB access happen in
 * the FastAPI backend (backend/app/api/addon_routes.py). This file only
 * builds the CardService sidebar UI and forwards messages to /addon/chat.
 *
 * First run: since Session.getEffectiveUser() is unreliable for personal
 * Gmail accounts inside Workspace Add-ons, we ask the user once to confirm
 * the email they connected in the Orbi web app, and remember it per-user
 * (UserProperties — scoped to this Google account automatically, no extra
 * OAuth scope needed).
 *
 * Conversation history is kept in UserCache (per-user, ~server-side, up to
 * 6h TTL) so a sidebar reload doesn't lose context, but it's fine if it
 * expires — it's a convenience, not a source of truth.
 */

var BACKEND_URL = 'https://orbi-1jgh.onrender.com'; // no trailing slash
var EMAIL_PROP_KEY = 'orbi_email';
var HISTORY_CACHE_KEY = 'orbi_history';
var HISTORY_TTL_SECONDS = 6 * 60 * 60;

function onHomepage(e) {
  var email = PropertiesService.getUserProperties().getProperty(EMAIL_PROP_KEY);
  return email ? buildChatCard(getHistory_(), '') : buildEmailCard('');
}

// ---------------------------------------------------------------- email setup

function buildEmailCard(error) {
  var section = CardService.newCardSection();
  if (error) {
    section.addWidget(CardService.newTextParagraph().setText('<font color="#c0392b">' + error + '</font>'));
  }
  section.addWidget(CardService.newTextParagraph().setText(
    'Enter the email you connected to Orbi in the web app.'));
  section.addWidget(CardService.newTextInput()
    .setFieldName('email')
    .setTitle('Your Orbi email'));
  section.addWidget(CardService.newTextButton()
    .setText('Connect')
    .setOnClickAction(CardService.newAction().setFunctionName('saveEmail')));
  return CardService.newCardBuilder().addSection(section).build();
}

function saveEmail(e) {
  var email = (e.formInput && e.formInput.email || '').trim();
  if (!email || email.indexOf('@') === -1) {
    return navigateTo_(buildEmailCard('That doesn\'t look like an email — try again.'));
  }
  PropertiesService.getUserProperties().setProperty(EMAIL_PROP_KEY, email);
  clearHistory_();
  return navigateTo_(buildChatCard([], "Connected as " + email + ". Ask me to find a time for your group."));
}

function resetEmail(e) {
  PropertiesService.getUserProperties().deleteProperty(EMAIL_PROP_KEY);
  clearHistory_();
  return navigateTo_(buildEmailCard(''));
}

// ---------------------------------------------------------------- chat

function buildChatCard(history, banner) {
  var section = CardService.newCardSection();
  if (banner) {
    section.addWidget(CardService.newTextParagraph().setText(banner));
  }
  // show the last few turns so the sidebar reads like a conversation
  var recent = history.slice(-6);
  for (var i = 0; i < recent.length; i++) {
    var turn = recent[i];
    var label = turn.role === 'user' ? '<b>You:</b> ' : '<b>Orbi:</b> ';
    section.addWidget(CardService.newTextParagraph().setText(label + escapeHtml_(turn.content)));
  }
  section.addWidget(CardService.newTextInput()
    .setFieldName('message')
    .setTitle('Ask Orbi'));
  section.addWidget(CardService.newTextButton()
    .setText('Send')
    .setOnClickAction(CardService.newAction().setFunctionName('handleMessage')));
  section.addWidget(CardService.newTextButton()
    .setText('Not you? Reset email')
    .setOnClickAction(CardService.newAction().setFunctionName('resetEmail')));

  return CardService.newCardBuilder().addSection(section).build();
}

function handleMessage(e) {
  var email = PropertiesService.getUserProperties().getProperty(EMAIL_PROP_KEY);
  var message = (e.formInput && e.formInput.message || '').trim();
  if (!email) return navigateTo_(buildEmailCard(''));
  if (!message) return navigateTo_(buildChatCard(getHistory_(), ''));

  var history = getHistory_();
  var reply;
  try {
    reply = callOrbi_(email, message, history);
  } catch (err) {
    return navigateTo_(buildChatCard(history,
      '<font color="#c0392b">Orbi is unreachable right now (' + err.message + ').</font>'));
  }

  history.push({ role: 'user', content: message });
  history.push({ role: 'assistant', content: reply });
  saveHistory_(history);

  return navigateTo_(buildChatCard(history, ''));
}

// ---------------------------------------------------------------- backend call

function callOrbi_(email, message, history) {
  var secret = PropertiesService.getScriptProperties().getProperty('ADDON_SHARED_SECRET');
  var response = UrlFetchApp.fetch(BACKEND_URL + '/addon/chat', {
    method: 'post',
    contentType: 'application/json',
    headers: { 'X-Orbi-Addon-Secret': secret || '' },
    payload: JSON.stringify({ email: email, message: message, history: history }),
    muteHttpExceptions: true,
  });
  var code = response.getResponseCode();
  var body = JSON.parse(response.getContentText() || '{}');
  if (code >= 400) {
    throw new Error(body.detail || ('HTTP ' + code));
  }
  return body.reply || '(no reply)';
}

// ---------------------------------------------------------------- helpers

function getHistory_() {
  var raw = CacheService.getUserCache().get(HISTORY_CACHE_KEY);
  return raw ? JSON.parse(raw) : [];
}

function saveHistory_(history) {
  // keep the cache small; the backend only needs recent turns anyway
  var trimmed = history.slice(-20);
  CacheService.getUserCache().put(HISTORY_CACHE_KEY, JSON.stringify(trimmed), HISTORY_TTL_SECONDS);
}

function clearHistory_() {
  CacheService.getUserCache().remove(HISTORY_CACHE_KEY);
}

function navigateTo_(card) {
  return CardService.newActionResponseBuilder()
    .setNavigation(CardService.newNavigation().updateCard(card))
    .build();
}

function escapeHtml_(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}
