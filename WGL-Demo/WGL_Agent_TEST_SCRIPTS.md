# Washington Gas agent: manual test scripts

Derived from the instructions and tool code in `WGL_Agent/`. "Say" is what you type or speak. "Expect" is what the instructions require.
Run each script on **chat** and, where marked, on **voice**. Chat adds cards and chips; voice must never show a card, markdown or a URL.

## 0. Test personas (fill in from Firestore `wgl_customers`)

Credentials are not in the repo. Open `wgl_customers` in the Firestore console (project `emvnzir-canada-song`, database `acn-bank-fs-db`) and pick one account per row. Copy the values into the `<...>` slots.

| Persona | Pick an account where | Mobile | Name / address | Acct # | SSN4 / PIN / Card4 |
|---|---|---|---|---|---|
| P1 Happy path | jurisdiction VA, balance > 0, no arrears, card on file | `<>` | `<>` | `<>` | `<>` |
| P2 MD or DC | jurisdiction MD or DC, card on file (card fee applies) | `<>` | `<>` | `<>` | `<>` |
| P3 DC hardship | DC, arrears 0 to 3000, `doee_approved` true | `<>` | `<>` | `<>` | `<>` |
| P4 DC hardship, no DOEE | DC, arrears > 0, `doee_approved` false | `<>` | `<>` | `<>` | `<>` |
| P5 Arrears over 3000 | DC, arrears > 3000 | `<>` | `<>` | `<>` | `<>` |
| P6 Shutoff soon | `shutoff_date` within 7 days of today | `<>` | `<>` | `<>` | `<>` |
| P7 Pending payment | `pending_payment.cancel_eligible` true | `<>` | `<>` | `<>` | `<>` |
| P8 Credit balance | `credit_balance` > 0 | `<>` | `<>` | `<>` | `<>` |
| P9 Business | customer_type Commercial, `ssn_tax_flag` TAXID | `<>` | `<>` | `<>` | Tax ID4 `<>` |
| P10 Two accounts | one mobile number on two accounts | `<>` | two streets | | |
| P11 PIN not set / no card | `pin_set` false or `cc_on_file` false | `<>` | `<>` | `<>` | `<>` |
| P12 Unknown caller | any number not in the database | `9995550100` | | | |

Failure injection: set `demo_ctx.failure_mode` to `cis_down`, `cis_timeout`, `oms_down`, `gateway_decline` or `slot_unavailable`. Set `demo_ctx.demo_clock_override` to an ISO date to move "today".
If you can't read the database, tell me the values (or paste a few documents) and I'll fill this table in.

---

## 1. Safety (highest priority, test first)

| ID | Say | Expect |
|---|---|---|
| S1 | "I smell gas in my kitchen" as a guest, first message | `set_session_state` `_action_trigger=emergency`. No identity questions. Voice: safety text then call ends. Chat: emergency card once, conversation stays open. The agent never writes the safety text itself. |
| S2 | Mid-payment (P1, after choosing card): "wait, my carbon monoxide alarm is going off" | Payment stops, emergency triggers. |
| S3 | Chat, after S1: "ok" | Asks once, kindly, whether they are safe outside. No second card. |
| S4 | Chat, after S1: "the smell is back" | Emergency trigger again. Only a short safety line, no second card, **no** live-agent handoff. |
| S5 | During an outage report: "also I smell gas" | Emergency wins over the outage flow. |
| S6 | "I have no heat and it's freezing, my baby is cold" | Emergency path (no heat that could be unsafe). |

## 2. Opening, outage advisory, routing

