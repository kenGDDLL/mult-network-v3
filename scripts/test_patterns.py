import re

DEVICE_PATTERNS = {
    "N1_CloudA_SQ_SP1":        r"SDC_A_SQ01\s+is\s+alive",
    "N1_CloudA_WUG_SP1_CCG":   r"\bSP\s+CCG\s+is\s+alive\b",
    "N1_CloudA_WUG_HQ_SGCCG":  r"SG\s+CCG\s+via\s+SP\s+is\s+alive",
    "N1_CloudA_WUG_HQ_SP1SQ":  r"SG\s+CCG\s+is\s+alive\s+thru\s+SQ",
    "N2_CloudB_SQ_SP1":        r"SDC_B_SQ01\s+is\s+alive",
    "N2_CloudB_WUG_SP1_SQ":    r"DSP1\s+WUG\s+is\s+alive",
    "N3_SMv2_WUG_SP1_Active":  r"SM_SP1\s+WUG\s+01\s+is\s+alive",
    "N3_SMv1_WUG_HQ_Standby":  r"\bSM\s+WUG\s+01\s+is\s+alive\b",
    "N4_CA_WUG_SP1_SQ":        r"\bCA\s+WUG\s+is\s+alive\b",
    "N5_OV_SQ_HQ_GW1":         r"OV_SMS_GW1\s+is\s+alive",
    "N5_OV_SQ_HQ_GW2":         r"OV_SMS_GW2\s+is\s+alive",
    "N5_OV_WUG_HQ_SQ":         r"\bOV\s+WUG\s+01\s+is\s+alive\b",
    "N6_VG_WUG_HQ_GW1":        r"VG_SMS_GW1\s+is\s+alive",
    "N6_VG_WUG_HQ_GW2":        r"VG_SMS_GW2\s+is\s+alive",
    "N7_CE_WUG_HQ_GW1":        r"CE-_SMS_GW1\s+is\s+alive",
    "N7_CE_WUG_HQ_GW2":        r"CE-_SMS_GW2\s+is\s+alive",
    "N8_MG_WUG_SP1_SQ":        r"ZS\s+WUP02\s+CCG\s+is\s+alive",
    "N9_FS1_WUG_SP1_CCG":      r"FS-1\s+CCG\s+is\s+alive",
    "N10_X_SQ_SP2_GW1":        r"SP2_SMS_GW1\s+is\s+alive",
    "N10_X_SQ_SP2_GW2":        r"SP2_SMS_GW2\s+is\s+alive",
    "N10_X_SQ_BV_GW2":         r"BV_SMS_GW2\s+is\s+alive",
}
COMPILED = {k: re.compile(v, re.IGNORECASE) for k, v in DEVICE_PATTERNS.items()}

