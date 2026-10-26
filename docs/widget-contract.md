# Widget contract: agent widget tools <-> mock-site chat cards

Audience: the agent-side author (tool JSON under `cxas_app/WGL_Agent/tools/`) and the front-end author (`assets/js/assistant.js`).
Both sides must follow this file. Anything marked **UNVERIFIED LIVE** could only be checked by reading the SDK source, not by running the real widget (it needs an allowed origin).

## 0. Verified live (Rev 17): how a CUSTOM widget tool reaches the page

Checked against the live WGL app with `Sessions.run` (raw `RunSessionResponse`, `outputs[]` and `diagnostic_info.messages[]`).

1. **Tool call args.** A widget tool (`widgetType: CUSTOM`) must be called with `args.payload` (the platform says "LLM failed to provide parameter `payload`" otherwise), plus `summary` and, when `textResponseConfig` is LLM_GENERATED, `textResponse`. Everything the tool schema declares lives inside `args.payload`. Flat args (`{prompt, options}`) fail and the model has to retry.
2. **Tool response (to the model).** `{"payload": <what was passed>, "summary": "...", "textResponse": "...", "widget_tool_status": "success"}`. The `textResponse` is also emitted to the customer as a text output, so a card tool must not carry a text that repeats the greeting or the answer.
3. **What the client receives.** One extra output in the turn with `payload` = `{"type": "custom_template", **args.payload}`: the declared fields are spread next to `type`. Nothing else names the tool: there is no tool call in `outputs[]`, and the element name is NOT derived from the tool name or `uiConfig`.
4. **What the SDK does with it.** `chat-messenger.js` v1.16 maps a `custom_template` rich element with `createElement(element.name)` and `dfPayload = element.payload` (SDK source, `case "custom_template"`). So the platform payload only renders if the tool's own `payload` argument contains `name` (a defined custom element) and `payload` (the card data).
5. **Resulting contract (what the tool JSONs now declare).** Every card tool has exactly two parameters: `name` (STRING, enum with one value `cx-<tool-name-with-dashes>`, e.g. `cx-show-bill-trend-card`) and `payload` (OBJECT, the card fields documented below). Call: `{"payload": {"name": "cx-show-bill-trend-card", "payload": {...card fields...}}, "summary": "..."}`. The client then gets `{"type": "custom_template", "name": "cx-show-bill-trend-card", "payload": {...card fields...}}`, which the SDK renders natively with no page adapter.
6. **Page side.** `assistant.js` defines one custom element per card tool, `cx-<tool-name-with-dashes>` (same renderer; the tool comes from the tag name; `dfPayload` is the card fields). `wgl-card` with `dfPayload = {tool, args}` stays for the preview page and `WGLCards.create`. The older toolCalls / registerClientSideFunction path stays as a harmless fallback; CES does not use it for CUSTOM tools.
7. **Callbacks.** The before_model callbacks build widget calls with `_widget_call(tool, card, summary)` (opening chips, emergency card). Quick actions and the emergency card use `textResponseConfig.type = NONE`, so a callback call needs no text.

## 0b. Rev 18: the website renders the welcome (no welcome event)

