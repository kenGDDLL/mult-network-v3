#!/usr/bin/env python3
"""
Telegram heartbeat -> multi-network status dashboard poller.

Built for a Telegram chat that receives SMS-to-Telegram "is alive" heartbeat
alerts from ~10 standalone/air-gapped networks, each with its own message
wording and its own check-in schedule (some hourly, some 3-4x/day, some
daily).

Run this on a schedule (see .github/workflows/heartbeat-sync.yml). Each run:
  1. Polls Telegram's getUpdates for messages newer than the last processed
     update_id.
  2. Matches each message against every device's own regex pattern (a single
     global pattern does not work here -- each network's monitoring tool
     phrases its heartbeat differently) and records a "last seen" timestamp
     for whichever device matched.
  3. If a message matches a device's pattern but does NOT contain "alive"
     and instead contains an explicit down/fail keyword, that device is
     marked down immediately (in addition to the normal silence-based
     detection below).
  4. Recomputes up/down/unknown status for every device purely from elapsed
     time vs that device's OWN threshold_minutes. This happens even if the
     Telegram fetch itself failed, and even if no new message arrived this
     cycle, because a device can transition to "down" with zero new messages
     just by going quiet for too long.
  5. Sends an email alert on any up/unknown -> down transition (not on every
     cycle a device stays down, to avoid repeat noise).
  6. Persists state.json and re-renders docs/index.html.

=====================  CONFIGURE THESE FOR YOUR DEPLOYMENT  =====================
"""

import json
import os
import re
import smtplib
import ssl
import urllib.request
from datetime import datetime, timezone
from email.mime.text import MIMEText

