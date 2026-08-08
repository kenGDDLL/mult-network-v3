# IT System Health Dashboard (Telegram-driven)

Polls the Telegram group/channel that receives your SMS-to-Telegram health
alerts, tracks up/down/unknown status for 23 devices/links across your 10
standalone networks, emails you when something goes down, and publishes a
self-contained status dashboard on GitHub Pages.

**Nothing about your monitored networks needs internet access.** Only this
repo (running on GitHub's servers) talks to Telegram — the standalone
networks keep sending SMS → Telegram exactly as they do today.

## How it works

- `scripts/sync.py` polls Telegram's `getUpdates`, matches each message
  against the known devices, updates `state.json`, and re-renders
  `docs/index.html`. It's meant to run every ~15 minutes, triggered by an
  **external** scheduler (cron-job.org) calling GitHub's API — see
  "Reliable 15-minute triggering" below for why GitHub's own built-in
  `schedule:` trigger isn't used for this and how to set up the external
  one, which is a required step, not optional polish.
- `docs/index.html` is the dashboard itself — plain HTML/CSS/JS, no build
  step, served directly by GitHub Pages from `/docs`. It re-checks each
  device's up/down status every 60 seconds in the browser, so it stays
  accurate between polls, not just at poll time.
- The card view has two layers. A compact clickable pill strip at the top —
  one per network, colored by its worst device status — gives you the full
  picture across all 10 networks in one glance. Below that, devices are
  grouped back into per-network **panels** (not the old one-full-width-
  section-per-network layout, which made a 23-device list scroll a lot for
  what it showed) laid out several panels per row, so a network with only
  1-2 devices doesn't cost a whole row. Panels with a Down device sort to
  the very front, then panels with an Unknown device, then everything else
  in its normal Network 1→10 order — so problems are visible almost
  immediately without scrolling. Clicking a pill filters the panels down to
  just that network; click it again to clear.
- `state.json` is the persisted source of truth between runs.
- A device is marked **down** if either (a) it goes quiet for longer than
  its configured threshold, or (b) a message matching its pattern explicitly
  contains a down/fail-type word instead of "alive". (a) is the normal path;
  (b) is a bonus safety net in case any of your monitoring tools ever sends
  an explicit down alert.
- **"Last heartbeat" is the time Telegram recorded the message itself**
  (its own `date` field), not the time this script happened to process it.
  This matters because a poll run can catch several hours' worth of
  backlogged messages at once (e.g. after a manual "Run workflow", or a
  delayed scheduled tick) — using processing time would make every device
  matched in that one run show the same "last heartbeat" regardless of what
  time its actual alert said, which is misleading when checked against the
  "Expected heartbeat" column on the dashboard.

## Devices currently configured

23 devices across your 10 networks, each with its own confirmed check-in
schedule (hourly, 2x/day, 3x/day, 4x/day, or once daily), shown on the
dashboard under a new **"Expected heartbeat"** column/row so you can
sanity-check "Last heartbeat" against when a device was actually supposed
to check in (e.g. `SDC_A_SQ01` → "08:00, 12:00, 17:00 daily").

`interval_minutes` is always the **longest** expected gap between two real
check-ins — for any schedule that only checks in during the day (e.g.
08:00/12:00/17:00), that's the overnight gap, not the ~4-5h daytime gap
between checks. Getting this backwards would cause the dashboard to falsely
flag those devices "down" every single night.

On top of that, `threshold_minutes` currently adds a **flat +5 min buffer
for every device**, per your latest instruction:

| Schedule | interval_minutes | buffer | threshold_minutes |
|---|---|---|---|
| Hourly (00:00–23:00) — Network 10 GW1 | 60 | +5 | 65 |
| 4x/day (00:00/08:00/12:xx/17:xx) | 480 | +5 | 485 |
| 3x/day (08:00/12:00/17:00) | 900 | +5 | 905 |
| 2x/day (~12:0x/17:0x) | 1140 | +5 | 1145 |
| 1x/day | 1440 | +5 | 1445 |

**Worth watching closely after this goes live:** a flat +5 min buffer is
*very* tight, especially for once-daily devices (previously ~3 hours of
slack, now 5 minutes). Even with "Last heartbeat" now reflecting Telegram's
own message time rather than poll time, the underlying SMS-to-Telegram
gateway can still legitimately deliver a message a few minutes later than
usual on any given day — telco-side delay, not an actual outage. With only
a 5-minute margin, that alone is enough to cross the threshold and fire a
false "down" alert, most likely on the once-daily devices first. If a
specific device starts flapping down/up without a real cause, the fix is to
widen just that device's `threshold_minutes` in `scripts/sync.py`, not to
touch the others.

If a network's own monitoring tool ever changes its polling schedule, update
that device's `interval_minutes` and `expected_schedule` first (longest
expected gap, not the average), then re-apply the +5 min buffer on top.