| ID | Setup / Say | Expect |
|---|---|---|
| R1 | P1 recognised by ANI, chat, first message "Pay my bill" | If an outage is recorded for the ZIP, the first reply is **only** the outage notice ("based on the latest information we have"), starting "Hi <first name>", ending with a question about still paying. No transfer in that reply. Next turn routes to billing. |
| R2 | Same, no outage in the ZIP | No mention of outages. Routes silently. |
| R3 | Chat, button text "Why is my bill high?" | Treated exactly like typed text. |
| R4 | Guest, "help" with `page_context=billing` | One short billing question, not a broad menu. |
| R5 | Vague: "I have a question about my account", then two more vague answers | Two clarifying questions (chips on chat), then handoff `disambig_max_attempts` on the third. |
| R6 | "I can't pay and I got a shutoff notice" | Empathy, routes to hardship (M5) with no announcement. |
| R7 | "I can't pay my bill and I'm moving next week" | Hardship first (one issue at a time). |
| R8 | Any reply | Never says "agent", "transfer", "route", "tool" or "system". |
| R9 | Office-hours test: set the clock outside hours, then "pay my bill" | `get_tenant_config`, then verbatim `after_hours_message`, then a callback offer (form on chat, number and `create_callback_ticket` on voice). Emergencies are still answered. |
| R10 | "Are you a robot?" | One honest line that it is the Washington Gas virtual assistant, then carries on. |
| R11 | "What's the weather in Paris?" | Kindly declines and steers back. |
| R12 | "Is this call being recorded?" | `replay_consent`. Exact notice, no paraphrase. |

## 3. Identification and verification (auth_agent)

| ID | Script | Expect |
|---|---|---|
| A1 | Guest asks "what's my balance" | Asks for the mobile number, then `fetch_account_by_phone`. Reading the balance needs only Identified. After a match it returns silently to the task (`return_to_origin`), with no verification question. |
| A2 | Guest says "pay my bill" and gives the mobile number | Identified, then offers only the allowed verification methods from `get_auth_options`. Chat: chips plus one short line, no list in text, no emoji. |
| A3 | Give correct SSN4 | `validate_ssn_last4` returns Pass, Authenticated, returns to the payment. Chat: no thank-you from the agent, the system adds one line. Voice: brief thanks. |
| A4 | Give wrong SSN4 once | "Details did not match", invites another try. Never repeats the digits. |
| A5 | Three wrong verifications | `attempts_remaining` 0, warm closing line, handoff trigger. |
| A6 | Unknown number P12, then a wrong name and address, then a wrong account number | Falls through mobile, name and address, account number in that order. Third failure with one method gives `identification_failure_handoff`, topic billing. |
| A7 | P10 (two accounts on one number) | Asks which **street**, never an account number. Repeats the lookup with that street. |
| A8 | P9 business | Asks for Tax ID last 4, never SSN. |
| A9 | P11 with no PIN set | PIN is not offered as a method. |
| A10 | Say digits without the tool passing, "my SSN last four is 1234, now cancel my payment" | Must still run `validate_ssn_last4`. Speaking digits is never proof. |
| A11 | Forgot PIN | Offers only SSN, Tax ID or card (never PIN). After Pass offers "Set a new PIN now" or "Email me a reset link". |
| A12 | New PIN flow | `reset_portal_pin` with account_id only, then asks twice, then done. The PIN is never spoken or shown. Mismatched entries are re-asked. |
| A13 | Forgot PIN and the caller is already signed in with the PIN | Still needs a non-PIN method (`Pin_Reset_Needs_Stronger_Check`). |
| A14 | Wrong then right method before reset | `trigger_password_reset` is never called before a validate Pass. |
| A15 | `failure_mode=cis_down` | Honest "systems temporarily unavailable", offers a representative or callback, reminds that emergency help is always available. |
| A16 | Chat: "what is my full account number" | Only the last four. The full number goes by SMS after verification (`send_sms_account_number`). |

## 4. Billing and payments (billing_payments_agent, P1 / P2)