* `enableWelcomeEvent` is **false**. When the chat opens (`chat-messenger-open-changed` with `isOpen`, or a widget already open on load, polled), `assistant.js` renders the page greeting with `renderCustomText(text, true)` and then a chips card, the custom element `cx-show-welcome-chips` (payload `{page, lang, initial}`), through `renderCustomCard([{type: "custom_template", name: "cx-show-welcome-chips", payload}])`. No agent call is made.
* Texts: ONE data structure `WELCOME` in `assistant.js` (13 pages, `en` and `es`, greeting and chips; the `notfound` page exists on the site only). `evals/offline_tests/test_site_welcome.py` keeps it equal to the `greeting_page_*` seed strings and the `_PAGE_CHIPS` lists in the before_model callback.
* Once per browser session: sessionStorage `wgl-welcome-shown`. It is cleared on session end or expiry and after "Start new chat" (the reset button click opens an 8 s window in which `chat-messenger-start-new-session` triggers a fresh welcome).
* Language: sessionStorage `wgl-ui-language` `{l, p}`; the Spanish page defaults to `es`, every other page to `en`. The toggle re-renders the card and, because the greeting bubble belongs to the SDK, repeats the new-language greeting inside the card.
* **Variables** (sessionStorage `chat-messenger-variables`, merged): `{page_context: <page>, ui_language: "en" | "es"}`, written at welcome time, on every toggle and again on a chip tap, before the chip text goes out through `presenter.sendQuery` (same route as the card buttons). The first agent turn is therefore the chip text or the typed message.
* The card locks (chips and toggle disabled, chosen chip highlighted) on a chip tap or on any `chat-messenger-user-input-entered`.
* Accessibility: toggle is a `role=group` of `aria-pressed` buttons, the translated greeting sits in an `aria-live=polite` region, chips are native buttons with the gold focus ring, animations stop under `prefers-reduced-motion`.
* `show_bill_trend_card` accepts optional `action_label` and `action_message`: rendered as one chip under the chart (used for "See weather vs usage", so only one card is delivered per reply).
* Rev 25 (additive, restructure not remove). `show_bill_trend_card` also accepts optional `reasons` (up to 6 objects `{label, amount, direction: up|down|flat, icon?}`; the icon is a name from the card icon set, never an emoji) drawn as a list under the highlight note, each with a signed amount, and optional `extra_actions` (up to 2 more chips `{label, message}` beside the first button, used for the Budget Plan offer). `show_weather_usage_card` accepts optional `stats` (up to 3 `{label, value}` facts drawn as a row under the legend: heating degree days against last month, gas used this month, the peak month). Both come from tool data: `run_high_bill_diagnosis` returns `reasons` and `reasons_total_note`, `get_usage_history` returns `weather_stats`. When the new fields are absent the card HTML and pixels are identical to before (screenshot diff of `widget-preview.html` at 420 and 375 px, zero changed pixels over every earlier card).

Sections 1 to 3 below were written before this check; where they say `wgl-card` receives `{tool, args}` from the platform, read section 0 instead. Card field shapes in section 3 are unchanged: they are the contents of the inner `payload`.

---

## 1. SDK research (chat-messenger.js v1.16, build `chat-messenger_chat-messenger_20260429_01_RC00`)

Sources read: the SDK bundle itself (`https://www.gstatic.com/chat-messenger/sdk/prod/v1.16/chat-messenger.js`, 1.3 MB, minified but with stable public names), `themes/chat-messenger-default.css`, and the Google docs pages "web widget" and "Rich response widgets" (the docs are thin: they name `renderCustomText`, `renderCustomCard`, the three events below and `enableWelcomeEvent`, and nothing about CUSTOM widgets).

### 1a. How custom widget responses reach the page and how the page renders its own HTML

What the SDK actually contains (verified in the bundle):

* The SDK has **no code that knows the strings `widgetType`, `CUSTOM`, `ORDER_SUMMARY` or `QUICK_ACTIONS`**. Widget typing is a server/console concept. The client only sees these shapes in a CES response (`outputs[]`):
  * `text`
  * `payload` (a Struct): if it has `richContent` it is rendered as Dialogflow-Messenger rich content; otherwise it is treated as an array of rich elements `{type: ...}`.
  * `toolCalls.toolCalls[]` with `{id, tool, args}`: these are **client-side function calls**. The SDK takes the last path segment of `tool` as the tool name and runs a registered JavaScript function; the function's return value is sent back to the agent as the tool result (`outputParameters`). If nothing is registered the SDK logs "Call to unknown ..." and sends an error result back to the agent.