# ---------------------------------------------------------------------------
# 1. Devices to track. One entry per distinct heartbeat line/link you receive.
#
#    - id: short internal key, used in state.json. Don't change once live --
#      renaming an id makes that device look "new" and drops its history.
#    - label: human-readable name shown on the dashboard.
#    - network: which of your standalone networks/links this belongs to,
#      used to group the dashboard.
#    - pattern: regex that uniquely identifies this device's heartbeat line.
#      Verified against your real sample messages -- every sample matched
#      exactly one device with zero cross-matches (see parser test run).
#    - interval_minutes: the LONGEST expected gap between two consecutive
#      real check-ins -- for daytime-only schedules that's the OVERNIGHT
#      gap, not the average daytime gap (e.g. a device that only checks in
#      at 08:00/12:00/17:00 goes quiet for 15 hours every single night).
#      Confirmed against the schedules you provided:
#        hourly (00:00-23:00):             interval=60
#        4x/day (00:00/08:00/12:xx/17:xx): interval=480   (max gap 8h)
#        3x/day (08:00/12:00/17:00):       interval=900   (max gap 15h overnight)
#        2x/day (~12:0x/17:0x):            interval=1140  (max gap ~19h overnight)
#        1x/day:                           interval=1440  (max gap 24h)
#    - threshold_minutes: a FLAT +5 min jitter buffer on top of
#      interval_minutes for every device, per explicit request. This is
#      MUCH tighter than the interval-scaled buffer used originally (which
#      gave once-daily devices ~3 hours of slack to absorb a late
#      SMS-to-Telegram gateway delivery) -- with only +5 min, a message
#      that's a few minutes late for reasons that have nothing to do with
#      an actual outage (telco delay, gateway backlog) can now cross the
#      threshold and fire a false "down" alert. Now that lastSeenIso is
#      Telegram's own message timestamp rather than poll-processing time
#      (see message_seen_iso() below), the buffer only needs to cover real
#      delivery jitter, not polling-cycle timing -- but +5 min is still
#      tight for once-daily devices. If a specific device starts flapping
#      down/up without a real outage, widen just that device's
#      threshold_minutes here, rather than touching the others.
#    - expected_schedule: a human-readable reference string shown on the
#      dashboard next to each device (e.g. "08:00, 12:00, 17:00 daily") so
#      you can sanity-check "Last heartbeat" against when it was actually
#      supposed to check in.
# ---------------------------------------------------------------------------
DEVICES = [
    {
        "id": "sdc_a_sq01", "label": "SDC_A_SQ01",
        "network": "Network 1 – Cloud A SQ at SP1",
        "pattern": re.compile(r"SDC_A_SQ01", re.I),
        "expected_schedule": "08:00, 12:00, 17:00 daily",
        "interval_minutes": 900, "threshold_minutes": 905,  # 08:00, 12:00, 17:00 daily
    },
    {
        "id": "sp1_ccg_a", "label": "SP CCG",
        "network": "Network 1 – Cloud A WUG at SP1 > SP1_CCG",
        "pattern": re.compile(r"\bSP\s+CCG\s+is\s+alive", re.I),
        "expected_schedule": "~12:00, 17:00 daily",
        "interval_minutes": 1140, "threshold_minutes": 1145,  # ~12:00, 17:00 daily
    },
    {
        "id": "sg_ccg_via_sp", "label": "SG CCG via SP",
        "network": "Network 1 – Cloud A WUG at HQ > SG_CCG (Default NMS routing)",
        "pattern": re.compile(r"SG\s+CCG\s+via\s+SP", re.I),
        "expected_schedule": "~17:05 daily",
        "interval_minutes": 1440, "threshold_minutes": 1445,  # ~17:05 daily
    },
    {
        "id": "sg_ccg_thru_sq", "label": "SG CCG thru SQ [ITI001]",
        "network": "Network 1 – Cloud A WUG at HQ > SP1_SQ",
        "pattern": re.compile(r"SG\s+CCG\s+is\s+alive\s+thru\s+SQ", re.I),
        "expected_schedule": "00:00, 08:00, 12:00, ~17:05 daily",
        "interval_minutes": 480, "threshold_minutes": 485,  # 00:00, 08:00, 12:00, ~17:05 daily
    },
    {
        "id": "sdc_b_sq01", "label": "SDC_B_SQ01",
        "network": "Network 2 – Cloud B SQ at SP1",
        "pattern": re.compile(r"SDC_B_SQ01", re.I),
        "expected_schedule": "08:00, 12:00, 17:00 daily",
        "interval_minutes": 900, "threshold_minutes": 905,  # 08:00, 12:00, 17:00 daily
    },
    {
        "id": "dsp1_wug", "label": "DSP1 WUG [ITI001]",
        "network": "Network 2 – Cloud B WUG at SP1 > SP1_SQ",
        "pattern": re.compile(r"DSP1\s+WUG", re.I),
        "expected_schedule": "~17:05 daily",
        "interval_minutes": 1440, "threshold_minutes": 1445,  # ~17:05 daily
    },
    {
        "id": "sm_sp1_wug01", "label": "SM_SP1 WUG 01 (Active site)",
        "network": "Network 3 – SM_v2 WUG at SP1 > SP1_SQ",
        "pattern": re.compile(r"SM_SP1\s+WUG\s+01", re.I),
        "expected_schedule": "00:00, 08:00, ~12:12, 17:00 daily",
        "interval_minutes": 480, "threshold_minutes": 485,  # 00:00, 08:00, ~12:12, 17:00 daily
    },
    {
        "id": "sm_wug01", "label": "SM WUG 01 (Standby site)",
        "network": "Network 3 – SM_v1 WUG at HQ > SP1_SQ",
        "pattern": re.compile(r"(?<!_SP1 )\bSM\s+WUG\s+01", re.I),
        "expected_schedule": "~12:12 daily",
        "interval_minutes": 1440, "threshold_minutes": 1445,  # ~12:12 daily
    },
    {
        "id": "ca_wug", "label": "CA WUG",
        "network": "Network 4 – CA WUG at SP1 > SP1_SQ",
        "pattern": re.compile(r"\bCA\s+WUG\s+is\s+alive", re.I),
        "expected_schedule": "~17:00 daily",
        "interval_minutes": 1440, "threshold_minutes": 1445,  # ~17:00 daily
    },
    {
        "id": "ov_sms_gw1", "label": "HQ OV_SMS_GW1",
        "network": "Network 5 – OV SQ GW1 at HQ",
        "pattern": re.compile(r"OV_SMS_GW1", re.I),
        "expected_schedule": "~15:01 daily",
        "interval_minutes": 1440, "threshold_minutes": 1445,  # ~15:01 daily
    },
    {
        "id": "ov_sms_gw2", "label": "HQ OV_SMS_GW2",
        "network": "Network 5 – OV SQ GW2 at HQ",
        "pattern": re.compile(r"OV_SMS_GW2", re.I),
        "expected_schedule": "~15:01 daily",
        "interval_minutes": 1440, "threshold_minutes": 1445,  # ~15:01 daily
    },
    {
        "id": "ov_wug01", "label": "OV WUG 01",
        "network": "Network 5 – OV WUG at HQ > HQ_SQ",
        "pattern": re.compile(r"OV\s+WUG\s+01", re.I),
        "expected_schedule": "~14:02 daily",
        "interval_minutes": 1440, "threshold_minutes": 1445,  # ~14:02 daily
    },
    {
        "id": "vg_sms_gw1", "label": "HQ VG_SMS_GW1",
        "network": "Network 6 – VG WUG GW1 at HQ > HQ_SQ",
        "pattern": re.compile(r"VG_SMS_GW1", re.I),
        "expected_schedule": "~15:01 daily",
        "interval_minutes": 1440, "threshold_minutes": 1445,  # ~15:01 daily
    },
    {
        "id": "vg_sms_gw2", "label": "HQ VG_SMS_GW2",
        "network": "Network 6 – VG WUG GW2 at HQ > HQ_SQ",
        "pattern": re.compile(r"VG_SMS_GW2", re.I),
        "expected_schedule": "~15:01 daily",
        "interval_minutes": 1440, "threshold_minutes": 1445,  # ~15:01 daily
    },
    {
        "id": "ce_wug01", "label": "CE-G WUP 01",
        "network": "Network 7 – CE WUG at HQ > HQ_SQ",
        "pattern": re.compile(r"CE-G\s+WUP\s+01", re.I),
        "expected_schedule": "~10:25 daily",
        "interval_minutes": 1440, "threshold_minutes": 1445,  # ~10:25 daily
    },
    {
        "id": "ce_sms_gw1", "label": "HQ CE-_SMS_GW1",
        "network": "Network 7 – CE SQ GW1 at HQ",
        "pattern": re.compile(r"CE-_SMS_GW1", re.I),
        "expected_schedule": "~15:01 daily",
        "interval_minutes": 1440, "threshold_minutes": 1445,  # ~15:01 daily
    },
    {
        "id": "ce_sms_gw2", "label": "HQ CE-_SMS_GW2",
        "network": "Network 7 – CE SQ GW2 at HQ",
        "pattern": re.compile(r"CE-_SMS_GW2", re.I),
        "expected_schedule": "~15:01 daily",
        "interval_minutes": 1440, "threshold_minutes": 1445,  # ~15:01 daily
    },
    {
        "id": "zs_wup01_ccg", "label": "ZS WUP01 CCG",
        "network": "Network 8 – MG WUG01 at SP1 > SP1_SQ",
        "pattern": re.compile(r"ZS\s+WUP01\s+CCG", re.I),
        "expected_schedule": "~12:00 daily",
        "interval_minutes": 1440, "threshold_minutes": 1445,  # ~12:00 daily
    },
    {
        "id": "zs_wup02_ccg", "label": "ZS WUP02 CCG",
        "network": "Network 8 – MG WUG02 at SP1 > SP1_SQ",
        "pattern": re.compile(r"ZS\s+WUP02\s+CCG", re.I),
        "expected_schedule": "~12:00 daily",
        "interval_minutes": 1440, "threshold_minutes": 1445,  # ~12:00 daily
    },
    {
        "id": "fs1_ccg", "label": "FS-1 CCG",
        "network": "Network 9 – FS-1 WUG at SP1 > SP1_CCG",
        "pattern": re.compile(r"FS-1\s+CCG", re.I),
        "expected_schedule": "~12:00 daily",
        "interval_minutes": 1440, "threshold_minutes": 1445,  # ~12:00 daily
    },
    {
        "id": "sp2_sms_gw1", "label": "[DC] SP2_SMS_GW1",
        "network": "Network 10 – X SQ GW1 at SP2",
        "pattern": re.compile(r"SP2_SMS_GW1", re.I),
        "expected_schedule": "Hourly, 00:00–23:00",
        "interval_minutes": 60, "threshold_minutes": 65,  # Hourly, 00:00–23:00
    },
    {
        "id": "sp2_sms_gw2", "label": "[DC] SP2_SMS_GW2",
        "network": "Network 10 – X SQ GW2 at SP2",
        "pattern": re.compile(r"SP2_SMS_GW2", re.I),
        "expected_schedule": "~17:00 daily",
        "interval_minutes": 1440, "threshold_minutes": 1445,  # ~17:00 daily
    },
    {
        "id": "bv_sms_gw1", "label": "[DC] BV_SMS_GW1",
        "network": "Network 11 – X SQ GW1 at BV",
        "pattern": re.compile(r"BV_SMS_GW1", re.I),
        "expected_schedule": "~12:00 daily",
        "interval_minutes": 1440, "threshold_minutes": 1445,  # ~12:00 daily
    },
    {
        "id": "bv_sms_gw2", "label": "[DC] BV_SMS_GW2",
        "network": "Network 11 – X SQ GW2 at BV",
        "pattern": re.compile(r"BV_SMS_GW2", re.I),
        "expected_schedule": "~17:00 daily",
        "interval_minutes": 1440, "threshold_minutes": 1445,  # ~17:00 daily
    },
]
DEVICE_IDS = [d["id"] for d in DEVICES]
DEVICE_BY_ID = {d["id"]: d for d in DEVICES}