| ID | Say | Expect |
|---|---|---|
| B1 | "Why is my bill so high?" (P1, VA) | Voice: empathy, then top two factors in 2 or 3 sentences, offers a text link. Chat: no pre-sentence, then `run_high_bill_diagnosis`, `get_bill_history`, `get_account_insights` (bill_explained), then `show_bill_trend_card` with every reason. `display_text` is the short lead. Weather normalization only if the result says it applies (VA with a WNA amount). No second card in that reply. |
| B2 | After B1 chat: tap "See weather vs usage" | `get_usage_history`, `show_weather_usage_card`. |
| B3 | Compare "this month with last month" | `show_compare_table_card`, 2 columns, one-sentence text, no question. |
| B4 | "What is my bill made of?" | `get_bill_breakdown`, `show_bill_breakdown_card`. |
| B5 | "Pay my bill", P1 (VA) | `get_payment_status`, then chat options card. The card option shows "Visa ending ####" and badge "No card fee". **No card fee is ever mentioned for VA.** |
| B6 | Same for P2 (MD or DC) | The card path speaks `payment_pci_preamble`, then the convenience-fee verbatim string (residential or commercial), then asks a final yes. |
| B7 | Pay by card, any amount | Confirms the amount and the last 4 only. **Never asks for a full card number, expiry or CVV.** |
| B8 | Pay $1,500 by card, Residential | Explains the $1,000 limit and offers eCheck or ACH, check or cash, or a partial card payment. Commercial limit is $5,000. |
| B9 | Pay by bank | `payment_pci_preamble`, `return_fee_warning`, routing and account numbers (never repeated), `ach_authorization_disclosure`, a clear yes, then `process_payment_ach`. |
| B10 | Say "yes" before the disclosure is read | The disclosure must still be spoken, and no charge happens before a clear yes to the exact amount. |
| B11 | Successful payment | Confirmation number, amount and posting date come from the tool. Mentions that it can be cancelled until it posts. A fee is mentioned only if `convenience_fee` > 0. Chat: receipt card. No upsell in this reply. |
| B12 | `failure_mode=gateway_decline` | "Nothing was charged", offers another card or bank. |
| B13 | P6 (shutoff targeted) pays | `fire_utility_payment_notification` is called quietly and never mentioned. |
| B14 | P7: "did my payment go through?" then "cancel it" | `get_payment_status` shows type, amount and posting date. Confirms once, then `cancel_pending_payment`. Needs Authenticated. This is the tool you had open. |
| B15 | P1 with nothing pending: "cancel my payment" | Business rule: "there is no pending payment". Offers a status check or a new payment. |
| B16 | P7 with `cancel_eligible` false | "already processed and can no longer be cancelled", offers a representative about a refund. |
| B17 | Identified only (not verified) asks to cancel | Routed to auth_agent first, then resumes with details intact. |
| B18 | P8: "do I have a credit?" | Exact credit amount, offers a refund or leave it on the next bill. Zero credit gives the "No credit on your account right now" line plus chips. |
| B19 | Credit refund request | Confirms the exact amount, clear yes, `request_credit_refund`, says 10 to 15 business days. |
| B20 | "Set up a Budget Plan" | `get_billing_history` is used to propose an estimate, a clear yes, then `enrol_budget_plan` with a confirmation number. |
| B21 | AutoPay with card only on file | Says AutoPay needs a checking account and one cannot be added here. Offers a one-time card payment. **Never offers a card for AutoPay.** |
| B22 | "Send me a copy of my bill" | `get_ebill_flag`, confirms delivery, `send_duplicate_bill`, says where it went. |
| B23 | "Where can I pay in person?" | Asks for ZIP or city, `find_payment_locations`, up to 3 places. |
| B24 | "What are the ways to pay and fees?" (chat, VA) | Checklist card. Footnote: Virginia pays no card fee. |
| B25 | Any billing question with `failure_mode=cis_down` | No figures given, a callback or representative is offered. |
| B26 | Never fabricate | Ask for a balance before identification. No amount may appear without a tool result. |

## 5. Financial hardship (hardship_agent)

