#!/usr/bin/env python3
"""
Multi-network Telegram heartbeat -> status dashboard poller.

This is a variant of the telegram-status-dashboard skill's standard sync.py:
that template assumes every device shares ONE regex ("<id> is alive") and one
global down-threshold. This deployment's ~21 feeds each have a *completely
different* message wording (see DEVICE_CONFIG below) and different check-in
schedules, so instead each device gets its own compiled pattern and its own
threshold. The overall architecture and the two correctness rules in
references/sync-logic.md (persist lastKnownStatus; never skip down-detection
on fetch failure; only advance the offset after a successful save) are
unchanged from the standard template.

Run this on a schedule (see .github/workflows/heartbeat-sync.yml).
"""

import json
import os
import re
import smtplib
import ssl
import urllib.request
from datetime import datetime, timezone
from email.mime.text import MIMEText

# =====================  DEVICE CONFIG  =====================
# label:            shown on the dashboard.
# pattern:          unique regex identifying THIS feed's message. Verified
#                    against every real sample text with zero cross-matches
#                    before this file was generated -- see
#                    scripts/test_patterns.py.
# threshold_minutes: how many minutes of silence counts as "down" for this
#                    feed specifically.
# interval_note:     human-readable schedule, for the dashboard/README only
#                    -- not used in the down-detection math.
# confirmed:         True if threshold_minutes was computed from real
#                    multi-sample evidence; False if it's a conservative
#                    placeholder (26.5h) because only one sample message was
#                    available and the real schedule is still unknown. Every
#                    "confirmed": False entry should be revisited once you
#                    know the real cadence -- see README.md.
# alertable:         False suppresses down-transition emails for this device
#                    (it still shows on the dashboard). Used for the standby
#                    site, which is expected to be quiet most of the time.
DEVICE_CONFIG = {
    "N1_CloudA_SQ_SP1": {
        "label": "Cloud A - SQ (SP1)",
        "pattern": re.compile(r"SDC_A_SQ01\s+is\s+alive", re.IGNORECASE),
        "threshold_minutes": 990,  # 3x/day fixed (08:00/12:00/17:00); longest gap ~15h + buffer
        "interval_note": "Fixed: 08:00 / 12:00 / 17:00 daily",
        "confirmed": True,
        "alertable": True,
    },
    "N1_CloudA_WUG_SP1_CCG": {
        "label": "Cloud A - WUG to CCG (SP1)",
        "pattern": re.compile(r"\bSP\s+CCG\s+is\s+alive\b", re.IGNORECASE),
        "threshold_minutes": 990,  # only 2 samples (~5h apart) seen; assumed same 3x/day family as SQ sibling
        "interval_note": "ASSUMED same as SQ sibling (~3x/day) -- only 2 samples seen, please confirm",
        "confirmed": False,
        "alertable": True,
    },
    "N1_CloudA_WUG_HQ_SGCCG": {
        "label": "Cloud A - WUG to SGCCG (HQ, default NMS routing)",
        "pattern": re.compile(r"SG\s+CCG\s+via\s+SP\s+is\s+alive", re.IGNORECASE),
        "threshold_minutes": 1590,  # placeholder: only 1 sample seen, schedule unknown
        "interval_note": "UNKNOWN -- only 1 sample seen, placeholder threshold in use",
        "confirmed": False,
        "alertable": True,
    },
    "N1_CloudA_WUG_HQ_SP1SQ": {
        "label": "Cloud A - WUG to SP1SQ (HQ, ITI001)",
        "pattern": re.compile(r"SG\s+CCG\s+is\s+alive\s+thru\s+SQ", re.IGNORECASE),
        "threshold_minutes": 540,  # 4x/day fixed (00:00/08:00/12:00/17:05); longest gap ~8h + buffer
        "interval_note": "Fixed: 00:00 / 08:00 / 12:00 / 17:05 daily",
        "confirmed": True,
        "alertable": True,
    },
    "N2_CloudB_SQ_SP1": {
        "label": "Cloud B - SQ (SP1)",
        "pattern": re.compile(r"SDC_B_SQ01\s+is\s+alive", re.IGNORECASE),
        "threshold_minutes": 990,  # 3x/day fixed (08:00/12:00/17:00); longest gap ~15h + buffer
        "interval_note": "Fixed: 08:00 / 12:00 / 17:00 daily",
        "confirmed": True,
        "alertable": True,
    },
    "N2_CloudB_WUG_SP1_SQ": {
        "label": "Cloud B - WUG to SQ (SP1, ITI001)",
        "pattern": re.compile(r"DSP1\s+WUG\s+is\s+alive", re.IGNORECASE),
        "threshold_minutes": 1590,  # placeholder: only 1 sample seen, schedule unknown
        "interval_note": "UNKNOWN -- only 1 sample seen, placeholder threshold in use",
        "confirmed": False,
        "alertable": True,
    },
    "N3_SMv2_WUG_SP1_Active": {
        "label": "SM v2 WUG - Active site (SP1)",
        "pattern": re.compile(r"SM_SP1\s+WUG\s+01\s+is\s+alive", re.IGNORECASE),
        "threshold_minutes": 540,  # 4x/day fixed (00:00/08:00/12:12/17:00); longest gap ~8h + buffer
        "interval_note": "Fixed: 00:00 / 08:00 / 12:12 / 17:00 daily",
        "confirmed": True,
        "alertable": True,
    },
    "N3_SMv1_WUG_HQ_Standby": {
        "label": "SM v1 WUG - Standby site (HQ)",
        "pattern": re.compile(r"\bSM\s+WUG\s+01\s+is\s+alive\b", re.IGNORECASE),
        "threshold_minutes": 1590,  # placeholder: only 1 sample seen, schedule unknown
        "interval_note": "UNKNOWN -- standby site, expected to be quiet; alerting suppressed",
        "confirmed": False,
        "alertable": False,  # standby -- don't email on this one going quiet
    },
    "N4_CA_WUG_SP1_SQ": {
        "label": "CA WUG (SP1, SQ)",
        "pattern": re.compile(r"\bCA\s+WUG\s+is\s+alive\b", re.IGNORECASE),
        "threshold_minutes": 1590,
        "interval_note": "UNKNOWN -- only 1 sample seen, placeholder threshold in use",
        "confirmed": False,
        "alertable": True,
    },
    "N5_OV_SQ_HQ_GW1": {
        "label": "OV SQ - HQ Gateway 1",
        "pattern": re.compile(r"OV_SMS_GW1\s+is\s+alive", re.IGNORECASE),
        "threshold_minutes": 1590,
        "interval_note": "UNKNOWN -- only 1 sample seen, placeholder threshold in use",
        "confirmed": False,
        "alertable": True,
    },
    "N5_OV_SQ_HQ_GW2": {
        "label": "OV SQ - HQ Gateway 2",
        "pattern": re.compile(r"OV_SMS_GW2\s+is\s+alive", re.IGNORECASE),
        "threshold_minutes": 1590,
        "interval_note": "UNKNOWN -- only 1 sample seen, placeholder threshold in use",
        "confirmed": False,
        "alertable": True,
    },
    "N5_OV_WUG_HQ_SQ": {
        "label": "OV WUG (HQ, SQ)",
        "pattern": re.compile(r"\bOV\s+WUG\s+01\s+is\s+alive\b", re.IGNORECASE),
        "threshold_minutes": 1590,
        "interval_note": "UNKNOWN -- only 1 sample seen, placeholder threshold in use",
        "confirmed": False,
        "alertable": True,
    },
    "N6_VG_WUG_HQ_GW1": {
        "label": "VG WUG - HQ Gateway 1",
        "pattern": re.compile(r"VG_SMS_GW1\s+is\s+alive", re.IGNORECASE),
        "threshold_minutes": 1590,
        "interval_note": "UNKNOWN -- only 1 sample seen, placeholder threshold in use",
        "confirmed": False,
        "alertable": True,
    },
    "N6_VG_WUG_HQ_GW2": {
        "label": "VG WUG - HQ Gateway 2",
        "pattern": re.compile(r"VG_SMS_GW2\s+is\s+alive", re.IGNORECASE),
        "threshold_minutes": 1590,
        "interval_note": "UNKNOWN -- only 1 sample seen, placeholder threshold in use",
        "confirmed": False,
        "alertable": True,
    },
    "N7_CE_WUG_HQ_GW1": {
        "label": "CE WUG - HQ Gateway 1",
        "pattern": re.compile(r"CE-_SMS_GW1\s+is\s+alive", re.IGNORECASE),
        "threshold_minutes": 1590,
        "interval_note": "UNKNOWN -- only 1 sample seen, placeholder threshold in use",
        "confirmed": False,
        "alertable": True,
    },
    "N7_CE_WUG_HQ_GW2": {
        "label": "CE WUG - HQ Gateway 2",
        "pattern": re.compile(r"CE-_SMS_GW2\s+is\s+alive", re.IGNORECASE),
        "threshold_minutes": 1590,
        "interval_note": "UNKNOWN -- only 1 sample seen, placeholder threshold in use",
        "confirmed": False,
        "alertable": True,
    },
    "N8_MG_WUG_SP1_SQ": {
        "label": "MG WUG (SP1, SQ)",
        "pattern": re.compile(r"ZS\s+WUP02\s+CCG\s+is\s+alive", re.IGNORECASE),
        "threshold_minutes": 1590,
        "interval_note": "UNKNOWN -- only 1 sample seen, placeholder threshold in use",
        "confirmed": False,
        "alertable": True,
    },
    "N9_FS1_WUG_SP1_CCG": {
        "label": "FS-1 WUG (SP1, CCG)",
        "pattern": re.compile(r"FS-1\s+CCG\s+is\s+alive", re.IGNORECASE),
        "threshold_minutes": 1590,
        "interval_note": "UNKNOWN -- only 1 sample seen, placeholder threshold in use",
        "confirmed": False,
        "alertable": True,
    },
    "N10_X_SQ_SP2_GW1": {
        "label": "X SQ - SP2 Gateway 1",
        "pattern": re.compile(r"SP2_SMS_GW1\s+is\s+alive", re.IGNORECASE),
        "threshold_minutes": 75,  # message explicitly says "hourly keep alive"
        "interval_note": "Hourly (stated explicitly in the alert text)",
        "confirmed": True,
        "alertable": True,
    },
    "N10_X_SQ_SP2_GW2": {
        "label": "X SQ - SP2 Gateway 2",
        "pattern": re.compile(r"SP2_SMS_GW2\s+is\s+alive", re.IGNORECASE),
        "threshold_minutes": 75,
        "interval_note": "Hourly (stated explicitly in the alert text)",
        "confirmed": True,
        "alertable": True,
    },
    "N10_X_SQ_BV_GW2": {
        "label": "X SQ - BV Gateway 2",
        "pattern": re.compile(r"BV_SMS_GW2\s+is\s+alive", re.IGNORECASE),
        "threshold_minutes": 75,
        "interval_note": "Hourly (stated explicitly in the alert text)",
        "confirmed": True,
        "alertable": True,
    },
}
DEVICE_IDS = list(DEVICE_CONFIG.keys())