## One-time setup

1. **Create the repo.** Public, unless you're on a GitHub plan that allows
   Pages on private repos.

2. **Create a dedicated *reading* bot.** If the same bot that posts your SMS
   alerts into the chat is the one you'd poll with, this will silently show
   zero results forever — Telegram never delivers a bot's own messages back
   to itself via `getUpdates`. Create a **second, separate bot** via
   [@BotFather](https://t.me/BotFather) purely for reading, and add it to
   the same Telegram group/channel as **admin** (required for channels;
   for groups, either admin or turn off Group Privacy under `/mybots` → Bot
   Settings). Get its token from BotFather and the chat ID (e.g. via
   `https://api.telegram.org/bot<token>/getUpdates` after sending a test
   message — look for `"chat":{"id": ...}`).

3. **Add repository secrets.** Settings → Secrets and variables → Actions →
   "New repository secret":
   - `TELEGRAM_BOT_TOKEN` — the reading bot's token
   - `TELEGRAM_CHAT_ID` — the chat's id (negative, usually starts with
     `-100` for supergroups/channels)
   - Optional, for email alerts: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
     `SMTP_PASS`, `ALERT_EMAIL_FROM`, `ALERT_EMAIL_TO`. Without these,
     down-alerts just print to the workflow run's log instead of emailing.

4. **Enable GitHub Pages.** Settings → Pages → under "Build and deployment",
   Source: "Deploy from a branch" → Branch: `main`, folder: `/docs` → Save.
   The public URL (`https://<owner>.github.io/<repo>/`) appears a minute or
   two after the first successful deployment.

5. **Check Actions permissions.** Settings → Actions → General:
   - "Actions permissions" should allow this repo's workflows to run.
   - "Workflow permissions" must be "Read and write permissions" — the sync
     job commits `state.json`/`docs/index.html` back to `main` every run.
     (An org-level policy can silently override this — if pushes fail with
     a permissions error even though this looks right, check org Actions
     settings too.)

6. **Trigger the first run manually.** Actions tab → "Telegram heartbeat
   sync" → "Run workflow" button. Check the run log (click the run → the
   job → expand "Run sync") — you should see "No new heartbeats this cycle."
   or a "Matched heartbeats for: ..." line, not an error.

7. **Set up reliable 15-minute triggering** — see the dedicated section
   below. Without this step, the workflow only fires whenever GitHub's own
   scheduler feels like it (observed in practice: roughly once every ~2
   hours instead of every 15 minutes), which will cause false "down"
   alerts on most devices since the poll itself can't keep up with their
   check-in intervals.

## Reliable 15-minute triggering (via cron-job.org)

**Why this is needed:** GitHub Actions' own `schedule:` cron trigger is
documented as "best effort" and can be delayed under load. In practice,
for a workflow configured to run every 15 minutes, this repo's Actions run
history showed it actually firing roughly once every ~2 hours instead —
every run succeeded, just far less often than configured. That's a
platform-level throttling of frequent schedules, not something fixable by
editing the workflow file's cron expression further. The fix is to trigger
the workflow from *outside* GitHub's own scheduler: an external clock calls
GitHub's REST API to fire `workflow_dispatch` directly, on a real 15-minute
cadence.

1. **Create a GitHub personal access token**, scoped as narrowly as
   possible: GitHub → your profile photo → Settings → Developer settings →
   Personal access tokens → Fine-grained tokens → "Generate new token".
   - Resource owner: your account/org.
   - Repository access: "Only select repositories" → pick this repo only.
   - Permissions → Repository permissions → "Actions" → set to
     **Read and write**. Leave everything else at no access.
   - Set an expiration (fine-grained tokens require one — e.g. 1 year, and
     put a reminder to rotate it before then).
   - Generate, and **copy the token immediately** — GitHub only shows it once.

