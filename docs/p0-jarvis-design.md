# Jarvis P0 — Design

## Context

Implements the P0 requirements locked in [`p0-jarvis-requirements.md`](./p0-jarvis-requirements.md):
every weekday at 08:00, fetch unread emails from one personal Gmail account,
filter noise, classify by urgency, draft replies for the top 8 actionable
items, write them as Gmail drafts (never sent), and fire one macOS
notification. User reviews and sends from Gmail.

This is a **single-user, single-host, single-account** P0. The design is
intentionally small. No plugin framework, no DI container, no event bus, no
async workers, no dashboard. We add abstractions only when V1 demands them.

Where future work (V1+) would slot in, we note the seam — but we don't build
it.

---

## End-to-end flow

```
launchd (0 8 * * 1-5)
  │
  └─> python -m jarvis.cli brief
        │
        1. load config            (config.py    → Config dataclass)
        2. open SQLite             (storage.py   → ~/.local/share/jarvis/jarvis.sqlite)
        3. start run trace         (trace.py    → INSERT runs row)
        4. Gmail auth + refresh    (auth.py     → google-auth, Keychain)
        5. list INBOX+UNREAD       (gmail.py    → list_unread)
        6. dedup against seen      (dedup.py    → seen_items lookup)
        7. apply exclusion rules   (filters.py  → List-Unsubscribe, noreply,
                                                  Promo/Social/Spam)
        8. Haiku batch classify    (classify.py → ACTION/FYI/NOISE + urgency)
        9. sort + take top N=8     (brief.py    → urgency_rank)
       10. for each top item:
             a. fetch full thread  (gmail.py    → get_thread)
             b. Sonnet draft reply (draft.py   → uses prompts.py)
             c. create Gmail draft (gmail.py    → drafts.create as reply)
             d. apply label        (gmail.py    → Jarvis/Urgent|Today|Later)
             e. mark seen          (dedup.py    → INSERT seen_items)
             f. check budget       (storage.py  → stop if > $1.00)
       11. close run trace         (trace.py    → UPDATE runs row)
       12. macOS notification      (notify.py   → osascript)
```

On any exception: catch in `brief.py`, mark run as failure in trace, fire failure notification, exit non-zero.

---

## File layout

All new code lives in `src/jarvis/` as a subpackage alongside the existing MCP memory server code in `src/`. No restructure of existing code.

```
src/jarvis/
  __init__.py
  cli.py              entry point: brief | auth gmail | status
  config.py           YAML loader + Config dataclass
  auth.py             OAuth flow, token refresh, macOS Keychain via `keyring`
  gmail.py            thin wrapper over googleapiclient.discovery
  filters.py          exclusion predicates (header-based, sender-based, category-based)
  dedup.py            seen_items table ops (is_seen, mark_seen)
  classify.py         Haiku call returning ClassifiedMessage list
  draft.py            Sonnet call returning DraftBody
  prompts.py          two module-level prompt constants
  notify.py           macOS notification via osascript subprocess
  storage.py          sqlite3 connection + schema init + budget tracking
  trace.py            run row lifecycle (start_run, end_run, record_stage)
  brief.py            orchestrator: the run_morning_brief() function above
  launchd/
    com.jarvis.morning-brief.plist    template, installed by `jarvis install`

tests/jarvis/
  __init__.py
  fixtures/
    sample_emails.json              recorded Gmail message payloads
    classifier_response.json        recorded Haiku output
  test_filters.py                   pure unit, no network
  test_dedup.py                     uses :memory: sqlite
  test_brief_e2e.py                 mocks GmailClient + anthropic client
```

`pyproject.toml` changes:
- Add to `dependencies`: `google-auth`, `google-auth-oauthlib`, `google-api-python-client`, `anthropic`, `pyyaml`, `keyring`
- Add `[project.scripts]`: `jarvis = "jarvis.cli:main"`

---

## Configuration

Single YAML file at `~/.config/jarvis/config.yaml`, loaded into a `Config` dataclass:

```yaml
account:
  email: prabhu@example.com

schedule:
  hour: 8
  weekdays_only: true

limits:
  max_drafts_per_run: 8
  budget_usd_per_run: 1.00
  max_thread_tokens: 20000
  max_email_bytes: 1048576           # 1 MB hard skip

exclusions:
  skip_mailing_lists: true           # List-Unsubscribe / Precedence: bulk
  skip_noreply: true                 # noreply@, no-reply@, mailer-daemon@
  skip_gmail_categories: [PROMOTIONS, SOCIAL, SPAM]
  skip_labels: []                    # user-defined; empty in P0

models:
  classifier: claude-haiku-4-5-20251001
  drafter: claude-sonnet-4-6

notifications:
  on_success: true
  on_failure: true

storage:
  db_path: ~/.local/share/jarvis/jarvis.sqlite
  retention_days: 7                  # trace rows older than this are vacuumed
  trace_body_chars: 500              # truncate stored email bodies
```

