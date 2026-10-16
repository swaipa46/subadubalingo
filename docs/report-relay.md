# Error report relay (swaipa.dk)

SubaDubaLingo sends error reports to a small relay on swaipa.dk. The relay turns them into GitHub Issues in a
**private** repo. The app holds no secret: the GitHub token lives only on the server. This file is the contract
between the app (`app/subadubalingo/report.py`) and the relay, which is built and run on the swaipa.dk box.

## Why a relay

- Reports must not go in the public code repo. Even redacted, a log shows what people work on, such as video titles
  from customer meetings.
- A token in the app would be public: anyone can download the exe from the public releases and pull it out. GitHub has
  no write-only token, so a leaked token could also read every report.
- The relay keeps the token server-side, validates and limits what comes in, and only ever creates issues and comments.

## Endpoint

```
POST https://swaipa.dk/api/subadubalingo/v1/reports
Content-Type: application/json; charset=utf-8
User-Agent: SubaDubaLingo/<version>
```

- Body: one JSON object, at most 256 KB (reject larger with 413).
- Health check: `GET https://swaipa.dk/api/subadubalingo/v1/health` returns 200 `{"ok": true}`.

### Request body (schema 1)

| Field | Type | Notes |
|---|---|---|
| `schema` | int | `1` |
| `report_id` | string | UUID4 made by the app. Idempotency key: the same id twice must not make two issues. |
| `created_at` | string | ISO 8601 UTC, from the user's clock |
| `kind` | string | `crash` (unhandled exception), `error` (a run failed), or `user` (the user reports something without a failure) |
| `fingerprint` | string | 16 hex chars: hash of the error type + the failed step + the first line of the error, without numbers and paths. Same problem = same fingerprint. |
| `title` | string | At most 120 chars, e.g. `Fejl i 1-6 Indtaler på svensk: piper exited with 1` |
| `app_version` | string | e.g. `2.1.0` |
| `os` | string | e.g. `Windows 11 26200` |
| `ui_language` | string | `da`, `sv`, `no` or `en` |
| `theme` | string | `light` or `night` |
| `step` | string | The failed step's key, e.g. `dub`, `transcribe`, `claude`, `cut`, `burn`; empty if none |
| `error` | string | The error text, at most 4,000 chars |
| `comment` | string | The user's comment, at most 4,000 chars; may be empty |
| `run` | object | The run's options and step states (languages, cut on/off, voice mode, which steps were done), no file contents |
| `log_tail` | string | The last lines of the log, at most 200 KB, already redacted by the app |

The app redacts before sending: the user's home folder becomes `~`, the Windows user name becomes `<user>`, and file
names of videos become `<video>.mp4` style placeholders. Subtitle texts, audio and video are never sent.

### Responses

| Status | Body | Meaning for the app |
|---|---|---|
| 201 | `{"status": "created", "ref": 12}` | A new issue was made. The app shows "Tak. Fejlen er sendt (#12)." |
| 200 | `{"status": "added", "ref": 12}` | Same fingerprint as an open issue; a comment was added |
| 200 | `{"status": "duplicate", "ref": 12}` | This `report_id` was already received |
| 400 | `{"error": "..."}` | Invalid body; the app does not retry |
| 413 | | Too large; the app shortens `log_tail` to 50 KB and retries once |
| 429 | `Retry-After` header | Rate limited; the app keeps the report and retries later |
| 5xx / no network | | The app keeps the report and retries at the next start |

Never return the issue URL or anything from GitHub besides the issue number.

## What the relay does

1. Validate the body against schema 1: types, lengths, allowed values. Reject unknown `schema` versions with 400.
2. Redact again as a safety net: Windows paths (`C:\Users\<name>\...`) and e-mail addresses become placeholders.
3. Idempotency: keep the `report_id`s of the last 30 days (a small SQLite or JSON file). A repeat gives `duplicate`.
4. Rate limits: at most 5 reports per IP per hour and 200 per day in total. Over the limit gives 429.
5. Fingerprint: look for an **open** issue with label `fp:<fingerprint>` in the reports repo.
   - Found: add a comment with the version, the OS, the time, the comment, and the log tail inside `<details>`.
   - Not found: create an issue (format below).
6. Log only metadata (time, IP hash, status, ref), never bodies.

### Issue format

- **Repo:** `swaipa46/subadubalingo-reports` (private).
- **Title:** `[<app_version>] <title>`
- **Labels:** create them if missing.
  - `version:<app_version>`
  - `type:<kind>`
  - `step:<step>` (when step is set)
  - `lang:<ui_language>`
  - `fp:<fingerprint>`
- **Body (Markdown):**

```markdown
**Kommentar:** <comment or "(ingen)">

| | |
|---|---|
| Version | 2.1.0 |
| Windows | Windows 11 26200 |
| Sprog / tema | da / night |
| Trin | dub |
| Rapport | <report_id>, <created_at> |

**Fejl**
```
<error>
```

<details><summary>Kørslen</summary>

```json
<run, pretty-printed>
```
</details>

<details><summary>Log (sidste linjer)</summary>

```
<log_tail, cut to fit GitHub's 65,536 character body limit>
```
</details>
```

### GitHub token

- Use a fine-grained personal access token from the swaipa46 account.
- Repository access: only `swaipa46/subadubalingo-reports`.
- Permissions:
  - Issues: Read and write
  - Metadata: Read
- Expiry: 1 year. Put a reminder in the calendar before it runs out.
- Store it only on the server, e.g. in an env file owned by the service user with mode 600. Never commit it.

## The app's side

- The report dialog shows exactly what is sent (the fields above, with the log tail viewable) and sends only when the
  user clicks "Send".
- Reports that could not be sent wait in `~/.subadubalingo/reports/pending/<report_id>.json` and are retried at the next
  start (at most 10 kept; the oldest are dropped).
- The relay URL is a constant in `report.py`. It is not a secret.

## Clarifications (from building the app's side)

- **Sizes:** 256 KB body = 262,144 bytes; 200 KB log tail = 200,000 UTF-8 bytes; the 4,000 and 120 limits are characters.
- **Step keys:** `extract`, `transcribe`, `claude`, `review`, `cut`, `voice` (speaking the dub), `dub` (mixing), `burn`;
  empty when no step failed.
- **`kind: user` fingerprint:** sha256 over `user` + step + the first line of the comment, so different user reports
  do not merge into one issue.
- **Titles:** always Danish ("Fejl i 1-6 Indtaler på svensk: …", "Nedbrud: KeyError: …", "Rapport fra brugeren: …");
  the step name inside is in the user's UI language.
- **`run`:** `{"view", "result", "steps": [{"step", "name", "state"}], "created": [redacted file names], "queue",
  "options"}`. Treat it as opaque JSON: pretty-print it, do not depend on its shape.
- **Other 4xx (401, 404, 405 …) and 5xx:** the app keeps the report and retries at the next start, so a relay that is
  not deployed yet loses nothing. `Retry-After` is ignored; a 429 is also retried at the next start.
- **Crash drafts:** a crash is saved in `~/.subadubalingo/reports/crash/` (at most 3) and only sent when the user
  confirms it in the dialog at the next start.