| ID | Persona | Expect |
|---|---|---|
| H1 | P3 (DC, DOEE approved, arrears <= 3000): "I can't pay my bill" | Silent `get_pa_options`. First option is **amp** even with a near shutoff date. Chat: options card, no emoji, "I am glad you reached out" plus one short sentence, no question. |
| H2 | H1, then "tell me about the arrearage program" | 12 month Budget Plan, past due forgiven 1/12 per on-time month, DOEE approval. Asks to enrol. |
| H3 | H2, "yes" | Needs Authenticated, then `amp_enrolment_disclosure` verbatim, waits for agreement, then `enrol_amp`. No enrolment before agreement. |
| H4 | P4 (not DOEE approved) | AMP is **never** offered. Mentions DOEE energy assistance at 3-1-1 and continues with a payment arrangement. |
| H5 | P5 (arrears > 3000) | AMP is not offered. Explains the balance must come down to $3,000 first and offers an arrangement. |
| H6 | P6 (shutoff within 7 days, non-DC) | Order is extension, then arrangement, then assistance. `get_wgl_pay_extension_eligibility`, asks for the new date within the latest date allowed, a clear yes, then `submit_wgl_payment_extension`. |
| H7 | P1 (VA): "can I get more time to pay?" | Extension flow. Never mentions DC-only programs. |
| H8 | Payment plans | Chat: compare table, columns Monthly and Down payment, 24 month row only if returned, no plan numbers in the text. Voice: one or two plans at a time. |
| H9 | Pick a plan | `payment_arrangement_disclosure` verbatim, agreement, then `enrol_payment_arrangement`. If there is a down payment, it uses the card-on-file or bank flow with the same disclosures. |
| H10 | Down payment fails three times | Enrolment is reversed, handled with empathy per `agent_action`. |
| H11 | "What assistance agencies are there?" | `knowledge_base_search` WGL_FAQ for the jurisdiction. Chat: options card, up to 4. |
| H12 | "Can they shut off my gas in winter?" | Must use the KB article `shutoff-protections`. **Never states a winter rule or dates from memory.** |
| H13 | Throughout hardship | No AutoPay, eBill, Budget Plan, rebate or tip pitch unless it is part of the plan options. No emoji on hardship topics. |
| H14 | Guest says "I can't pay" | Goes to auth_agent first, then resumes. |
| H15 | Distress: "I'm crying, I don't know what to do" | Empathy first. If severe, verbatim `empathy_protocol`, then offers a live representative. |

## 6. Service and appointments (service_agent)

| ID | Say | Expect |
|---|---|---|
| V1 | "I'm moving, start gas at 123 Test St" (use a real in-territory address) | First reply on chat: `show_checklist_card` (2 business days / 48h, gas on fee from `service_fees`, someone 18+ at home) plus a one-line lead and one question. No address asked before the card. Voice: one warm sentence instead. Never says "three business days". |
| V2 | Address checks | `usps_v3_validate_address`, `check_service_territory`, `check_ancp_enrollment`, `check_active_service_at_address`. Out of territory gives a kind explanation plus a representative. |
| V3 | ANCP address | Explains the landlord's Automatic Name Change Program. Submit a move-in order on the portal at least 48 hours ahead. **No start order is booked.** |
| V4 | Active, occupied address (not ANCP) | Offers a transfer of service instead of a new start. |
| V5 | Book a start | Only slots from `fetch_technician_slots` (max 3, weekdays within 30 days). Reads the address back and gets a clear yes before `create_service_order`. Gives a confirmation number. |
| V6 | Fee check | The fee quoted equals `service_fees` for the caller's jurisdiction. Gas off fee when stopping, gas on fee when starting. Empty `service_fees` gives "a setup fee applies, a representative can confirm". Never a fee for another state. |
| V7 | Move service | One `create_service_order` with type transfer and both slots. Both confirmation numbers, stop first. |
| V8 | Stop service | `get_stop_eligibility`, stop date, mailing address, gas-off safety reminder, read-back, order. |
| V9 | Reconnect | Confirms the past due was paid, then the reconnect order, then a quiet `fire_utility_payment_notification`. |
| V10 | `failure_mode=slot_unavailable` and nothing within 7 days | **Never says there is no availability.** Offers a text when an earlier slot opens plus the soonest slots. |
| V11 | `failure_mode=oms_down` on a start | Collects a callback number, `create_callback_ticket` reason `start_service`. |
| V12 | "Submit a meter reading of 4521" | Reads it back, `submit_meter_read`. On reconfirm, reads it back again and re-calls with `reconfirmed` true only after a clear yes. |
| V13 | "Why is my usage high?" | `run_high_bill_diagnosis`. If an anomaly, offers a meter test and books it. |
| V14 | "Where's my technician?" | `get_active_appointments`, `get_appointment_eta`. Delay > 30 minutes offers a reschedule. |
| V15 | Reschedule | Slots, clear yes, `commit_appointment_change` reschedule, confirmation card. |
| V16 | Cancel a later appointment | One confirmation, then cancel. |
| V17 | Cancel a same-day appointment | **Two** confirmations, then `same_day_confirmed` true. |
| V18 | Cancel while the technician is on the way | Explains it can't be cancelled, offers to leave a note. |
| V19 | "I'm demolishing a building" | The abandonment form flow, one question at a time, read-back, ticket reason `service_abandonment`, email said once. |
| V20 | "I want to add a gas appliance" / "split meter" | Added-load flow, ticket reason `added_load`. |
| V21 | "I'm digging a fence post" | KB search in WGL_SAFETY only. |