Validation is a hand-written check in `config.py` — no schema lib. If a required field is missing, raise with a clear message at startup.

---

## Storage schema

Single SQLite file. No migration framework — schema is created idempotently on first run by `storage.py:init()`.

```sql
CREATE TABLE IF NOT EXISTS seen_items (
  message_id    TEXT PRIMARY KEY,
  thread_id     TEXT NOT NULL,
  content_hash  TEXT NOT NULL,            -- sha256 of body + last-message-date
  drafted_at    TEXT NOT NULL,            -- ISO-8601
  draft_id      TEXT,                     -- Gmail draft ID
  urgency       TEXT                      -- URGENT | TODAY | LATER
);

CREATE TABLE IF NOT EXISTS runs (
  run_id              INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at          TEXT NOT NULL,
  ended_at            TEXT,
  status              TEXT,                -- success | partial | failure
  unread_scanned      INTEGER DEFAULT 0,
  excluded            INTEGER DEFAULT 0,
  classified_action   INTEGER DEFAULT 0,
  drafts_created      INTEGER DEFAULT 0,
  budget_cents_used   INTEGER DEFAULT 0,
  error               TEXT
);

CREATE TABLE IF NOT EXISTS traces (
  run_id      INTEGER NOT NULL,
  message_id  TEXT NOT NULL,
  stage       TEXT NOT NULL,                -- excluded | classified | drafted | skipped
  detail_json TEXT NOT NULL,                -- bodies truncated to 500 chars
  FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_traces_run ON traces(run_id);
```

Dedup key is `(message_id, content_hash)`: if a thread gets new messages, the content_hash changes and we re-process. Replied/archived messages drop out of `UNREAD`, so they naturally don't show up again.

---

## Auth (one-time setup + automatic refresh)

**One-time, by the user:**
1. Create Google Cloud project at console.cloud.google.com.
2. Enable Gmail API.
3. Create OAuth 2.0 Client ID (Desktop app type).
4. Download client_secret.json to `~/.config/jarvis/client_secret.json`.
5. Run `jarvis auth gmail` — this opens a browser for consent, stores the refresh token in macOS Keychain under service `jarvis.gmail` account `<email>`.

**Per-run, automatically (auth.py):**
- Load refresh token from Keychain.
- Call `Credentials.refresh()` if access token is expired.
- On refresh failure: write error to trace, fire macOS notification "Jarvis: Gmail auth expired — run `jarvis auth gmail`", exit non-zero.

OAuth scopes:
- `gmail.readonly` — read inbox, threads
- `gmail.modify` — create drafts, add labels (smallest scope that allows draft creation)
- We do NOT request `gmail.send`.

---

## Gmail client (gmail.py)

Wraps `googleapiclient.discovery.build('gmail', 'v1', ...)`. Public surface:

```python
class GmailClient:
    def list_unread(self) -> list[MessageMeta]:
        # users().messages().list(q='in:inbox is:unread', maxResults=500)
        # returns id, thread_id, header snippets (From, To, Cc, Subject, List-Unsubscribe,
        #   Precedence, Auto-Submitted, Date), gmail category labels, size estimate

    def get_thread(self, thread_id: str) -> Thread:
        # users().threads().get(format='full') → all messages with bodies decoded

    def create_draft_reply(self, thread: Thread, body_text: str) -> str:
        # build RFC 2822 with In-Reply-To, References, To = original From only (not reply-all)
        # users().drafts().create(message=...) — Gmail attaches to the right thread automatically

    def add_label(self, message_id: str, label_name: str) -> None:
        # create Jarvis/{Urgent,Today,Later,Archive-Candidate} labels on first use, cache IDs
        # users().messages().modify(addLabelIds=[label_id])
```

Always reply-to-sender only (never reply-all), so N10 reply-all blunders cannot happen even by hand. Plain text body by default; preserve HTML only if original is HTML-only.

---

## Filters (filters.py)

Pure functions — no I/O. Each takes a `MessageMeta` and returns a `(skip: bool, reason: str)`.