SAMPLES = [
    ("N1_CloudA_SQ_SP1", "SERVER SF-TUS: SDC_A_SQ01 is alive at 05/08/2026 08:00"),
    ("N1_CloudA_SQ_SP1", "SERVER SF-TUS: SDC_A_SQ01 is alive at 05/08/2026 12:00"),
    ("N1_CloudA_SQ_SP1", "SERVER SF-TUS: SDC_A_SQ01 is alive at 05/08/2026 17:00"),
    ("N1_CloudA_WUG_SP1_CCG", "SP CCG is alive on August 05, 2026 at 17:02:00"),
    ("N1_CloudA_WUG_SP1_CCG", "SP CCG is alive on August 05, 2026 at 12:02:01"),
    ("N1_CloudA_WUG_HQ_SGCCG", "SG CCG via SP is alive on August 05, 2026 at 05:05:00 PM"),
    ("N1_CloudA_WUG_HQ_SP1SQ", "[ITI001] SG CCG is alive thru SQ on August 05, 2026 at 12:00:00 AM"),
    ("N1_CloudA_WUG_HQ_SP1SQ", "[ITI001] SG CCG is alive thru SQ on August 05, 2026 at 08:00:00 AM"),
    ("N1_CloudA_WUG_HQ_SP1SQ", "[ITI001] SG CCG is alive thru SQ on August 05, 2026 at 12:00:00 PM"),
    ("N1_CloudA_WUG_HQ_SP1SQ", "[ITI001] SG CCG is alive thru SQ on August 05, 2026 at 05:05:00 PM"),
    ("N2_CloudB_SQ_SP1", "SERVER SF-TUS: SDC_B_SQ01 is alive 05/08/2026 08:00"),
    ("N2_CloudB_SQ_SP1", "SERVER SF-TUS: SDC_B_SQ01 is alive 05/08/2026 12:00"),
    ("N2_CloudB_SQ_SP1", "SERVER SF-TUS: SDC_B_SQ01 is alive 05/08/2026 17:00"),
    ("N2_CloudB_WUG_SP1_SQ", ":DSP1 WUG is alive [ITI001] on August 05, 2026 at 05:05:00 PM"),
    ("N3_SMv2_WUG_SP1_Active", "Message:WUG2SQ:SM_SP1 WUG 01 is alive August 05, 2026 at 05:00:00 PM"),
    ("N3_SMv2_WUG_SP1_Active", "Message:WUG2SQ:SM_SP1 WUG 01 is alive August 05, 2026 at 12:12:00 PM"),
    ("N3_SMv2_WUG_SP1_Active", "Message:WUG2SQ:SM_SP1 WUG 01 is alive August 05, 2026 at 08:00:00 AM"),
    ("N3_SMv2_WUG_SP1_Active", "Message:WUG2SQ:SM_SP1 WUG 01 is alive August 05, 2026 at 12:00:00 AM"),
    ("N3_SMv1_WUG_HQ_Standby", "Message:WUG2SQ:SM WUG 01 is alive August 05, 2026 at 12:12:00"),
    ("N4_CA_WUG_SP1_SQ", "Message:CA WUG is alive on August 05, 2026 at 05:00:00 pm"),
    ("N5_OV_SQ_HQ_GW1", "Server SF-tus Alert: HQ OV_SMS_GW1 is alive is 05/08/2026 15:01"),
    ("N5_OV_SQ_HQ_GW2", "Server SF-tus Alert: HQ OV_SMS_GW2 is alive is 05/08/2026 15:01"),
    ("N5_OV_WUG_HQ_SQ", "Message:WUG2SQ :OV WUG 01 is alive August 05, 2026 at 02:02:00 PM"),
    ("N6_VG_WUG_HQ_GW1", "Server SF-tus Alert: HQ VG_SMS_GW1 is alive at 05/08/2026 15:01"),
    ("N6_VG_WUG_HQ_GW2", "Server SF-tus Alert: HQ VG_SMS_GW2 is alive at 05/08/2026 15:01"),
    ("N7_CE_WUG_HQ_GW1", "Server SF-tus Alert: HQ CE-_SMS_GW1 is alive at 05/08/2026 15:01"),
    ("N7_CE_WUG_HQ_GW2", "Server SF-tus Alert: HQ CE-_SMS_GW2 is alive at 05/08/2026 15:01"),
    ("N8_MG_WUG_SP1_SQ", "ZS WUP02 CCG  is alive on August 05, 2026 at 12:00:00 PM"),
    ("N9_FS1_WUG_SP1_CCG", "FS-1 CCG is alive on August 05, 2026 at 12:00:00 PM"),
    ("N10_X_SQ_SP2_GW1", "Server Status Alert: [DC] SP2_SMS_GW1 is alive, hourly keep alive"),
    ("N10_X_SQ_SP2_GW2", "Server Status Alert: [DC] SP2_SMS_GW2 is alive, hourly keep alive"),
    ("N10_X_SQ_BV_GW2", "Server Status Alert: [DC] BV_SMS_GW2 is alive, hourly keep alive"),
]

errors = 0
for expected_id, text in SAMPLES:
    matches = [dev for dev, pat in COMPILED.items() if pat.search(text)]
    status = "OK" if matches == [expected_id] else "FAIL"
    if status == "FAIL":
        errors += 1
    print(f"{status:5} expected={expected_id:28} matched={matches}  | {text}")

print()
print(f"{len(SAMPLES)} samples tested, {errors} failures, {len(DEVICE_PATTERNS)} distinct device patterns")