# Words that mean "still fine" vs words that mean "explicitly reported down"
# (in addition to the normal silence/timeout based detection below). None of
# the sample messages seen so far included a down-alert example, so this is
# a best-effort net for whatever wording your monitoring tools use if/when
# they ever send one directly, on top of (never instead of) the timeout logic.
ALIVE_KEYWORDS = re.compile(r"\balive\b", re.I)
DOWN_KEYWORDS = re.compile(
    r"\b(down|dead|fail(?:ed|ure)?|unreachable|not\s+respond(?:ing)?|"
    r"no\s+response|lost|offline|critical)\b",
    re.I,
)

# 4. Where state/output live, relative to the repo root this script runs from.
STATE_PATH = "state.json"
DASHBOARD_PATH = "docs/index.html"

# ===================================================================
# Nothing below this line should normally need editing -- it's the
# correctness-critical plumbing. Read references/sync-logic.md (in the
# skill this was generated from) before touching the status-transition
# logic specifically; regressing it silently breaks down-alerting.
# ===================================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587") or "587")
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
ALERT_EMAIL_FROM = os.environ.get("ALERT_EMAIL_FROM", "")
ALERT_EMAIL_TO = os.environ.get("ALERT_EMAIL_TO", "")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def blank_device_state(d):
    return {
        "label": d["label"],
        "network": d["network"],
        "expectedSchedule": d["expected_schedule"],
        "lastSeenIso": None,
        "lastKnownStatus": "unknown",
        "intervalMinutes": d["interval_minutes"],
        "thresholdMinutes": d["threshold_minutes"],
    }


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r") as f:
            return json.load(f)
    return {
        "devices": {d["id"]: blank_device_state(d) for d in DEVICES},
        "lastPollIso": None,
        "lastPollOk": False,
        "lastPollNote": "Never polled yet.",
        "lastUpdateId": 0,
    }