```python
def is_excluded(msg: MessageMeta, cfg: Config) -> tuple[bool, str]:
    if cfg.exclusions.skip_mailing_lists and (
        msg.headers.get('List-Unsubscribe')
        or msg.headers.get('Precedence', '').lower() == 'bulk'
        or msg.headers.get('Auto-Submitted', '').lower() != 'no'
    ):
        return True, 'mailing_list'
    if cfg.exclusions.skip_noreply and _is_noreply(msg.from_addr):
        return True, 'noreply'
    if any(cat in msg.gmail_labels for cat in cfg.exclusions.skip_gmail_categories):
        return True, 'gmail_category'
    if any(lbl in msg.gmail_labels for lbl in cfg.exclusions.skip_labels):
        return True, 'user_label'
    if msg.size_estimate > cfg.limits.max_email_bytes:
        return True, 'too_large'
    if _is_phishing_suspect(msg):                  # spam label OR display-name spoof
        return True, 'phishing_suspect'
    if _is_cc_only_to_large_group(msg):            # user only on Cc, >5 recipients total
        return True, 'cc_only_distribution'
    return False, ''
```

Each predicate is one short function. Easy to add another rule later, but no AbstractFilterRule.

---

## Classifier (classify.py)

One Anthropic API call: send up to ~50 message summaries (subject + From + Date + first 800 chars + key headers) in a single Haiku batch, get back JSON.

```python
def classify(messages: list[MessageMeta], cfg: Config) -> list[ClassifiedMessage]:
    payload = [
        {"id": m.id, "from": m.from_addr, "subject": m.subject,
         "date": m.date, "snippet": m.snippet[:800],
         "user_in_to": cfg.account.email in m.to_addrs}
        for m in messages
    ]
    response = anthropic.messages.create(
        model=cfg.models.classifier,
        max_tokens=4000,
        system=prompts.CLASSIFIER_PROMPT,
        messages=[{"role": "user", "content": json.dumps(payload)}],
    )
    return [ClassifiedMessage(**item) for item in json.loads(response.content[0].text)]
```

Output schema (the model is told this in the prompt):
```json
[{"id": "msg123", "class": "ACTION", "urgency": "URGENT", "reason": "<1 sentence>"}, ...]
```

If the model returns malformed JSON: log the trace, default the whole batch to FYI (treat as no-action), continue. We don't retry — one bad batch is acceptable; the user will see it next morning.

---

## Drafter (draft.py)

For each top-N action item, fetch the full thread and call Sonnet once. No fan-out, no concurrency — sequential is fine for 8 calls.

```python
def draft_reply(thread: Thread, cfg: Config) -> str:
    context = _truncate_to_budget(thread, cfg.limits.max_thread_tokens)
    response = anthropic.messages.create(
        model=cfg.models.drafter,
        max_tokens=1500,
        system=prompts.DRAFTER_PROMPT,
        messages=[{"role": "user", "content": _format_thread(context)}],
    )
    return response.content[0].text
```

`_truncate_to_budget`: keep latest message verbatim; older messages summarised inline by a quick Haiku pass only if total > token cap. For most threads (< 20k tokens), pass everything verbatim.

---

## Prompts (prompts.py)

Two module-level string constants. Easy to grep, easy to iterate.

```python
CLASSIFIER_PROMPT = """\
You are an email triage assistant for {user_name}.

Classify each email as one of:
  ACTION  — the user needs to reply or take action
  FYI     — informational; the user should be aware but no reply needed
  NOISE   — marketing, automated alerts, mailing-list traffic

For ACTION emails, also assign urgency:
  URGENT  — time-sensitive AND from a person who matters to the user
            (boss, customer, family). Reply needed today.
  TODAY   — should be replied today but not blocking
  LATER   — can wait a day or two

Signals to use:
- Sender importance (one-on-one with a real person ranks higher than a list)
- SLA cues in the body ("by EOD", "urgent", "blocker", "ASAP")
- Whether the user is in To: (higher) vs Cc: (lower)
- Thread age (older replies-needed are more urgent)
- Time-zone overlap (overnight from EU/Asia is normal, not necessarily urgent)

Reply ONLY with a JSON array; no prose. Each item:
  {{"id": "<message_id>", "class": "ACTION|FYI|NOISE",
    "urgency": "URGENT|TODAY|LATER|null", "reason": "<one sentence>"}}
"""

DRAFTER_PROMPT = """\
You are drafting an email reply on behalf of {user_name}. Match the
original sender's tone and formality.

HARD CONSTRAINTS:
1. Use ONLY information present in the thread. Do NOT invent prior
   conversations, shared documents, or facts. If you would need outside
   context, write exactly: "[Jarvis: insufficient context — please draft manually]"
2. Do NOT commit to specific dates or deliverables unless the thread
   itself proposed them. Hedge with "I'll get back to you with timing".
3. Reply ONLY to the original sender (not reply-all).
4. Keep it concise. One purpose per email.
5. End the body with this footer (italicised by the user later):
   --
   Drafted by Jarvis — review before sending.

Output the email body only. No subject line; no greeting boilerplate
beyond what the situation calls for.
"""
```

