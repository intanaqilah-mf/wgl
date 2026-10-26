# Washington Gas assistant: demo script

A short walkthrough of what the assistant does well, using phrases that were checked against the live Sandbox app. `WGL_Agent_TEST_SCRIPTS.md` is the full manual test plan; this is the 10 to 15 minute version for showing it off.

Phone numbers and verification answers (last 4 of SSN, PIN, card) are not in the repo. Look the account up in the Firestore console (`wgl_customers`, project `emvnzir-canada-song`, database `acn-bank-fs-db`). The account ids below are the ones to pick.

## Before the demo

1. Refresh the seed dates: in `WGL-Demo/demo-tools`, run `python reseed.py --apply` (see its README). Without it appointments, outage ETAs and shutoff dates drift into the past.
2. Open the site page you want (the page sets the opening chips: billing, assistance, savings and so on). The call from a known phone number is identified silently; a typed phone number is looked up in chat.
3. Optional settings, sent as chat variables: `demo_ctx.demo_clock_override` (an ISO time, moves "today" and drives the after-hours demo), `demo_ctx.failure_mode` (`cis_down`, `cis_timeout`, `oms_down`, `gateway_decline`, `slot_unavailable`).

## The accounts

| Account | Story |
|---|---|
| W01 (VA) | Owner demo account: no arrears, Visa on file. Use it for balance, bills, usage, rebates, payment. |
| W04 (DC) | Arrears and a shutoff notice; eligible for the Arrearage Management Program. Hardship demo. |
| W09 (MD) | Appointments today (en route, delayed, meter test, stop) and a later reconnect. |
| W14 (VA) | Cold-snap high bill, fully explained by the weather; moving. |
| W08 (VA) | Showpiece high bill: estimated read, adjustment, corrected earlier bill. |
| W11 (DC) | Seven accounts on one phone number; the assistant asks for the street. |
| S05 (MI) | SEMCO showpiece high bill (cold early fall). |

## Walkthrough (chat)

| # | Say | What you should see |
|---|---|---|
| 1 | Open the chat on the billing page | Greeting and quick-reply chips for the page. |
| 2 | "What is my balance?" (W01) | Short answer plus the bill summary card. |
| 3 | "Why is my bill so high?" | Bill trend card with the highlighted month, then an offer of next steps. |
| 4 | "What is my bill made of?" then "Compare this month with last month" | Breakdown card, then the compare table. |
| 5 | "How much gas did I use?" then "Does the weather explain my usage?" | Usage history card, then the weather-versus-usage card. |
| 6 | "What rebates are available for a new water heater?" | Rebate card with amounts and apply-by dates; "Start claim" button. |
| 7 | "Is there an outage in my area?" | Answered straight away; the ZIP decides the answer (S06 and W07 are in an active-outage ZIP). |
| 8 | "Do I have any appointments coming up?" (W09) | The appointments listed with today's windows. |
| 9 | "I want to start gas service" | Checklist, then the slot picker and the appointment confirmation card. |
| 10 | "I can't pay my bill and I got a shutoff notice." (W04) | Warm reply plus the options card (Arrearage Management Program first). |
| 11 | "I want to pay my bill." | Asks to verify first (identified by phone is not verified); after verification the amount and payment-method options, the disclosures, and the receipt card. Completing it writes a payment, so stop before the final yes unless you mean it. |
| 12 | "No, that's all I need, thanks" | The star rating card; tapping a star ends the conversation politely. |

## Safety and people

| Say | What you should see |
|---|---|
| "I smell gas in my kitchen" | Emergency card with the call button, at any hour and from any state. |
| "My carbon monoxide alarm is going off" | The same emergency flow. |
| "I want to talk to a representative." | A hand-off to a person (no questions about why). |
| "My husband is on oxygen and we can't pay." | Goes to a specialist hand-off, not the gas-emergency flow. |

## After hours

1. Send `demo_ctx.demo_clock_override` = `2026-10-25T03:00:00-04:00` (a Sunday, 3 AM Eastern) with the first message.
2. Say "I want to pay my bill." The assistant speaks the closed-office notice and shows the callback form.
3. Type a number in the form. The assistant files the callback ticket and gives the ticket number.

Reading a balance or bill, usage, outages and emergencies are still answered after hours. Without a demo clock the assistant follows its normal behaviour at any hour. To enforce the real clock, send `demo_ctx.enforce_office_hours` = `true`.

## Known soft spots

- The model occasionally skips a card (start service, appointment list) or asks "Would you like to verify?" instead of going straight to verification. A retry fixes it.
- Chips are limited to three, and some card labels are English only.
- Every test conversation adds records to the shared database; `demo-tools/cleanup_tests.py` removes scripted ones.
