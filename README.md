# Multi-network status dashboard (Telegram-driven)

Tracks 21 heartbeat feeds across 10 networks, each with its own message
wording and its own down-threshold (unlike a single "same phrase for every
device" setup, these feeds genuinely say different things). Polls a single
Telegram chat, updates `state.json`, re-renders `docs/index.html`, and emails
on down-transitions for every feed except the marked standby site.

## Read this first: you need a SECOND bot

You confirmed the bot that will *read* this chat is the same bot that the
SMS-to-Telegram gateway / NMS uses to *post* these alerts. **That will not
work.** Telegram never delivers a bot's own sent messages back to itself
through `getUpdates` -- if `TELEGRAM_BOT_TOKEN` below is set to the posting
bot's token, `scripts/sync.py` will run successfully on schedule forever,
with zero errors, and simply never see a single one of these 21 feeds
(`lastUpdateId` will stay stuck at 0 in `state.json`).

**Before doing anything else:**
1. Create a brand-new bot via `@BotFather` in Telegram (`/newbot`) -- purely
   for reading, separate from whatever posts the alerts.
2. Add the new reader bot to the same chat as an admin (required for
   channels; groups can instead disable privacy mode via `/mybots` -> Bot
   Settings -> Group Privacy -> Turn off).
3. Use the reader bot's token as `TELEGRAM_BOT_TOKEN` below -- never the
   posting bot's token.
4. Send (or wait for) a fresh heartbeat *after* the reader bot is confirmed
   admin -- Telegram only delivers updates from the moment admin status is
   granted onward; earlier messages don't retroactively appear.

## The 21 feeds being tracked

Threshold is per-feed because check-in schedules differ across networks --
some are fixed daily clock times, some are hourly, and several are still
placeholders because only one sample message was available when this repo
was generated.

| Feed id | Label | Schedule | Threshold | Status |
|---|---|---|---|---|
| N1_CloudA_SQ_SP1 | Cloud A - SQ (SP1) | 08:00 / 12:00 / 17:00 daily | 16.5h | Confirmed |
| N1_CloudA_WUG_SP1_CCG | Cloud A - WUG to CCG (SP1) | assumed same as SQ sibling | 16.5h | **Assumed** -- confirm |
| N1_CloudA_WUG_HQ_SGCCG | Cloud A - WUG to SGCCG (HQ, default NMS routing) | unknown | 26.5h placeholder | **Unconfirmed** |
| N1_CloudA_WUG_HQ_SP1SQ | Cloud A - WUG to SP1SQ (HQ, ITI001) | 00:00 / 08:00 / 12:00 / 17:05 daily | 9h | Confirmed |
| N2_CloudB_SQ_SP1 | Cloud B - SQ (SP1) | 08:00 / 12:00 / 17:00 daily | 16.5h | Confirmed |
| N2_CloudB_WUG_SP1_SQ | Cloud B - WUG to SQ (SP1, ITI001) | unknown | 26.5h placeholder | **Unconfirmed** |
| N3_SMv2_WUG_SP1_Active | SM v2 WUG - Active site (SP1) | 00:00 / 08:00 / 12:12 / 17:00 daily | 9h | Confirmed |
| N3_SMv1_WUG_HQ_Standby | SM v1 WUG - Standby site (HQ) | unknown | 26.5h placeholder | **Unconfirmed** -- alerting off |
| N4_CA_WUG_SP1_SQ | CA WUG (SP1, SQ) | unknown | 26.5h placeholder | **Unconfirmed** |
| N5_OV_SQ_HQ_GW1 | OV SQ - HQ Gateway 1 | unknown | 26.5h placeholder | **Unconfirmed** |
| N5_OV_SQ_HQ_GW2 | OV SQ - HQ Gateway 2 | unknown | 26.5h placeholder | **Unconfirmed** |
| N5_OV_WUG_HQ_SQ | OV WUG (HQ, SQ) | unknown | 26.5h placeholder | **Unconfirmed** |
| N6_VG_WUG_HQ_GW1 | VG WUG - HQ Gateway 1 | unknown | 26.5h placeholder | **Unconfirmed** |
| N6_VG_WUG_HQ_GW2 | VG WUG - HQ Gateway 2 | unknown | 26.5h placeholder | **Unconfirmed** |
| N7_CE_WUG_HQ_GW1 | CE WUG - HQ Gateway 1 | unknown | 26.5h placeholder | **Unconfirmed** |
| N7_CE_WUG_HQ_GW2 | CE WUG - HQ Gateway 2 | unknown | 26.5h placeholder | **Unconfirmed** |
| N8_MG_WUG_SP1_SQ | MG WUG (SP1, SQ) | unknown | 26.5h placeholder | **Unconfirmed** |
| N9_FS1_WUG_SP1_CCG | FS-1 WUG (SP1, CCG) | unknown | 26.5h placeholder | **Unconfirmed** |
| N10_X_SQ_SP2_GW1 | X SQ - SP2 Gateway 1 | hourly (stated in the alert text) | 75m | Confirmed |
| N10_X_SQ_SP2_GW2 | X SQ - SP2 Gateway 2 | hourly | 75m | Confirmed |
| N10_X_SQ_BV_GW2 | X SQ - BV Gateway 2 | hourly | 75m | Confirmed |

**13 of the 21 feeds are running on a conservative 26.5-hour placeholder**
because only one sample message was seen for them -- this avoids false
"down" alarms until the real cadence is known, at the cost of being slow to
actually detect a real outage on those feeds. As soon as you know each one's
real check-in schedule, update its `threshold_minutes` (and set
`"confirmed": True`) at the top of `scripts/sync.py`, commit, and push -- no
other changes needed.

The standby site (`N3_SMv1_WUG_HQ_Standby`) has `alertable: False` -- it
still shows its status on the dashboard, but won't trigger a down email,
since it's expected to be quiet most of the time.

## One-time setup

1. **Create the repo.** Public, unless you're on a GitHub plan that allows
   Pages on private repos.

2. **Create the reader bot and add it to the chat as admin.** See "Read this
   first" above -- do this before adding secrets.

3. **Add repository secrets.** Settings -> Secrets and variables -> Actions:
   - `TELEGRAM_BOT_TOKEN` -- the reader bot's token.
   - `TELEGRAM_CHAT_ID` -- the single chat all 21 feeds post into.
   - Optional, for email alerts: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
     `SMTP_PASS`, `ALERT_EMAIL_FROM`, `ALERT_EMAIL_TO`. Without these, alerts
     just print to the workflow log instead of emailing.

4. **Enable GitHub Pages.** Settings -> Pages -> Build and deployment ->
   Source: "Deploy from a branch" -> Branch: `main`, folder: `/docs`.

5. **Check both Actions permission settings.** Settings -> Actions ->
   General: "Actions permissions" should allow workflows to run; "Workflow
   permissions" should be "Read and write permissions" (the sync job commits
   `state.json`/`docs/index.html` back every run). An org-level policy can
   override the repo-level setting -- check that too if pushes fail.

6. **Trigger the first run manually.** Actions tab -> "Multi-network
   heartbeat sync" -> "Run workflow". Check the log -- you should see either
   "No new heartbeats this cycle." or "Matched heartbeats for: ...".

## Re-testing the parser after any wording change

If any of these networks' NMS/gateway ever changes its exact wording,
`scripts/test_patterns.py` has all 32 real sample messages this repo was
built from, each asserted against its expected feed id with zero
cross-matches. Run it after any pattern edit:

```
python3 scripts/test_patterns.py
```

A `FAIL` line means either a pattern stopped matching its own feed, or (more
dangerous) started matching a different feed's messages -- both are worth
fixing before pushing.

## Troubleshooting

Same failure modes as any deployment from the `telegram-status-dashboard`
skill -- zero results forever (check the same-bot issue first), a device
stuck on "Unknown" (check its regex against a real message with
`test_patterns.py`), stale-looking dashboard data (hard refresh, it's Pages
caching), or a device flapping down/up with no real outage (check the
Actions run list's Event column for dropped scheduled ticks). See the
skill's `references/github-setup-checklist.md` for the full list.