Iteration is a matter of editing these two strings, dogfooding for a week, and tuning.

---

## Notifications (notify.py)

Shell out to `osascript` — no extra dependency, no app to install.

```python
def notify(title: str, body: str) -> None:
    subprocess.run(
        ["osascript", "-e",
         f'display notification {json.dumps(body)} with title {json.dumps(title)}'],
        check=False,                  # don't fail the run if notif fails
        timeout=5,
    )
```

Success message: `"7 drafts ready · 3 urgent · 47 noise skipped"`.
Failure message: the exception message, prefixed with `"Jarvis failed: "`.

---

## launchd job

Template at `src/jarvis/launchd/com.jarvis.morning-brief.plist`. `jarvis install` writes the rendered plist to `~/Library/LaunchAgents/` and runs `launchctl load`.

Key fields:
- `StartCalendarInterval`: Hour=8, Minute=0, Weekday=1..5
- `RunAtLoad`: true (so a missed schedule fires on next wake)
- `StandardOutPath` / `StandardErrorPath`: `~/Library/Logs/Jarvis/morning-brief.log`
- `ProgramArguments`: `[<python_path>, "-m", "jarvis.cli", "brief"]`

`jarvis install` is the only "infrastructure" command — the rest is a normal Python package.

---

## CLI (cli.py)

`argparse`-based, no Click/Typer (one fewer dependency for three commands).

```
jarvis brief              run one morning-brief cycle now (also what launchd calls)
jarvis brief --dry-run    fetch + classify, but do NOT create drafts or modify Gmail
jarvis auth gmail         run the OAuth flow, store refresh token in Keychain
jarvis status             show last run summary from runs.sqlite
jarvis install            write launchd plist, launchctl load
jarvis uninstall          launchctl unload, remove plist
```

---

## Guardrail → mechanism mapping (back to negative use cases)

| N# | Risk | Implementation |
|----|------|----------------|
| N1 | Sensitive content | `exclusions.skip_labels` in config; `filters.is_excluded` checks |
| N2 | Email → Claude API | Documented in README; consent prompted on first `auth gmail` |
| N3 | Hallucination | DRAFTER_PROMPT hard constraints; fallback string "insufficient context" |
| N4 | Bot/list replies | Header heuristics in `filters.py` (List-Unsubscribe, Precedence, Auto-Submitted, noreply patterns) |
| N5 | Misprioritization | CLASSIFIER_PROMPT signal list; trace rows show ranking for tuning |
| N6 | Re-processing | `seen_items` table with (message_id, content_hash) key |
| N7 | Token expiry | `auth.py` refresh + failure notification |
| N8 | Long threads | `_truncate_to_budget` + 1MB hard skip in `filters.py` |
| N9 | Quoting/format | `create_draft_reply` uses Gmail's reply API (preserves quoting); plain-text default |
| N10 | Reply-all | Never; `create_draft_reply` always reply-to-sender. No `gmail.send` scope ever requested |
| N11 | Promo flood | `skip_gmail_categories: [PROMOTIONS, SOCIAL, SPAM]`; trace footer in notif |
| N12 | Multi-account | Config holds one `account` object (no list) |
| N13 | Offline | `RunAtLoad: true`; failure notif on any exception; `jarvis status` |
| N14 | Phishing | `_is_phishing_suspect` in filters; skip on SPAM label or display-name spoof |
| N15 | Tone | DRAFTER_PROMPT "match the original sender's tone" |
| N16 | Retention | `storage.trace_body_chars: 500`; nightly vacuum of trace rows older than `retention_days` |
| N17 | Cost | Two-stage model split; per-run `budget_cents_used` check before each Sonnet call |
| N18 | Wrong unread | Gmail query `in:inbox is:unread` + seen_items dedup |

---

## Dependencies added

Minimal:
- `google-auth`, `google-auth-oauthlib`, `google-api-python-client` — Gmail
- `anthropic` — Claude
- `pyyaml` — config
- `keyring` — macOS Keychain (refresh token storage)

Stdlib does the rest: `sqlite3`, `argparse`, `subprocess`, `json`, `hashlib`, `dataclasses`, `pathlib`.

---

## Verification