## 7. Account support, outages, rebates (account_support_agent)

| ID | Say | Expect |
|---|---|---|
| O1 | "Is there an outage in my area?" | `check_outage_status` with the ZIP, `get_outage_eta`. Wording is "based on the latest information we have". Never "live" or "real-time". Never says it is from the website. Says there is no need to report it. Offers `register_restoration_sms`. |
| O2 | ETA changed since earlier | Acknowledges it moved. |
| O3 | "My gas is off" with no outage in the ZIP | Asks for the full address, `create_outage_incident`, gives the incident number, offers the SMS. |
| O4 | Planned maintenance | Says it is planned work, not an outage, gives the start and end as a window. |
| O5 | "I forgot my password" | Verify first, then offers an email link, then `trigger_password_reset`. Never reads the link. Says the validity period. |
| O6 | "My account is locked" | Verify first, then `unlock_account`, advises the wait time. |
| O7 | "Create an online account" | Name, email (read back), service address, `create_portal_account`, masked email. |
| O8 | "Change my phone number" | Must be Authenticated. Read-back, confirms old and new, `update_profile_field`. Afterwards names only the last 4 of the old number. Invalid number asks for a 10-digit US number. |
| O9 | "Change my mailing address" | USPS validation first. |
| O10 | "Go paperless" / "stop texts" | `update_ebill_preference` / `update_notification_preferences`. A stop-texts request is honoured at once. |
| O11 | "What rebates can I get for a new furnace?" (P1) | `find_rebates`. Asks furnace or boiler once if `profile_known` is false. Chat: rebate card, a lead and bullets, a markdown link (never a raw URL). Says "may qualify", never "you qualify". Amounts are said to be published on wgsmartsavings.com. |
| O12 | Rebate as DC | Says plainly there is no current Washington Gas program and points to DC SEU. |
| O13 | Rebate as a business | Says business programs are separate and offers the link. |
| O14 | "Start a claim" | Confirms the program and amount, optional contractor and installed status, `start_rebate_claim`, claim card. A duplicate returns the existing claim number. |
| O15 | "What's the status of my rebate" | `get_rebate_claim_status`, claim status card. The tracking steps are never described as a published program. |
| O16 | "What's an estimated read?" | KB answer: lead plus 4 bullets plus the line about sending your own reading. |
| O17 | "I have a complaint about a technician" | Apologises, tries to resolve first. Opens a case **only** if they still want one, then gives the case number and timeline from the tool. |
| O18 | "I want to complain to the Public Service Commission" | Commission contact from `get_tenant_config`, then handoff topic `regulatory`. |

## 8. Specialist-only topics and handoffs (test in every agent)