def save_state(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def fetch_updates(offset):
    url = (
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        f"?offset={offset}&timeout=0"
    )
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API returned ok=false: {payload}")
    return payload["result"]


def extract_message(item):
    # Groups deliver posts as message/edited_message; broadcast Channels
    # deliver them as channel_post/edited_channel_post. Check all four
    # unconditionally rather than assuming which one applies.
    return (
        item.get("message")
        or item.get("edited_message")
        or item.get("channel_post")
        or item.get("edited_channel_post")
    )


def message_seen_iso(msg):
    # Telegram stamps every message with its own "date" (Unix seconds) --
    # this is when the message actually posted to the chat, which is much
    # closer to when the monitored device was actually alive than
    # now_iso() (the time THIS script happened to process it). Using
    # now_iso() here was a real bug: if a poll run catches a backlog of
    # several hours' worth of messages at once (e.g. after a manual
    # workflow_dispatch, or a delayed scheduled run), every device matched
    # in that one run would get stamped with the same processing time
    # instead of each device's own actual heartbeat time -- e.g. a device
    # whose message says "5:05 PM" showing "Last heartbeat: 5:38 PM"
    # because that's just when the script happened to run.
    ts = msg.get("date")
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    return now_iso()  # fallback -- Telegram always sends "date" in practice


def compute_status(dev_state, now_dt):
    last_seen_iso = dev_state.get("lastSeenIso")
    if last_seen_iso is None:
        return "unknown"
    threshold = dev_state.get("thresholdMinutes", 1440)
    last_seen = datetime.fromisoformat(last_seen_iso)
    elapsed_minutes = (now_dt - last_seen).total_seconds() / 60.0
    return "up" if elapsed_minutes <= threshold else "down"


def send_alert(subject, body):
    if not (SMTP_HOST and ALERT_EMAIL_TO):
        print(f"[alert - no SMTP configured, logging only] {subject}\n{body}")
        return
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = ALERT_EMAIL_FROM or SMTP_USER
    msg["To"] = ALERT_EMAIL_TO
    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls(context=context)
        if SMTP_USER:
            server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(msg["From"], [ALERT_EMAIL_TO], msg.as_string())
    print(f"[alert sent] {subject}")


def render_dashboard(state):
    if not os.path.exists(DASHBOARD_PATH):
        print(f"warning: {DASHBOARD_PATH} not found, skipping render")
        return
    with open(DASHBOARD_PATH, "r") as f:
        html = f.read()

    embedded = {
        "devices": {
            d_id: {
                "label": state["devices"][d_id]["label"],
                "network": state["devices"][d_id]["network"],
                "expectedSchedule": state["devices"][d_id]["expectedSchedule"],
                "lastSeenIso": state["devices"][d_id]["lastSeenIso"],
                "intervalMinutes": state["devices"][d_id]["intervalMinutes"],
                "thresholdMinutes": state["devices"][d_id]["thresholdMinutes"],
            }
            for d_id in DEVICE_IDS
        },
        "lastPollIso": state["lastPollIso"],
        "lastPollOk": state["lastPollOk"],
        "lastPollNote": state["lastPollNote"],
    }
    block = (
        '<script type="application/json" id="device-state">'
        + json.dumps(embedded, indent=2)
        + "</script>"
    )
    # Use a replacement FUNCTION, not the raw string -- re.sub interprets
    # backslashes in a string replacement (e.g. \1, \g<name>), and
    # json.dumps' default ensure_ascii=True escapes non-ASCII characters
    # (the en/em dashes in network labels below) as literal "–"-style
    # sequences, which re.sub would otherwise try to parse as an invalid
    # backreference and crash with "bad escape \u".
    new_html = re.sub(
        r'<script type="application/json" id="device-state">.*?</script>',
        lambda _match: block,
        html,
        flags=re.DOTALL,
    )
    with open(DASHBOARD_PATH, "w") as f:
        f.write(new_html)


def main():
    state = load_state()
    now_dt = datetime.now(timezone.utc)

    # Add any newly-configured devices that aren't in state.json yet, without
    # touching devices that already have history.
    for d in DEVICES:
        state["devices"].setdefault(d["id"], blank_device_state(d))
        # Keep interval/threshold/label/network in state in sync with the
        # config above, in case they were edited since the last run.
        state["devices"][d["id"]]["label"] = d["label"]
        state["devices"][d["id"]]["network"] = d["network"]
        state["devices"][d["id"]]["expectedSchedule"] = d["expected_schedule"]
        state["devices"][d["id"]]["intervalMinutes"] = d["interval_minutes"]
        state["devices"][d["id"]]["thresholdMinutes"] = d["threshold_minutes"]

    # Before-status per device: use the status PERSISTED FROM THE LAST RUN,
    # never recompute it fresh from lastSeenIso + the current "now" -- see
    # references/sync-logic.md. This is what makes pure-timeout (silent)
    # down-transitions detectable at all.
    before_status = {}
    for d_id in DEVICE_IDS:
        dev = state["devices"][d_id]
        before_status[d_id] = dev.get("lastKnownStatus") or compute_status(dev, now_dt)

    offset = state.get("lastUpdateId", 0)
    new_offset = offset
    fetch_error = None

    try:
        updates = fetch_updates(offset)
    except Exception as exc:  # deliberately broad -- see comment below
        updates = []
        fetch_error = str(exc)
        # No early return / sys.exit here: a Telegram fetch failure must NOT
        # suppress down-detection below, because a device can go "down"
        # purely from elapsed time with zero new messages this run.

    matched_devices = set()
    explicit_down = set()
    for item in updates:
        update_id = item.get("update_id")
        if update_id is not None and update_id >= new_offset:
            new_offset = update_id + 1
        msg = extract_message(item)
        if not msg:
            continue
        text = msg.get("text") or msg.get("caption") or ""
        if not text:
            continue

        for d in DEVICES:
            if not d["pattern"].search(text):
                continue
            if ALIVE_KEYWORDS.search(text):
                state["devices"][d["id"]]["lastSeenIso"] = message_seen_iso(msg)
                matched_devices.add(d["id"])
            elif DOWN_KEYWORDS.search(text):
                # Explicit down report -- mark it immediately, don't touch
                # lastSeenIso (that's specifically "last time it was alive").
                explicit_down.add(d["id"])
            break  # a message belongs to at most one device

    # Down-detection runs unconditionally -- fetch failure or not, new
    # messages or not -- because it's purely a function of elapsed time.
    after_status = {}
    for d_id in DEVICE_IDS:
        if d_id in explicit_down:
            after_status[d_id] = "down"
        else:
            after_status[d_id] = compute_status(state["devices"][d_id], now_dt)

    newly_down = [
        d_id
        for d_id in DEVICE_IDS
        if before_status[d_id] in ("up", "unknown") and after_status[d_id] == "down"
    ]

    for d_id in newly_down:
        dev = state["devices"][d_id]
        reason = (
            "reported down explicitly in an alert message"
            if d_id in explicit_down
            else f"has not sent a heartbeat in over {dev['thresholdMinutes']} minutes"
        )
        send_alert(
            subject=f"[DOWN] {dev['label']} ({dev['network']})",
            body=(
                f"{dev['label']} on {dev['network']} {reason}.\n"
                f"Last seen: {dev.get('lastSeenIso') or 'never'}\n"
                f"Checked at: {now_iso()}"
            ),
        )

    for d_id in DEVICE_IDS:
        state["devices"][d_id]["lastKnownStatus"] = after_status[d_id]

    state["lastPollIso"] = now_iso()
    if fetch_error:
        state["lastPollOk"] = False
        state["lastPollNote"] = f"Telegram fetch failed: {fetch_error}"
    else:
        state["lastPollOk"] = True
        note_parts = []
        if matched_devices:
            note_parts.append(f"Matched heartbeats for: {', '.join(sorted(matched_devices))}.")
        if explicit_down:
            note_parts.append(f"Explicit down reports for: {', '.join(sorted(explicit_down))}.")
        state["lastPollNote"] = " ".join(note_parts) if note_parts else "No new heartbeats this cycle."

    # Only advance the offset in the state we're about to persist AFTER
    # everything above has been computed from `updates`.
    state["lastUpdateId"] = new_offset

    save_state(state)
    render_dashboard(state)

    print(state["lastPollNote"])
    if newly_down:
        print(f"Newly down: {', '.join(newly_down)}")


if __name__ == "__main__":
    main()