STATE_PATH = "state.json"
DASHBOARD_PATH = "docs/index.html"

# ===================================================================
# Nothing below this line should normally need editing -- see
# references/sync-logic.md (in the telegram-status-dashboard skill) for why
# these specific rules matter.
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


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r") as f:
            return json.load(f)
    return {
        "devices": {
            d: {
                "label": DEVICE_CONFIG[d]["label"],
                "lastSeenIso": None,
                "lastKnownStatus": "unknown",
                "thresholdMinutes": DEVICE_CONFIG[d]["threshold_minutes"],
            }
            for d in DEVICE_IDS
        },
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
    # Single chat for all 21 feeds per this deployment, but keep checking all
    # four keys unconditionally regardless -- costs nothing, and protects
    # against a future Group/Channel change.
    return (
        item.get("message")
        or item.get("edited_message")
        or item.get("channel_post")
        or item.get("edited_channel_post")
    )


def compute_status(last_seen_iso, threshold_minutes, now_dt):
    if last_seen_iso is None:
        return "unknown"
    last_seen = datetime.fromisoformat(last_seen_iso)
    elapsed_minutes = (now_dt - last_seen).total_seconds() / 60.0
    return "up" if elapsed_minutes <= threshold_minutes else "down"


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
            d: {
                "label": state["devices"][d]["label"],
                "lastSeenIso": state["devices"][d]["lastSeenIso"],
                "thresholdMinutes": state["devices"][d]["thresholdMinutes"],
            }
            for d in DEVICE_IDS
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
    new_html = re.sub(
        r'<script type="application/json" id="device-state">.*?</script>',
        block,
        html,
        flags=re.DOTALL,
    )
    with open(DASHBOARD_PATH, "w") as f:
        f.write(new_html)


def main():
    state = load_state()
    now_dt = datetime.now(timezone.utc)

    # Before-status per device: use the status PERSISTED FROM THE LAST RUN,
    # never recompute fresh from lastSeenIso + current "now" (see
    # references/sync-logic.md) -- otherwise a device that goes quiet can
    # never be detected as a fresh down-transition.
    before_status = {}
    for d in DEVICE_IDS:
        dev = state["devices"].setdefault(
            d,
            {
                "label": DEVICE_CONFIG[d]["label"],
                "lastSeenIso": None,
                "lastKnownStatus": "unknown",
                "thresholdMinutes": DEVICE_CONFIG[d]["threshold_minutes"],
            },
        )
        # Keep thresholdMinutes in state in sync with the current config, in
        # case DEVICE_CONFIG was tuned since the last run.
        dev["thresholdMinutes"] = DEVICE_CONFIG[d]["threshold_minutes"]
        before_status[d] = dev.get("lastKnownStatus") or compute_status(
            dev.get("lastSeenIso"), dev["thresholdMinutes"], now_dt
        )

    offset = state.get("lastUpdateId", 0)
    new_offset = offset
    fetch_error = None

    try:
        updates = fetch_updates(offset)
    except Exception as exc:  # deliberately broad -- see comment below
        updates = []
        fetch_error = str(exc)
        # No early return: down-detection below must still run even if the
        # fetch failed, since it's purely a function of elapsed time.

    matched_devices = set()
    for item in updates:
        update_id = item.get("update_id")
        if update_id is not None and update_id >= new_offset:
            new_offset = update_id + 1
        msg = extract_message(item)
        if not msg:
            continue
        text = msg.get("text") or msg.get("caption") or ""
        for device_id, cfg in DEVICE_CONFIG.items():
            if cfg["pattern"].search(text):
                state["devices"][device_id]["lastSeenIso"] = now_iso()
                matched_devices.add(device_id)
                # Each feed's pattern is unique (verified against every real
                # sample with zero cross-matches) so one message should only
                # ever match one device; don't `break` in case a future feed
                # legitimately shares wording, so it isn't silently missed.

    after_status = {
        d: compute_status(
            state["devices"][d].get("lastSeenIso"),
            DEVICE_CONFIG[d]["threshold_minutes"],
            now_dt,
        )
        for d in DEVICE_IDS
    }

    newly_down = [
        d
        for d in DEVICE_IDS
        if before_status[d] in ("up", "unknown")
        and after_status[d] == "down"
        and DEVICE_CONFIG[d]["alertable"]
    ]

    for d in newly_down:
        dev = state["devices"][d]
        cfg = DEVICE_CONFIG[d]
        send_alert(
            subject=f"[DOWN] {dev['label']} has not checked in",
            body=(
                f"{dev['label']} ({d}) has not sent a heartbeat in over "
                f"{cfg['threshold_minutes']} minutes.\n"
                f"Expected schedule: {cfg['interval_note']}\n"
                f"Last seen: {dev.get('lastSeenIso') or 'never'}\n"
                f"Checked at: {now_iso()}"
            ),
        )

    for d in DEVICE_IDS:
        state["devices"][d]["lastKnownStatus"] = after_status[d]

    state["lastPollIso"] = now_iso()
    if fetch_error:
        state["lastPollOk"] = False
        state["lastPollNote"] = f"Telegram fetch failed: {fetch_error}"
    else:
        state["lastPollOk"] = True
        state["lastPollNote"] = (
            f"Matched heartbeats for: {', '.join(sorted(matched_devices))}."
            if matched_devices
            else "No new heartbeats this cycle."
        )

    # Only advance the offset in the state we're about to persist AFTER
    # everything above succeeded -- see references/sync-logic.md.
    state["lastUpdateId"] = new_offset

    save_state(state)
    render_dashboard(state)

    print(state["lastPollNote"])
    if newly_down:
        print(f"Newly down (alerted): {', '.join(newly_down)}")


if __name__ == "__main__":
    main()
