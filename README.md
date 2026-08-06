# IT System Health Dashboard (Telegram-driven)

Polls the Telegram group/channel that receives your SMS-to-Telegram health
alerts, tracks up/down/unknown status for 23 devices/links across your 10
standalone networks, emails you when something goes down, and publishes a
self-contained status dashboard on GitHub Pages.

**Nothing about your monitored networks needs internet access.** Only this
repo (running on GitHub's servers) talks to Telegram — the standalone
networks keep sending SMS → Telegram exactly as they do today.

## How it works

- `scripts/sync.py` runs every ~15 minutes (see `.github/workflows/heartbeat-sync.yml`),
  polls Telegram's `getUpdates`, matches each message against the known
  devices, updates `state.json`, and re-renders `docs/index.html`.
- `docs/index.html` is the dashboard itself — plain HTML/CSS/JS, no build
  step, served directly by GitHub Pages from `/docs`. It re-checks each
  device's up/down status every 60 seconds in the browser, so it stays
  accurate between polls, not just at poll time.
- `state.json` is the persisted source of truth between runs.
- A device is marked **down** if either (a) it goes quiet for longer than
  its configured threshold, or (b) a message matching its pattern explicitly
  contains a down/fail-type word instead of "alive". (a) is the normal path;
  (b) is a bonus safety net in case any of your monitoring tools ever sends
  an explicit down alert.

## Devices currently configured

23 devices across your 10 networks, each with its own confirmed check-in
schedule (hourly, 2x/day, 3x/day, 4x/day, or once daily). `interval_minutes`
is always the **longest** expected gap between two real check-ins — for any
schedule that only checks in during the day (e.g. 08:00/12:00/17:00), that's
the overnight gap, not the ~4-5h daytime gap between checks. Getting this
backwards would cause the dashboard to falsely flag those devices "down"
every single night.

On top of that, `threshold_minutes` currently adds a **flat** buffer, per
your instruction: **+5 min** for Network 10 GW1 (`sp2_sms_gw1`, the one
truly-hourly device, where fast detection matters most), **+10 min** for
every other device:

| Schedule | interval_minutes | buffer | threshold_minutes |
|---|---|---|---|
| Hourly (00:00–23:00) — Network 10 GW1 only | 60 | +5 | 65 |
| 4x/day (00:00/08:00/12:xx/17:xx) | 480 | +10 | 490 |
| 3x/day (08:00/12:00/17:00) | 900 | +10 | 910 |
| 2x/day (~12:0x/17:0x) | 1140 | +10 | 1150 |
| 1x/day | 1440 | +10 | 1450 |

**Worth watching after this goes live:** a flat +10 min buffer is much
tighter than what a once-daily device had before (previously ~3 hours of
slack). If that device's single daily SMS-to-Telegram message ever lands a
little late — telco-side delivery jitter, not an actual outage — it will
now cross the threshold and fire a false "down" alert. If that starts
happening for a specific device, the fix is to widen just that device's
`threshold_minutes` in `scripts/sync.py`, not to touch the others.

If a network's own monitoring tool ever changes its polling schedule, update
that device's `interval_minutes` first (longest expected gap, not the
average), then re-apply the buffer on top.

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
(regex matched against incoming message text), `interval_minutes`, and
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
- **A device flaps between up/down with no real change, or "down" clears
  itself on a manual re-run with no new heartbeat** → check the Actions run
  list's **Event** column for gaps in "Scheduled" runs — GitHub can drop
  scheduled ticks under load, which looks like an outage but isn't. This is
  why the schedule polls every 15 minutes (not 30/60) and every device's
  threshold has a jitter buffer over its raw interval — don't tighten
  thresholds to "catch it faster," that makes this more likely, not less.
- **Repo deleted and recreated** → secrets do not carry over; re-add them.

## Want this mirrored into a Cowork/Claude dashboard too?

This GitHub Pages dashboard is meant to stay the source of truth for polling
and alerting either way. If you also want a read-only mirror of the same
status visible inside a Claude/Cowork artifact, ask and it can be set up as
an additional hourly job that only reads `state.json` from this repo — it
would never need your Telegram bot token.