**Manual end-to-end (the only true test):**
1. `pip install -e .` from repo root.
2. Create `~/.config/jarvis/config.yaml` from the example.
3. Drop Google OAuth client_secret.json at the configured path.
4. `jarvis auth gmail` → consent in browser → see "Token stored."
5. `jarvis brief --dry-run` → logs to stdout, no Gmail writes. Verify counts look sane.
6. `jarvis brief` → drafts appear in Gmail Drafts folder within ~3 min; labels (`Jarvis/Urgent` etc.) on the originals; macOS notification fires.
7. `jarvis brief` again immediately → second run creates zero new drafts (dedup works); notif says "0 new drafts".
8. Reply to one of Jarvis's drafts manually → mark another as read → run `jarvis brief` again → those are skipped because no longer `UNREAD`.
9. `jarvis status` → shows last 3 run summaries.
10. `jarvis install` → restart Mac, wait for next 08:00, verify run fired (or test by editing the plist to fire 2 minutes in the future).

**Unit tests (tests/jarvis/):**
- `test_filters.py` — table-driven: 12 sample MessageMeta inputs, expected (skip, reason) outputs. Covers all header/sender/category rules.
- `test_dedup.py` — in-memory SQLite; insert, check, hash change → re-process.
- `test_config.py` — load valid + invalid YAML; assert dataclass shape; assert clear errors.

**Integration test (tests/jarvis/test_brief_e2e.py):**
- Mock `GmailClient` with recorded JSON fixtures of 12 messages (mix of newsletters, noreply, real personal emails, one thread with multiple messages).
- Mock `anthropic.messages.create` to return recorded classifier output + recorded draft bodies.
- Run `brief.run_morning_brief(config, mock_gmail, mock_anthropic)`.
- Assert: 7 messages excluded, 5 classified ACTION, 3 drafts created (top N=3 set in test config), seen_items has 3 rows, runs has 1 success row, no exception, notification called once with expected counts.

CI runs the unit + integration tests on every push. Manual end-to-end is a one-time setup the user runs.

---

## What this design does NOT include (deferred to V1+)

| Deferred | Why | Where it would slot in |
|---|---|---|
| MCP memory writes (learning preferences) | V1 explicit | New `memory.py`; `classify.py`/`draft.py` would consume it |
| Multi-account | One user, one inbox now | `Config.account` becomes `list[AccountConfig]`; `brief.py` outer loop |
| Other triggers (webhooks, manual chat) | No use case in P0 | New entry points alongside `jarvis brief` in `cli.py` |
| Other output renderers (markdown brief, dashboard) | Option F covers P0 | New modules parallel to `gmail.py` for output; `brief.py` would call a renderer instead of directly invoking gmail |
| Other skills (Slack, Asana, Calendar) | Out of scope | Each becomes its own `src/jarvis/skills/<name>/` package — but we wait until we have two before extracting a shared abstraction |
| Local-model fallback | V2 if privacy stance changes | Swap `anthropic.messages.create` calls behind a thin function |
| Per-sender allowlists for auto-send | Future, dangerous | Would require deliberate V2 design + per-sender config |
| Concurrent draft generation | 8 drafts × ~5s = 40s; fine sequentially | Trivial to parallelise later via `asyncio.gather` |

The seams above are real but unbuilt. Premature interfaces would lock us into the wrong abstractions before the second consumer exists.

---

## Implementation order

Once approved, work in this order so each step is independently verifiable:

1. `pyproject.toml` deps + `[project.scripts]` entry; empty `src/jarvis/__init__.py`; verify `jarvis` command is on PATH.
2. `config.py` + sample YAML + `test_config.py`.
3. `storage.py` schema init + `test_storage.py` (in-memory SQLite).
4. `filters.py` + `test_filters.py` (no Gmail dependency; uses fixture dicts).
5. `auth.py` + `gmail.py` thin wrapper. Manual smoke: `python -c "from jarvis.gmail import GmailClient; print(GmailClient.authenticate().list_unread()[:3])"`.
6. `prompts.py` + `classify.py` + `draft.py`. Manual smoke against a small recorded fixture set.
7. `notify.py` (5 lines) — verify a notification appears.
8. `brief.py` orchestrator + `test_brief_e2e.py` integration test with mocks.
9. `cli.py` — wire `brief`, `auth gmail`, `status`, `brief --dry-run`.
10. `launchd/com.jarvis.morning-brief.plist` + `jarvis install`/`uninstall`.

Each step ends in a green test or a working manual smoke. After step 9 the system is usable manually; after step 10 it runs unattended.