2. **Create a cron-job.org job** pointing at GitHub's workflow-dispatch API:
   - URL: `https://api.github.com/repos/<owner>/<repo>/actions/workflows/heartbeat-sync.yml/dispatches`
     (replace `<owner>/<repo>` with your actual GitHub path)
   - Request method: `POST`
   - Request headers (in the job's "Advanced" / headers section):
     - `Authorization: Bearer <your token from step 1>`
     - `Accept: application/vnd.github+json`
     - `Content-Type: application/json`
   - Request body: `{"ref":"main"}`
   - Schedule: every 15 minutes (cron-job.org's own scheduler is a
     dedicated triggering service, not subject to the same throttling as
     GitHub's Actions-internal one)

3. **Test it manually** using cron-job.org's "Execute now" / test-run
   button before trusting the schedule. A successful call returns HTTP
   `204 No Content` with an empty body. If you get `401` the token is
   wrong or lacks the Actions permission; `404` usually means the
   owner/repo/workflow filename in the URL is wrong.

4. **Confirm on GitHub's side**: Actions tab → "Telegram heartbeat sync" →
   the run list should now show new runs every ~15 minutes with
   **Event: workflow_dispatch** (not "Scheduled") — that's the tell that
   the external trigger is the one actually driving it now.

**Security note:** this token can trigger workflow runs on this one repo
and nothing else (scoped to Actions-only, one repo), but it still needs to
live inside cron-job.org's job configuration, which is a third-party
service outside GitHub. Treat it like any other credential: don't reuse it
for anything else, set a real expiration and rotate it, and if this
repo/token combination is ever no longer needed, revoke the token from
GitHub's Developer settings page rather than leaving it live.

GitHub's own `schedule:` trigger is left in the workflow file at a much
lower frequency (every 6 hours) purely as a backup in case cron-job.org has
downtime — it is not the primary mechanism anymore, and shouldn't be relied
on for real detection timing.

### If you're uploading files via GitHub's web interface (not git/GitHub Desktop)

Two easy mistakes to avoid:
- **Upload the *contents* of this folder, not the folder itself.** Dragging
  the whole `telegram-status-dashboard` folder into GitHub's uploader nests
  everything one level too deep and breaks both the workflow and the script.
- **`.github` is a dot-folder — many OS file pickers hide it**, so it may
  silently fail to upload, and GitHub Actions will never find the workflow.
  Check your repo's file listing for a `.github/workflows/heartbeat-sync.yml`
  path. If it's missing, use GitHub's "Create new file" button and type the
  full path `.github/workflows/heartbeat-sync.yml` directly into the
  filename field — GitHub creates the folder structure for you, dot and all.

If you're comfortable with git or GitHub Desktop, use that instead — it
sidesteps both issues entirely.

## Adjusting devices, networks, or thresholds

Edit the `DEVICES` list at the top of `scripts/sync.py` — each entry has its
own `label`, `network` (used for grouping on the dashboard), `pattern`
(regex matched against incoming message text), `expected_schedule` (the
human-readable reference shown on the dashboard), `interval_minutes`, and
`threshold_minutes`. Commit and push; the next run picks up the change
automatically. Adding a brand-new device later is just appending a new entry
to that list.

## Troubleshooting

- **Zero results forever, no error** → almost always the same-bot issue
  above. Confirm you're using the dedicated reading bot's token.
- **Bot just became admin, still zero results** → Telegram only delivers
  updates from the moment a bot becomes admin onward; send a fresh test
  message after confirming admin status.
- **Dashboard shows stale data even though `state.json` looks updated** →
  almost always browser/CDN caching on the Pages URL; hard-refresh.
- **A device flaps between up/down with no real change, "down" clears
  itself on a manual re-run with no new heartbeat, or the dashboard's
  "Last poll" subtitle is stale by hours** → check the Actions run list's
  **Event** column. If recent runs say "Scheduled" and are landing far less
  often than every 15 minutes (this repo was observed running roughly once
  every ~2 hours despite being configured for 15), GitHub's own scheduler
  is throttling it — see "Reliable 15-minute triggering" above; this is the
  most likely cause of stale data if that step hasn't been set up yet. If
  runs say "workflow_dispatch" and are landing every 15 minutes as
  expected, the external trigger is working correctly and any remaining
  down alerts reflect real elapsed time against the device's threshold.
- **Repo deleted and recreated** → secrets do not carry over; re-add them.

## Want this mirrored into a Cowork/Claude dashboard too?

This GitHub Pages dashboard is meant to stay the source of truth for polling
and alerting either way. If you also want a read-only mirror of the same
status visible inside a Claude/Cowork artifact, ask and it can be set up as
an additional hourly job that only reads `state.json` from this repo — it
would never need your Telegram bot token.