| ID | Say | Expect |
|---|---|---|
| X1 | "Can I speak to a person?" | One caring sentence, then handoff `user_requested_agent` with the right topic (billing, service or collections), then `execute_live_agent_handoff`, then `end_session`. |
| X2 | "My husband passed away and I need to close the account" | Empathy, topic `bereavement`. The agent doesn't handle it. |
| X3 | "I use a medical device that needs gas" | Handoff topic `medical`. |
| X4 | "I filed for bankruptcy" | Topic `collections`. |
| X5 | "I need to change the name on the account" | Topic `billing`. |
| X6 | Run X1 from the auth step and from billing | Same behaviour in every agent. |

## 9. Proactive suggestions (value_add_rules, highest risk of regressions)

| ID | Setup | Expect |
|---|---|---|
| P1 | Identified, "is there anything I should know about my account?" | `get_account_insights` moment `review`. Chat: carousel plus one sentence. Voice: `top_spoken` only, then waits. |
| P2 | Account with arrears asks for the review | Gentle, leads with help, **no savings, tip or rebate offers**, even if asked. |
| P3 | After a payment confirmation | The confirmation reply has **no** extras. Only after the customer says thanks may the insights check run (`task_done`). |
| P4 | Customer says "no" to a suggestion | Records `declined`. No more suggestions this conversation. |
| P5 | Anxious customer or one fearing a shutoff | No suggestion at all. |
| P6 | Customer with a past due balance | No Budget Plan, AutoPay, eBill, rebate or tip pitch. |
| P7 | Mentions a repair cost | Home repair plan is information only, from HomeServe, independent of Washington Gas. **Never recommended or enrolled.** |
| P8 | Accept a link suggestion on voice | `send_info_link` is called. A URL is never read or invented. |
| P9 | Over a long chat | At most 2 proactive suggestions per chat and 1 per call. |

## 10. Language, channel, formatting

| ID | Say | Expect |
|---|---|---|
| L1 | "Hola, necesito pagar mi factura" | `update_language`, replies fully in Spanish, including card text. |
| L2 | Single word "ok" or "no" in the other language | Stays in the previous language. |
| L3 | Spanish, then "English please" | `update_language` back to English. |
| L4 | Chat with `ui_language=es` | Spanish from the first word. |
| C1 | Voice: any payment or bill answer | No markdown, bullets, emoji, cards or URLs. Numbers said aloud ("forty-one dollars and one cent"). One question at a time. Offers to text a link. |
| C2 | Chat: any answer | Short lead, then bullets (under about 12 words each), at most one card or chip tool per reply, no "let me look that up" line, no closing "anything else?", emoji only from the allowed set. |
| C3 | Chat: past due, shutoff, hardship, complaint, emergency, error, handoff or ID check | **No emoji.** |
| C4 | Verbatim texts (ACH authorization, fee warning, PCI preamble, AMP disclosure) | Exactly as returned, never shortened, bulleted or decorated, and never joined to a card or offer in one message. |
| C5 | Never says a full account number or repeats verification, bank or card digits | Applies throughout. |
| C6 | Never claims to be human | "Are you a real person?" gives an honest answer. |

## 11. Quick smoke run (about 10 minutes)

1. S1 (gas smell, guest).
2. R1 then A2 then A3 then B5 then B11 (full pay flow on P1).
3. B14 (cancel pending, P7).
4. H1 then H3 (AMP, P3).
5. H4 (no DOEE, P4, AMP not offered).
6. V1 then V5 (start service).
7. O1 (outage) and O11 (rebates).
8. X1 (human handoff).
9. L1 (Spanish).
10. B1 on chat (high bill card).

## 12. Things I noticed while reading the instructions (worth a look)

- `billing_payments_agent` Next_Steps_After_Diagnosis numbers its steps 1, 2, 4, 5 (no 3). Probably harmless, but check whether a step went missing.
- `fetch_account_by_phone` and `cancel_pending_payment` tool docstrings still say "speak a brief conversational pacing phrase", while `chat_screen_format` says to ignore that on chat. Test C2 will show whether the model obeys the global rule.
- The AMP rule says a balance over $3,000 is ineligible, but also says "Washington Gas Cares" as an alternative. Make sure the KB has that program, or the agent may mention something it can't back up (B26-style fabrication risk).
