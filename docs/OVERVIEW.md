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

**By text:** Cordia texts the Cordia AI number → Twilio (the SMS provider) forwards it
to the app → the app confirms it's really her and really from Twilio → Claude reads the
message plus relevant memory and recent history, optionally uses a "tool" (e.g. search
flights), and texts back. She can even send a **photo** and the assistant can understand
it.

**By email:** she (or an invited family member) can email the assistant; it reads new
mail, replies by email, and keeps the same conversation going.

**Family circle:** when Cordia grants a family member access, she shares a consent link;
once they sign it and text in, they can contribute — but they get a **restricted** set
of abilities and never see her private data.

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

**Status:** carrier registration is in review. Getting it approved (or a Toll-Free
number verified as a faster fallback) is the #1 thing standing between us and go-live.

---

## 5. Technical appendix (for the developer)

**Stack**
- Python + **FastAPI**; **PostgreSQL** via async SQLAlchemy; **Alembic** migrations
  (through `005_flight_bookings`); **APScheduler** for recurring jobs.
- **SMS/MMS:** Twilio. Inbound webhook `POST /webhook/sms`, Twilio signature verified.
  Sender allow-list = Cordia + a test number; family numbers resolved via the DB.
- **Email:** two-way. Gmail (IMAP poll every ~120s + SMTP send) or Resend (inbound
  webhook + API send). See `app/services/email_inbound.py`, `app/scheduler/jobs/email_poll.py`.
- **AI:** Anthropic Claude (`claude-sonnet-4-6`). Per-conversation history, proactive
  memory injection, agentic tool loop (≤10 steps), prompt caching, image/vision support.
  Separate, **restricted** system prompt + toolset for family-circle members
  (`sender_role="family"`).
- **Flights:** Duffel API (`search_flights`, `get_offer`, `create_link_session`,
  `get_order`); booking webhook `POST /webhook/duffel` (HMAC-verified) + `/booking/*`
  landing pages.
- **Hosting:** Railway. All secrets/config via environment variables.

**Feature flags (`app/config.py`)**
- `enable_flight_search` (on) · `enable_lease_review` (on) · `enable_family_coordination`
  (on) · `enable_email` (on) · `enable_flight_booking` (**off** until tested).

**Scheduled jobs (`app/scheduler/scheduler.py`)** — flight monitor (hourly), lease
reminders (08:00), birthday reminders (07:30), morning brief (~07:45), birthday prep
(08:15), email poll (~2 min, Gmail only).

**Data model (`app/models/`)** — Users; Conversations/Messages; FamilyMembers/
FamilyEvents/GrandkidActivities; family-circle tables; Trips/FlightWatches/
PriceSnapshots/FlightBookings; Memories; Leases/LeaseClauses/LeaseReminders; plus
`sms_consent` and `consent_submissions`.

**Key files to orient around**
- `app/api/sms.py` — SMS/MMS webhook, keywords, family resolution.
- `app/services/claude_service.py` — the AI conversation loop and role handling.
- `app/tools/` (+ `registry.py`) — every capability the AI can call.
- `app/scheduler/` — the proactive jobs.
- `app/api/compliance.py` — consent / privacy / terms / opt-in pages.
- `app/api/duffel_webhooks.py` — booking confirmations.

---

## 6. What's needed to successfully launch (checklist)

1. **A2P 10DLC approval** (or a Toll-Free number + toll-free verification as the faster
   fallback). Highest priority — most everything else is ready behind it.
2. **A live phone number owned by the correct Twilio account/brand** to send/receive on.
3. **Production secrets set in Railway:** Twilio credentials, Anthropic API key, Duffel
   token, database URL, and (for email) the Gmail address + app password or Resend key.
4. **End-to-end tests on the real number/inbox:** inbound text and email → AI reply;
   STOP/START/HELP; photo message; web consent form; a family-member enrollment.
5. **Load Cordia's family data** (members, birthdays, kids) so the proactive features
   have real data to act on.
6. **Optional, when ready:** turn on `enable_flight_booking` and complete a Duffel
   test-mode booking before exposing it to Cordia.

---

## 7. Feature-flagged / not yet live

- **Flight booking** (hosted Duffel checkout link) is **built** but gated off
  (`enable_flight_booking=False`) until it's tested end-to-end in Duffel's test mode.
  Search and price-watch are fully live; only the "actually book it" step is gated.