* Rich element type **`custom_template`** is the official hook for custom HTML: `{type: "custom_template", name: "<custom-element-tag>", payload: {...}}`. The SDK does `document.createElement(name)`, sets `element.dfPayload = payload` and `element.dfResponseId`, and mounts it **inside the message list** (inside a `df-custom-template` element, inside the SDK's shadow DOM). The element must already be defined with `customElements.define`. If it is not defined the SDK logs "Unknown custom element" and shows nothing.
* Public methods on `<chat-messenger>`: `renderCustomText(text, isBot = true)` and `renderCustomCard(richElementsArray)` (runs the array through the same rich-element mapper, so a `custom_template` element works). Also `setVariables(obj)`, `registerClientSideFunction(tool, action, fn)`, and `presenter.registerClientSideFunctionForAllTools(actionName, fn)` (the SDK itself uses this for the built-in action name `df_render_custom_card`, whose `payloads` argument is passed to `renderCustomCard`).

Design chosen (what `assistant.js` does):

1. Defines the custom element **`wgl-card`** (shadow DOM, own CSS, no external libs) at script load, before the SDK starts.
2. When `<chat-messenger>` exists it registers, for every card tool name in section 3, a client-side function via `chatMessenger.presenter.registerClientSideFunctionForAllTools(<tool_name>, fn)`. `fn(args)` calls `chatMessenger.renderCustomCard([{type: "custom_template", name: "wgl-card", payload: {tool: <tool_name>, args: args}}])` and returns `{rendered: true}`.
3. Fallback: it also listens to the `chat-messenger-response-received` event (`detail.raw.outputs[]`) and renders any `toolCalls` whose tool name is in the card list that the registered function did not already handle (de-duplicated by tool + args within 10 s).
4. If the server delivers a card as a native rich element, the agent side can emit exactly `{"type": "custom_template", "name": "wgl-card", "payload": {"tool": "show_bill_trend_card", "args": {...}}}` in a `payload` and it renders with no extra client code.

**Resolved in section 0 (was UNVERIFIED LIVE):** whether CES delivers widget tool (CUSTOM) results to the client as (a) `toolCalls` (client-side functions), (b) a `payload` rich element, or (c) something else. The SDK handles (a) and (b) as described; if the real traffic shows a different shape, only the small adapter in `assistant.js` (`registerTools` and `onResponse`) needs to change. Check with the browser Network tab on the first live run (look at the `outputs` of the response) and with `loggingLevel`/console.

What the SDK does **not** allow:

* No documented render hook for `widgetType: CUSTOM` and no way to replace the built-in renderers for `ORDER_SUMMARY`, `QUICK_ACTIONS`, `APPOINTMENT_*`, `OVERALL_SATISFACTION`, `CONTACT_FORM` (they are server-typed; the client may render them natively, we cannot restyle their markup, only theme variables).
* No page-level CSS reaches into the chat (it lives in nested shadow roots); only `--chat-messenger-*` CSS variables inherit. That is why `wgl-card` carries its own stylesheet and reads the brand colours directly.
* The message-list typing indicator, avatar ring and the open/close animation are internal to the SDK (only `bot-writing-image`, `bot-writing-text`, `bot-actor-image` attributes and the theme variables are configurable).
* No documented public API to send a user message from outside. The working route is the one the SDK's own chips use internally: `renderCustomText(text, false)` (shows the customer bubble) + `presenter.sendQuery(text)` (sends it), then dispatch `chat-messenger-user-input-entered`. `assistant.js` uses exactly that. `presenter` is an undocumented-but-unminified property; if it is missing the card falls back to a console warning.

### 1b. Session variables (page context)

* Storage key is `"chat-messenger-" + "variables"` = **`chat-messenger-variables`**, in `window.sessionStorage` (verified: `getVariables` / `storeVariables`; the storage class is used unless the element has `storage-option="none"`).
* It is read with `JSON.parse` **on every request** (text and welcome event: the body is `inputs: [{text|event}, {variables}]`), not only at init. So writing it before the widget opens works, and changing it on the next page is picked up on the next message.
* The SDK wipes `variables` (with the other `chat-messenger-*` keys except the access token) when the session ends or expires, and on "new chat". `assistant.js` therefore writes `page_context` synchronously at script load **and again** on `chat-messenger-loaded`, `chat-messenger-context-registered`, `chat-messenger-session-id-set`, `chat-messenger-start-new-session`, `chat-messenger-open-changed`, and also calls `chatMessenger.setVariables()` when the element exists. Existing keys in the stored object are preserved (merge, not overwrite).
* Server response `updatedVariables` can also be stored by the SDK; merging keeps them.

### 1c. Events (all `CustomEvent`, `bubbles: true, composed: true`, so `window.addEventListener` works)

| Event | detail | Use |
| --- | --- | --- |
| `chat-messenger-loaded` | none | register context / tools |
| `chat-messenger-context-registered` | none | `registerContext` done |
| `chat-messenger-open-changed` | `{isOpen, isCentered, isSideAnchored}` | chat window opened or closed (bubble) |
| `chat-messenger-close` | none | closed |
| `chat-messenger-request-sent` | request body | outgoing message |
| `chat-messenger-response-received` | `{messages, data, raw}` | incoming response (`raw.outputs[]`) |
| `chat-messenger-user-input-entered` | none | a user message was submitted (typed or chip) |
| `chat-messenger-chip-clicked` | `{text, actionLink?}` | native chip |
| `chat-messenger-button-clicked` | `{text, event, actionLink}` | native button element |
| `chat-messenger-session-id-set`, `-session-ended`, `-session-expired`, `-start-new-session` | | session lifecycle |
| `chat-messenger-error` | `{error}` | error |
| `df-quick-actions-click` | `{action}` | native quick actions |

Custom cards additionally fire `wgl-send` (detail `{text, tool}`, bubbles, composed, cancelable) just before they send; a host page (the preview page) can `preventDefault()` it to intercept.

### 1d. Theme variables

Read from `chat-messenger-default.css`: public tokens `--chat-messenger-color--{primary, on-primary, primary-container, on-primary-container, secondary, on-secondary, surface, surface-container(-low/-high), on-surface, on-surface-variant, outline(-active/-variant), error, link, state-layer-*}`, `--chat-messenger-shape--corner-value-{small,medium,large,extra-large}`, `--chat-messenger-spacing--*`, `--chat-messenger-typescale--*`, `--chat-messenger-font-family`, `--chat-messenger-elevation`; plus undocumented `--chat-messenger-internal-*` (titlebar, message, input, chips, chat-bubble, card, focus). `titlebar-background`, `message-user-background` and `chat-bubble-background` are used with the `background:` shorthand, so gradients work. Attributes on `<chat-messenger-chat-bubble>` used: `chat-subtitle` (the "Online" line), `bot-actor-image`, `bot-writing-text`, `placeholder-text`.

### 1e. Welcome event

`createContext({ ..., enableWelcomeEvent: true })` exists (default false). With the token broker it fires once per session after the access token resolves (`triggerWelcomeIntent`), sending the event `welcome` plus the stored variables, so `page_context` is present on the very first turn. The agent must handle the `welcome` event (greeting that uses `page_context`). Enabled in `widget-embed.html`.

## 2. page_context

`<body data-page-context="...">` on every page; `assistant.js` copies it to `window.wglPageContext` (index page is `home`) and writes sessionStorage `chat-messenger-variables` = `{"page_context": "<value>", ...existing}`.

Values: `home`, `billing`, `service`, `safety`, `assistance`, `savings`, `business`, `choice`, `claims`, `energy`, `spanish`, `contact`, `notfound`.

Agent side: read it as the session variable `page_context` (declare it in the app variables with a default of `home`). Suggested use: pick the greeting and the first suggested chips (billing -> pay or explain my bill; savings -> rebate match; claims -> claim status; safety -> emergency first). `spanish` means the visitor is on the Spanish page: greet in Spanish.

## 3. Card payloads

Rules for every card:

* Tool name = the key below; `widgetType` should be **`CUSTOM`** for all of them (including `show_quick_actions`, `show_slot_picker`, `show_bill_summary_card`, `show_appointment_confirmation`, `show_csat_card`). If a tool keeps a built-in type (`QUICK_ACTIONS`, `ORDER_SUMMARY`, `APPOINTMENT_SCHEDULER`, ...), the server/SDK may render it natively and our `wgl-card` will not be used; the renderer still exists for each of these names.
* Only real numbers from tool results; nothing invented. Money is a plain number in dollars (not a string), months are oldest first, `period` is a short label such as `"Oct 2025"` or `"Oct"` (the card shows the first three letters).
* Unknown or missing optional fields are tolerated (card hides the row). Missing required arrays render an "unavailable" note.
* **Every actionable button sends `action_message` back as the customer's next message** (it is shown as a customer bubble, then sent to the agent). Keep it a natural sentence the agent can route, for example `"Sign me up for budget billing"`.
* Agent text still carries the same facts (voice parity); cards are enhancement only.

### Existing (unchanged shapes)

```json
show_usage_history_card   {"unit": "therms", "months": [{"period": "Oct 2025", "usage": 31}]}
show_bill_summary_card    {"title": "Payment received", "amount": 142.55, "convenience_fee": 0, "due_date": "Nov 12, 2026",
                           "confirmation_number": "WG-123456", "card_last4": "4242"}      // required: title, amount
show_quick_actions        {"prompt": "How would you like to verify?", "options": ["Text me a code", "Use my PIN", "Other"]}  // up to 3
show_slot_picker          {"service_type": "start", "slots": [{"slot": "S1", "date": "Tue Nov 3", "window": "8am-12pm"}]}   // up to 3
show_appointment_confirmation {"service_type": "start", "date": "Tue Nov 3", "window": "8am-12pm", "address": "12 Oak St",
                           "confirmation_numbers": ["AP-1001"]}
show_emergency_card       {"safety_text": "...", "emergency_number": "800-752-7520"}
show_csat_card            {"prompt": "How did we do?"}
```

Messages the existing cards send: quick-action chip -> the option label; slot -> `"I'll take <date>, <window> (slot <slot>)"` unless the slot has its own `action_message`; CSAT star -> `"I rate this experience N out of 5"`.
show_callback_form        {"reason": "after_hours", "prompt": "What number should we call you back on?"}   // reason: after_hours or degraded

The callback form validates a 10-digit US number in the card and sends `"Please call me back at 555-123-4567"` through the chat; the agent then calls `create_callback_ticket` with that number.

### New cards

```json
show_bill_trend_card       {"months": [{"period": "Oct 2025", "amount": 96.10, "is_current": false}, ...,
                                       {"period": "Sep 2026", "amount": 142.55, "is_current": true}],
                            "highlight_note": "$46 higher than last month"}       // highlight_note optional; one is_current

show_weather_usage_card    {"unit": "therms",
                            "months": [{"period": "Jan 2026", "usage": 142, "hdd": 905}, ...]}      // hdd = heating degree days

show_bill_breakdown_card   {"total": 142.55,
                            "items": [{"label": "Gas supply", "amount": 61.20}, {"label": "Delivery", "amount": 48.10}, ...]}

show_insights_carousel     {"insights": [{"id": "ins1", "title": "Usage is up 38%", "summary": "Colder weather explains most of it.",
                                          "severity": "info|attention|urgent|saving",
                                          "action_label": "Explain my bill", "action_message": "Why is my bill higher this month?"}]}   // 1-5

show_rebate_card           {"rebates": [{"id": "r1", "name": "High-efficiency furnace", "amount": "$800", "deadline": "Dec 1, 2026",
                                         "status_note": "You may qualify", "action_label": "Start claim",
                                         "action_message": "Help me claim the furnace rebate"}]}    // amount is a display string

show_claim_status_card     {"claim_id": "CL-2041",
                            "steps": [{"label": "Submitted", "state": "done|current|todo"}, {"label": "In review", "state": "current"}, {"label": "Paid", "state": "todo"}],
                            "note": "Reviews take up to 10 business days."}

show_payment_receipt_card  {"amount": 142.55, "date": "Oct 18, 2026", "confirmation": "WG-123456", "method_last4": "4242"}
```

Limits the renderer enforces: charts show up to 13 months; carousel up to 6 items; donut up to 6 items (rest grouped under "Other"); labels are clipped by CSS, not truncated in data.

### Quality bar for the tool author

* One card per turn, placed after the supporting tool call; keep the spoken/text answer short because the card carries the detail.
* `severity` must be one of the four values; the badge text (not just colour) shows it.
* `action_message` must be phrased as the customer would say it; it is echoed in the transcript.
* Do not put a `$` in numeric fields; `show_rebate_card.amount` is the only money field that is a display string.

## 0b. Rev 19: web deployments are pinned to an app version

The web widget deployment serves the app version it points to, not the draft. A version created before the CUSTOM widget tools delivers card tools as built-in typed payloads ({type: 'quick_actions', prompt, options}), which chat-messenger.js v1.16 cannot render: its card renderer throws and the whole reply (text too) disappears. After every agent push that changes widget tools, create a version and repoint the deployment (WGL-Demo, c746ebd7-e448-4c29-a028-df672baa388c) to it. ssistant.js (
ormalizeMessages) now converts such built-in elements into the matching cx-show-* element and drops anything it cannot render, so text is always shown. Checking with Sessions.run must pass deployment_id to see what the widget gets.

## 0c. Rev 20: one insight per proactive reply

`show_insights_carousel` is also used for the single suggestion of a proactive moment (after a task, after a bill explanation, or after something the customer said): the payload then holds exactly ONE insight, with `action_label` and `action_message` as its button (for example "Send me bill tips" sends "Send me the tips for managing my bill"). The review the customer asked for still shows up to three. The call shape is unchanged: `{payload: {name: "cx-show-insights-carousel", payload: {insights: [...]}}}`; a call that sends the insights without the inner `name` reaches the page as a `custom_template` with no element and is not rendered (seen once live at the root agent; the instructions now spell the shape out).

## 0d. Rev 23: chat-screen redesign (three new cards, markdown and emoji in chat text)

Chat text is short (at most two sentences, about 30 words) and the card carries the detail. The SDK renders the agent text as markdown (marked + sanitizer): `- ` bullets, `**bold**` and `[label](https://...)` links work; tables, headings and numbered lists are not used. The theme font stack now ends with `"Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji"` so emoji render in colour (widget-theme.css, additive). Voice never receives emoji or markdown: the chat rules are keyed on `{channel}` and the after_model callback strips them if one slips into a voice reply.

Existing cards, their renderers and styles are unchanged (checked by screenshot diff of `widget-preview.html`). Three cards were added to `assistant.js` (same shadow DOM, theme variables, accessible, reduced motion, tested at 375 px). Call shape as for every card: `{payload: {name: "cx-<tool-dashed>", payload: {...}}, summary}`. Icons are names mapped to inline SVG, never emoji characters. One widget per reply: a new card replaces the quick-action chips in that reply.

```json
show_options_card          // cx-show-options-card, 2-4 options, tap sends action_message
  {"title": "How would you like to pay?", "prompt": "Tap one to continue",
   "options": [{"id": "visa", "label": "Visa ending 4521", "benefit": "Your card on file", "badge": "No card fee",
                "icon": "card|bank|calendar|home|flame|check|money|info|clock|phone", "highlight": true,
                "action_message": "Pay with my Visa ending 4521"}],
   "footnote": "You can cancel a payment until it posts."}      // required: options (label and action_message per option)

show_compare_table_card    // cx-show-compare-table-card, up to 4 columns and 6 rows, cells are display strings
  {"title": "Payment plans for $323.64 past due", "columns": ["Monthly", "Down payment"],
   "rows": [{"label": "6 months", "cells": ["$53.94", "None"], "note": "optional"}],
   "highlight_column": 0, "highlight_label": "Pay over time", "caption": "All plans are interest-free.",
   "action_label": "Help me choose", "action_message": "Help me choose a payment plan"}   // required: title, columns, rows

show_checklist_card        // cx-show-checklist-card, up to 6 items, bullets (icon per item) or steps (numbered)
  {"title": "Moving your gas service", "style": "bullets|steps",
   "items": [{"icon": "calendar", "text": "At least 2 business days notice", "detail": "That is 48 hours"}],
   "footnote": "Weekends and holidays do not count.", "action_label": "...", "action_message": "..."}   // required: title, items
```

Renderer notes: the options rows are native buttons in a locked group (one tap, then the group is disabled and the chosen row marked); the table sits in a focusable, horizontally scrollable region with a hidden caption, row and column headers (`th scope`), and an optional highlighted column with a text tag (not colour alone); the checklist is a `ul` or an `ol`. Missing fields are tolerated and an empty list shows the "not available" note.

Agents: `show_options_card` and `show_compare_table_card` and `show_checklist_card` on billing_payments_agent; options and compare on hardship_agent; checklist on service_agent. WGL only (SEMCO is paused and has none of them). `get_verbatim_string` returns an optional chat wording (`text_chat_en` / `text_chat_es`, only the two WNA explanation keys) when `channel` is chat; voice always reads `text_en` / `text_es`.

## 0e. Rev 27 speech pilot (high bill explanation only)

* **Problem.** In voice mode (mic) the widget plays the audio the backend streams (bidi websocket, no browser speech) and the agent speaks only the text part of its reply. The chat rules had made that text a short lead and moved the detail into cards, so the spoken answer was too thin. The agent cannot tell voice from typed (voice mode carries no page variables and `_session.modality` stays TEXT), so the design is mode independent.
* **Agent.** For the high bill explanation only, the text part of the reply (the textResponse of `show_bill_trend_card`) is the full spoken explanation: plain natural sentences, every amount in words, no emoji, markdown, bullets or symbols, built only from the diagnosis and history data. The card payload carries a new additive optional field `display_text` (STRING): the short on-screen lead, for example `That is a big jump, and I see why.` Every other reply and card is unchanged.
* **Page, typed chat.** In the `chat-messenger-response-received` listener, after `normalizeMessages`, `applyDisplayText(messages)` finds a card element with `display_text` (flat `custom_template` message, or inside a `customCard` message), replaces the first text message by `display_text` and drops any further text messages of that reply. Without `display_text` nothing changes. The result on screen equals Rev 25: lead, reasons card, chart.
* **Page, voice (bidi).** `chat-messenger-start-bidi` and `chat-messenger-connect-bidi` set a voice flag and register the message consumer `wgl-speech-pilot` (`registerMessageConsumer`); `chat-messenger-disconnect-bidi` clears the flag. The consumer sees every utterance synchronously: it remembers the bot text utterances of the current reply (a new customer utterance starts a new reply), and when a card utterance with `display_text` arrives it hides those bubbles (`display:none` on their `chat-messenger-utterance`, re-applied on `updateUtterance`; a bot text utterance that arrives after the card is hidden too) and marks the card payload `_lead: true`, which makes the card draw `display_text` as its first line. Audio is never touched. Any exception, or a missing `display_text`, leaves the text visible.
* **Bidi card shape.** The bidi parser turns `sessionOutput.payload` into `customCard` with `richElements: [payload]`. `normalizeMessages` and the consumer accept a flat `{name: "cx-...", payload}` (no `type`) as `custom_template`, and convert a built-in typed element of a known card tool into `cx-<tool>` (otherwise the SDK throws while drawing it). The exact payload shape the live CES bidi stream sends for a CUSTOM widget tool is not yet observed (unverified).
* **Preview.** `widget-preview.html` shows the voice rendition (card with `_lead`).


## 0f. Rev 28 speech pilot extended to the card replies (WGL, chat channel)

* **Agent.** For a reply WITH a card the textResponse of the card tool is the full spoken explanation (plain natural sentences, every amount and date in words, every fact the card shows, no emoji, markdown, bullets, tables or symbols), and the card payload carries the additive optional field `display_text` (STRING): the short on-screen text, with today's short wording of that reply (it may hold markdown such as bullets, bold and a link, and digits). Covered cards: `show_options_card`, `show_compare_table_card`, `show_checklist_card`, `show_bill_breakdown_card`, `show_bill_summary_card`, `show_bill_trend_card`, `show_usage_history_card`, `show_weather_usage_card`, `show_rebate_card`, `show_insights_carousel` (put `display_text` beside `insights`), `show_slot_picker`, `show_appointment_confirmation`, `show_claim_status_card`, `show_payment_receipt_card`. Unchanged: `show_quick_actions`, `show_callback_form`, `show_csat_card`, `show_emergency_card`, replies without a card, the welcome, verbatim and safety text.
* **Page, typed chat.** Unchanged code path (Rev 27 `applyDisplayText`, generic for any card with `display_text`): the long text is replaced by `display_text`; the screen shows the short text and the card as before.
* **Page, voice (bidi).** The message consumer is registered as soon as the SDK element exists (not only on a bidi event), hides the long bot text of the reply when a card with `display_text` arrives, keeps the idle prompt and a text equal to `display_text`, and marks the payload `_lead: true`. Every card (`buildCardHTML`) now draws `display_text` as its first block when `_lead` is set: plain text as one paragraph (`p.dlead`), lines starting with `- ` as a list (`ul.dbul`), `**bold**` and `[label](https://link)` supported, everything else escaped. Without `_lead` (typed chat, preview) the card markup is unchanged, so existing cards stay pixel-identical.
* **Chat text style (Rev 29, option A).** The bot text bubbles (`.message.bot-message`, typed and voice) use the card typeface (Figtree through `--chat-messenger-font-family`), 14.5px, line height 1.5, weight 400, navy; dollar amounts (`$55`, `$41.01`, `$1,000`) and dates (`November 6`, `Nov 6, 2026`) in them are wrapped in `<strong>` (weight 700). `widget-theme.css` sets `--chat-messenger-internal-message-bot-font-weight` and `-bot-strong-font-weight`; the size and line height come from a small stylesheet that `assistant.js` (`WGLTextStyle`) adds to the open shadow roots that hold a bot bubble, because the SDK size variable is shared with the customer bubble. Customer bubbles, cards (`wgl-*`), chips, links, inputs, headings and text that is already bold are never touched; on any error the text stays as it is. No agent change.

## 0g. Rev 29b card lead as a bubble (typed and voice alike)

* **Rule.** `display_text` is ONE bot bubble above the card (one or two short sentences, dollar amounts and dates in `**bold**` markdown, which the SDK renders as `<strong>`; the Rev 29 text style also bolds a bare amount or date). The card never draws `display_text` itself (the Rev 28 `_lead` / `p.dlead` / `ul.dbul` block is gone; `_lead` is ignored) and never repeats a sentence of it.
* **Typed chat.** Unchanged: `applyDisplayText` replaces the long text of the reply by `display_text` (a normal bot bubble above the card).
* **Voice (bidi).** The long spoken text bubble is hidden when the card with `display_text` arrives (audio untouched). The page inserts a text message with `display_text` in front of the card inside the card's utterance, so the SDK draws it as an ordinary bot bubble above the card. If a text utterance or a text message of the same utterance already equals `display_text` (typed sequence) nothing is inserted. If the bubble does not show up within about two seconds the long text is shown again (`verifyLead`). The idle prompt (`Take your time - I'm still here.`) is never hidden.
* **Voice lead check (Rev 29c).** The page confirms the inserted lead bubble is on screen by comparing normalized plain text: markdown markers, entities, case, whitespace and line breaks are ignored (letters and digits only, first 30 characters), because the SDK draws `**bold**` as <strong> and a newline as <br> with no space between the lines. The long text is shown again only if the lead is truly absent after about 2 s, with one console warning per page load.
* **No repeats inside a card.** When `display_text` is present, a card heading or hint of `show_options_card`, `show_compare_table_card`, `show_checklist_card`, `show_bill_summary_card` that repeats a sentence of it (three or more words) is replaced by the card's own default (`Your options`, `Tap one to continue`, `Compare`, `Good to know`, `Bill summary`); the options footnote is dropped when it repeats `display_text` or says `No card fee` while a row already carries it as badge or benefit; the trend card's `highlight_note` is dropped when `display_text` already says it. Without `display_text` nothing is dropped (preview and older replies are pixel-identical).
* **Agent contract.** Payment methods: `title` Ways to pay, `prompt` Tap one to continue., no footnote when the badge says No card fee; `display_text` Your balance is **$83.32**, due **November 7, 2026**. How would you like to pay? High bill: `display_text` That is a big jump, and I see why. Your bill is about **$55 higher** than last month. The spoken text part of every reply is unchanged.
