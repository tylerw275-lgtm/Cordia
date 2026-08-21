# Cordia AI — What It Is & What It Needs to Launch

## 1. In one paragraph

Cordia AI is a **private, personal AI assistant** for one person (Cordia) and a small,
invited circle of her family members. She reaches it by **text message or email**, and
an AI (Anthropic's Claude) replies conversationally — and takes real action: remembering
what she tells it, tracking family details and birthdays, searching and monitoring
flight prices, reviewing leases, and sending her a daily brief. Invited family members
get their own limited access to quietly contribute gift ideas and preferences, without
ever seeing Cordia's private information. It is **not** a public app — access is locked
to approved phone numbers and email addresses. Under the hood it's a Python web service
with a database, running on Railway.

---

## 2. How it works (plain English)

**By text:** Cordia texts Cord at **(629) 225-9067** → Signal House (the SMS provider) forwards it
to the app → the app confirms it's really her and really from Twilio → Claude reads the
message plus relevant memory and recent history, optionally uses a "tool" (e.g. search
flights), and texts back. She can even send a **photo** and the assistant can understand
it.

**By email:** she (or an invited family member) can email the assistant; it reads new
mail, replies by email, and keeps the same conversation going.

**Family circle:** when Cordia grants a family member access, she shares a consent link;
once they sign it and text in, they can contribute — but they get a **restricted** set
of abilities and never see her private data.

**The assistant's name is "Cord"** — Cordia's AI chief of staff. (The carrier-registered
SMS program name remains "Cordia AI by AI-Gen Partners" for compliance.)

---

## 3. What it can do today (built & working)

**Conversation & memory**
- Natural back-and-forth over SMS or email; understands photos sent by text.
- Remembers facts, preferences, and instructions, and recalls them later.

**Family knowledge & coordination**
- Profiles for family members (interests, birthdays, kids, locations, loyalty programs).
- Lists upcoming birthdays, anniversaries, and events; schedules family events.
- Tracks activities done with grandchildren and helps keep attention balanced across them.
- Updates family notes/interests over time.

**Family circle (invited relatives, limited access)**
- Relatives can share gift ideas, tips on how they like to be contacted, and update
  their own kids' interests — all surfaced to Cordia when useful.
- Cordia has owner-only tools to grant access, ask family to source ideas, and review
  what they've shared.

**Flights**
- Live flight search (Duffel API), cheapest-first.
- "Watch this route" price alerts — texts Cordia when a watched flight hits her target
  or drops 10%+.
- *Booking* via a hosted Duffel checkout link is built but kept **off by a feature flag
  until it's tested end-to-end** (see §7).

**Outbound communication (draft → approve → send)** *(feature-flagged, see §7)*
- Cordia can say "tell everyone on the trip we leave Friday" — Cord drafts a
  **personalized message per person/family**, shows her the drafts, and sends **only after
  she approves**. SMS goes only to opted-in numbers; everyone else gets email.
- A secure **contact book**: Cord asks once for a missing email/phone, saves it, and never
  reveals stored contact details to anyone (lookups return only "on file: yes/no").

**Inbound capture & the Naples house**
- Contacts Cordia marks trusted (e.g. a relative sending the school calendar) get their
  emails captured: every date saved as a family event, a one-line summary texted to
  Cordia — and third-party mail is treated as information, never instructions.
- "What's happening for the kids in <city>?" lists those events and offers flights
  around the best dates.
- The **Naples, FL house inbox** is monitored separately: each property email is
  summarized to Cordia by text, with replies drafted for her approval.

**Smart flight preferences**
- Cord asks once for her ranked preferences (non-stop assumed; fastest vs cheapest;
  cities to avoid; cabin; loyalty programs) and applies them to every search — and when
  a preference hides a notably faster/cheaper option, it surfaces the tradeoff in one
  line instead of hiding it. Preferences update as she overrides them.

**Expert-grade answers ("world-class prompting")**
- Open-ended asks ("help me plan a comedy night") trigger an internal expert brief —
  role, goal, her tastes/network as constraints, task decomposition — producing a real
  producer-grade plan, not generic tips. Prompting adapts automatically to whichever
  Claude model is configured.

**Learning & expectations**
- If a reply takes more than a few seconds, Cord texts a short "working on this" note.
- Cord only ever offers capabilities that are actually switched on; anything gated is
  described as "in development," and Cordia can ask for features she wants — they're
  logged for the team (`request_feature`).

**Live web research**
- Cord can search the web and read pages, so prices, hours, addresses, availability
  and seasonal conditions come from a page it actually read rather than memory —
  and it says where each figure came from. A failed search is reported as a failure,
  never passed off as "nothing found."

**Cost tracking**
- Every text (billed per *segment*, not per message), email, AI request and web
  search is recorded with what it cost, and the dashboard shows this month, all
  time, and a per-person breakdown. Rates are configurable; the page always states
  the ones it used.

**Lease / document review**
- Analyzes a lease, flags clauses by severity (standard / flag / urgent), stores a
  summary, and can schedule renewal-deadline reminders.

**Proactive outreach (it reaches out on its own)**
- **Morning brief** — a daily summary text.
- **Birthday reminders** — nudges at 7 days out, 1 day out, and day-of.
- **Birthday prep** — proactively prepares ahead of upcoming birthdays (~2 weeks out).
- **Flight price alerts** — re-checks watched routes hourly.
- **Lease reminders** — daily, e.g. renewal deadlines.

---

## 4. Compliance & carrier setup (the current launch blocker)

US carriers require every automated SMS service to be registered ("A2P 10DLC"). We've
built everything reviewers ask for:
- Public web pages: **Privacy Policy, Terms, SMS Program Disclosure, and a fillable
  Consent Form** (name + mobile + an actively-checked consent box, recorded in the DB).
- Standard keyword handling: **STOP** (unsubscribe), **START** (subscribe), **HELP**.
- Consistent branding everywhere as **Cordia AI by AI-Gen Partners (Marq LLC)**.
- Family members enroll compliantly: Cordia shares the consent link person-to-person;
  they sign, then text START. The assistant never messages anyone first.

**Status: APPROVED and live.** The campaign is registered through Signal House under
brand AI-Gen Partners (Marq LLC), sending from **+1 (629) 225-9067**. Consent pages live
at cordia.aigenpartners.com; the opt-in page carries the consent form itself.

---

## 5. Technical appendix (for the developer)

**Stack**
- Python + **FastAPI**; **PostgreSQL** via async SQLAlchemy; **Alembic** migrations
  (through `012_usage_events`); **APScheduler** for recurring jobs, on an
  explicit `SCHEDULER_TIMEZONE`.
- **SMS:** Signal House (10DLC campaign approved). Inbound webhook
  `POST /webhook/signalhouse` (shared-secret auth, constant-time compare); outbound via
  their REST API, and every send checks the opt-out list. Provider is swappable via
  `SMS_PROVIDER` (`app/services/sms_service.py`); the Twilio driver remains as a
  fallback and refuses to run unverified. Webhooks acknowledge immediately and process
  in the background, with duplicate-delivery suppression.
  Sender allow-list = Cordia + a test number; family numbers resolved via the DB.
- **Email:** two-way. Gmail (IMAP poll every ~120s + SMTP send) or Resend (inbound
  webhook + API send). See `app/services/email_inbound.py`, `app/scheduler/jobs/email_poll.py`.
- **AI:** Anthropic Claude (`claude-sonnet-4-6`). Per-conversation history, proactive
  memory injection, agentic tool loop (≤10 steps), prompt caching, image/vision support.
  The family roster is injected into every owner prompt, so Cord never has to call a
  tool to know who the family is.
  Model-adaptive prompting profiles (`app/prompts/prompt_profiles.py`) pick thinking/effort
  params and token budgets per model; deep-work asks get an 8K budget vs 2K conversational.
  Separate, **restricted** system prompt + toolset for family-circle members
  (`sender_role="family"`).
- **Access control:** the data APIs (`/api/v1/*`, `/health/config`, `/health/data`) are
  gated on `ADMIN_API_SECRET` (`X-Admin-Secret` header or `?secret=`) and fail closed
  when it's unset. Webhooks keep their own verification; `/health` and the compliance
  pages stay public. The OpenAPI schema and `/docs` are disabled.
- **Flights:** Duffel API (`search_flights`, `get_offer`, `create_link_session`,
  `get_order`); booking webhook `POST /webhook/duffel` (HMAC-verified) + `/booking/*`
  landing pages.
- **Hosting:** Railway, served at **cordia.aigenpartners.com**. All secrets/config via
  environment variables. `/health/config` and `/health/test-send` are secret-gated
  deployment diagnostics.

**Feature flags (`app/config.py`)**
- `enable_web_research` (on, ~$0.01/search) · `enable_flight_search` (on) · `enable_lease_review` (on) · `enable_family_coordination`
  (on) · `enable_email` (on) · `enable_flight_booking` (**off** until tested) ·
  `enable_outbound` (**off** until the approval flow is verified) · Naples inbox (on when
  `naples_email_address`/`naples_email_app_password` are set).

**Scheduled jobs (`app/scheduler/scheduler.py`)** — flight monitor (hourly), lease
reminders (08:00), birthday reminders (07:30), morning brief (~07:45), birthday prep
(08:15), email poll (~2 min, Gmail only).

**Data model (`app/models/`)** — Users; Conversations/Messages; FamilyMembers/
FamilyEvents/GrandkidActivities; family-circle tables; Trips/FlightWatches/
PriceSnapshots/FlightBookings; Memories; Leases/LeaseClauses/LeaseReminders; plus
`sms_consent` and `consent_submissions`; Contacts (secure address book) and
OutboundMessages (draft/approve/send queue) — migration `006`.

**Key files to orient around**
- `app/api/sms.py` — SMS/MMS webhook, keywords, family resolution.
- `app/services/claude_service.py` — the AI conversation loop and role handling.
- `app/tools/` (+ `registry.py`) — every capability the AI can call.
- `app/scheduler/` — the proactive jobs.
- `app/api/compliance.py` — consent / privacy / terms / opt-in pages.
- `app/data/family_seed_loader.py` + `app/services/family_seed.py` — parse the roster
  from `FAMILY_SEED_JSON` and load it idempotently on boot. The roster itself is
  configuration, never source: a malformed document seeds nothing and logs the failing
  field path (never the value), and an unset one with an empty database logs an ERROR.
- `app/api/duffel_webhooks.py` — booking confirmations.

---

## 6. What's needed to successfully launch (checklist)

1. ~~**A2P 10DLC approval**~~ — **DONE.** Campaign approved through Signal House
   (brand AI-Gen Partners / Marq LLC); program number **+1 (629) 225-9067** is live.
2. ~~**A live phone number**~~ — **DONE.**
3. **Production secrets set in Railway:** Signal House API key + webhook secret,
   Anthropic API key, Duffel token, database URL, plus two that are now **required**:
   `ADMIN_API_SECRET` (the data APIs deny every request without it) and
   `FAMILY_SEED_JSON` (the family roster — deliberately not in the repo). And, for
   email, the Gmail address + app password or Resend key.
4. **End-to-end tests on the real number/inbox:** inbound text and email → AI reply;
   STOP/START/HELP; photo message; web consent form; a family-member enrollment.
5. **Set `FAMILY_SEED_JSON` in Railway** (the family roster as JSON — it is
   deliberately not in the repo). Then loading is automatic. The roster (members, birthdays,
   kids) loads on every boot from the `FAMILY_SEED_JSON` variable; the load is idempotent,
   so a redeploy or a database reset self-heals. Check it with
   `GET /health/data`. Set `SEED_FAMILY_ON_STARTUP=false` to turn it off.
6. **Optional, when ready:** turn on `enable_flight_booking` and complete a Duffel
   test-mode booking before exposing it to Cordia.

---

## 7. Feature-flagged / not yet live

- **Flight booking** (hosted Duffel checkout link) is **built** but gated off
  (`enable_flight_booking=False`) until it's tested end-to-end in Duffel's test mode.
  Search and price-watch are fully live; only the "actually book it" step is gated.
- **Outbound drafting/sending** is **built** but gated off (`enable_outbound=False`)
  until the draft→approve→send flow is verified in staging. Flip the flag to enable
  the contact book and outbound tools.
- **Naples house inbox** activates automatically once its Gmail address + app password
  are set in Railway.
- **WhatsApp** is planned as a fast-follow (needs Meta Business verification).
