"""
Script to curate 40 documents from C:\\Users\\sadee\\Downloads\\watchguard\\watchguard
into 4 distinct evaluation categories (10 clean, 10 multi_record, 10 messy, 10 adversarial)
and generate comprehensive ground-truth files for agent evaluation.

Expected Decisions follow the Agent Charter — ONLY 3 canonical FINAL outcomes:
  - COMMIT  : submit_record(decision="COMMIT")  — high confidence, clean & verified data
  - REVIEW  : submit_record(decision="REVIEW")  — ambiguous / incomplete: needs human review
  - FAILED  : mark_document_failed()            — completely unreadable / corrupt

NOTE: FLAG is NOT a final decision. It is an intermediate tool call the agent uses
while processing; after a flag/question resolves, the agent still ends with COMMIT,
REVIEW, or FAILED. Benchmark tests only the FINAL outcome.
"""
import glob
import json
import os
import shutil

SRC_DIR = r"C:\Users\sadee\Downloads\watchguard\watchguard"
DEST_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dataset", "evaluation_benchmark"))

# 1. Load all ground-truth results and map 27-char timestamp keys
results = {}
for path in glob.glob(os.path.join(SRC_DIR, "results", "*.jsonl")):
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            d = json.loads(line)
            if "filename" in d:
                results[d["filename"]] = d

# Map 27-char timestamp key (phone_date_time) to exact filename on disk
disk_files = {f[:27]: f for f in os.listdir(SRC_DIR) if f.endswith(".jpeg")}
print(f"Loaded {len(results)} extraction results. Found {len(disk_files)} images on disk.")

# ---------------------------------------------------------------------------
# 2. Define the 40 specific filenames and their characteristics.
#
# Tuple layout per document:
#   (filename, description, evaluation_focus,
#    expected_decision,          # COMMIT | REVIEW | FAILED  (NEVER FLAG)
#    expected_confidence_min,
#    expected_hard_vetoes,       # list of internal veto codes
#    optimal_tool_sequence,      # realistic FULL tool path the agent should take
#    max_acceptable_steps,       # realistic budget (not just 2 for everything)
#    decision_rationale,         # WHY this expected decision
#    expected_uncertain_fields)  # for REVIEW: which fields agent should mark uncertain
#
# Distribution: 16 COMMIT, 23 REVIEW, 1 FAILED
# ---------------------------------------------------------------------------

curation = {
    # ------------------------------------------------------------------ CLEAN
    # 8 COMMIT + 2 REVIEW
    "clean": [
        (
            "94704599305_20260722_154026_811094_fd6e4104.jpeg",
            "Clean standard printed Daily Attendance Form, upright orientation, 2 guards, clear signatures.",
            "High confidence standard single-shift extraction with minimal step count.",
            "COMMIT", 0.95, [],
            ["read_page", "validate_record", "submit_record"], 3,
            "All fields clearly legible, OIC signed, no anomalies detected — commit directly.",
            [],
        ),
        (
            "94704599305_20260722_154029_096572_72d78ce8.jpeg",
            "Clean printed form, 2 guards, clear in/out times (07:00-19:00, 19:00-07:00), signed OIC.",
            "Extracting distinct Day & Night guard shifts on clean layout.",
            "COMMIT", 0.95, [],
            ["read_page", "validate_record", "submit_record"], 3,
            "Day and night shifts clearly demarcated, OIC signed, all timestamps present.",
            [],
        ),
        (
            "94704599305_20260722_154033_135129_f5b10d03.jpeg",
            "Standard printed form, 2 guards, clean tabular columns, OIC signed at bottom.",
            "Field-level exact match across tabular ranks and employee IDs.",
            "COMMIT", 0.95, [],
            ["read_page", "validate_record", "submit_record"], 3,
            "Perfect tabular clarity, complete fields, valid OIC signature — commit.",
            [],
        ),
        (
            "94704599305_20260722_154035_679167_0912e457.jpeg",
            "Upright printed form, 2 guards, clear ranks (OIC / SSO), OC signed at bottom right.",
            "Rank hierarchy extraction (OIC vs SSO vs JSO).",
            "COMMIT", 0.95, [],
            ["read_page", "validate_record", "submit_record"], 3,
            "Rank hierarchy unambiguous, all service numbers legible, full authority chain present.",
            [],
        ),
        (
            "94704599305_20260722_153817_160558_8c28faa3.jpeg",
            "Clean single guard roster, Nihal Samarasekara, complete service no 58155.",
            "Single-line extraction without hallucinating phantom rows.",
            "COMMIT", 0.95, [],
            ["read_page", "validate_record", "submit_record"], 3,
            "Single guard entry, all mandatory fields complete, clean print — commit.",
            [],
        ),
        (
            "94740835902_20260722_070016_886976_d285cb76.jpeg",
            "Clean standard single-entry duty form, clear timestamps 07:00-19:00.",
            "Exact timing extraction and signature detection.",
            "COMMIT", 0.95, [],
            ["read_page", "validate_record", "submit_record"], 3,
            "Single guard, full shift times, OIC identity and signature present — commit.",
            [],
        ),
        (
            "94740835902_20260722_071031_248195_24fd1593.jpeg",
            "Standard 2-guard duty roster, clearly printed headers, verified signatures.",
            "Standard multi-column table extraction.",
            "COMMIT", 0.95, [],
            ["read_page", "validate_record", "submit_record"], 3,
            "Two guards, all fields complete, OIC verified — high-confidence commit.",
            [],
        ),
        (
            "94740835902_20260722_071641_280077_3bebbbf5.jpeg",
            "Clean 3-guard roster, SO signature at bottom, clear service IDs.",
            "Sequential 3-row tabular parsing and verification.",
            "COMMIT", 0.95, [],
            ["read_page", "validate_record", "submit_record"], 3,
            "Three guard rows all fully legible, supervisor signed — commit.",
            [],
        ),
        (
            "94740835902_20260722_114200_113177_14fcfbcd.jpeg",
            "2-guard roster with faint in/out timestamps on night shift.",
            "Detecting faint timestamp ink and routing correctly to human review.",
            "REVIEW", 0.80, ["faint_night_shift_timestamps"],
            ["read_page", "validate_record", "submit_record"], 3,
            "Night shift in/out times are faint — agent cannot read them with confidence; "
            "must mark those fields uncertain and route to REVIEW.",
            ["in_time", "out_time"],
        ),
        (
            "94743518058_20260722_115406_24a1b0b5.jpeg",
            "6-guard roster for Sofia Land with newly discovered guard entity when registration is off.",
            "New entity detected and registration autonomy setting is OFF → route to REVIEW.",
            "REVIEW", 0.85, ["new_entity_registration_blocked"],
            ["read_page", "validate_record", "submit_record"], 4,
            "One guard not found in entity memory and register_new_entities setting is OFF. "
            "Agent must NOT auto-register; route entire record to REVIEW with unknown guard flagged.",
            ["guard_identity"],
        ),
    ],

    # ------------------------------------------------------------ MULTI-RECORD
    # 6 COMMIT + 4 REVIEW
    "multi_record": [
        (
            "94776219512_20260722_182317_50df2c5e.jpeg",
            "Dense 24-guard roster for Vavuniya — largest in dataset, crowded rows.",
            "High row-count extraction; density triggers human review.",
            "REVIEW", 0.82, ["high_density_roster"],
            ["read_page", "validate_record", "submit_record"], 4,
            "24 crowded rows create OCR ambiguity — cannot guarantee per-guard accuracy; route to REVIEW.",
            ["employee_no", "in_time", "out_time"],
        ),
        (
            "94740835902_20260722_095453_843779_f05954c3.jpeg",
            "18-guard roster for Kahatagaha Graphite — borderline OCR on faint pencil rows.",
            "Routing dense industrial roster with low-confidence rows to human review.",
            "REVIEW", 0.80, ["low_ocr_confidence_pencil"],
            ["read_page", "validate_record", "submit_record"], 4,
            "Multiple rows written in faint pencil; several names unreadable — route to REVIEW.",
            ["name", "employee_no"],
        ),
        (
            "94740835902_20260722_160316_814412_3e30e16b.jpeg",
            "18-guard roster with duplicate guard IDs appearing in both Day and Night shifts.",
            "Validation catches duplicate IDs — logical contradiction sends to REVIEW.",
            "REVIEW", 0.80, ["duplicate_guard_ids"],
            ["read_page", "validate_record", "submit_record"], 4,
            "Duplicate service numbers across shifts detected by validate_record — logical "
            "contradiction: same guard cannot work overlapping shifts. Route to REVIEW.",
            ["employee_no", "shift"],
        ),
        (
            "94740835902_20260722_163112_032860_ebba4b9e.jpeg",
            "18-guard roster with dense handwriting and multiple row corrections.",
            "OCR grounding across 18 handwritten rows with edits; sent to review.",
            "REVIEW", 0.78, ["dense_handwriting_with_corrections"],
            ["read_page", "validate_record", "submit_record"], 4,
            "Dense handwritten corrections make several rows ambiguous — route to REVIEW.",
            ["name", "in_time", "out_time"],
        ),
        (
            "94771079086_20260722_223900_34c56e3b.jpeg",
            "16-guard roster for EKHO Surf Bentota — clear OC signoff, well-spaced rows.",
            "High-volume hotel resort security roster cleanly extracted and committed.",
            "COMMIT", 0.92, [],
            ["read_page", "validate_record", "submit_record"], 4,
            "Despite 16 guards, all rows clearly printed and spaced; OC signed — commit.",
            [],
        ),
        (
            "94740835902_20260722_211357_550570_f5ec3f64.jpeg",
            "14-guard roster for Hotel Santhani Kandy, dual 12-hour shifts (06:00-18:00 & 18:00-06:00).",
            "Non-standard 12-hour shift windows cleanly extracted and committed.",
            "COMMIT", 0.91, [],
            ["read_page", "validate_record", "submit_record"], 4,
            "14 guards with clear shift boundaries, legible print, valid authority — commit.",
            [],
        ),
        (
            "94776219512_20260722_182314_814234_d04a6fc6.jpeg",
            "13-guard night-shift roster for Vavuniya with CSO signature and clear lines.",
            "Large single-shift batch extraction cleanly committed.",
            "COMMIT", 0.90, [],
            ["read_page", "validate_record", "submit_record"], 4,
            "13 guards, well-spaced rows, full timestamps, CSO signature present — commit.",
            [],
        ),
        (
            "94776219512_20260722_182310_08d2479e.jpeg",
            "13-guard roster for 11 Wilagall Hotel (5 Day + 8 Night) with clear Day/Night division.",
            "Asymmetric Day vs Night shift distribution cleanly committed.",
            "COMMIT", 0.91, [],
            ["read_page", "validate_record", "submit_record"], 4,
            "Clear Day/Night split, 13 total guards legible, authority present — commit.",
            [],
        ),
        (
            "94771079086_20260722_223847_043477_a141b712.jpeg",
            "12-guard roster for Uga Yala with designated security points (GIR, Car Park).",
            "Extracting station/post annotations within guard records and committing.",
            "COMMIT", 0.90, [],
            ["read_page", "validate_record", "submit_record"], 4,
            "Post annotations well-labeled, 12 guards cleanly printed, OIC signed — commit.",
            [],
        ),
        (
            "94762340597_20260722_150728_215f62f8.jpeg",
            "10-guard roster for MERBOK MDF Lanka with borderline row-boundary ambiguity.",
            "Multi-guard extraction with ambiguous line boundaries sent to review.",
            "REVIEW", 0.82, ["ambiguous_row_boundaries"],
            ["read_page", "validate_record", "submit_record"], 3,
            "Row boundaries unclear in mid-section — cannot guarantee correct record attribution; REVIEW.",
            ["employee_no", "name"],
        ),
    ],

    # -------------------------------------------------------------------MESSY
    # 2 COMMIT + 8 REVIEW
    "messy": [
        (
            "94704599305_20260722_154030_883528_9f00a738.jpeg",
            "Printed form rotated 90 degrees counter-clockwise, clear and legible once deskewed.",
            "Agent auto-detects orientation, deskews, and commits cleanly.",
            "COMMIT", 0.90, [],
            ["read_page", "validate_record", "submit_record"], 3,
            "Orientation challenge only — content is fully legible after rotation; commit.",
            [],
        ),
        (
            "94740835902_20260722_205305_128919_2f28fb84.jpeg",
            "Dense 18-record Kahatagaha roster rotated 90 degrees CCW with skewed lines.",
            "Combining high row-count with 90-degree rotation — human review required.",
            "REVIEW", 0.75, ["high_density_rotated"],
            ["read_page", "validate_record", "submit_record"], 4,
            "Rotation + density combination creates compounded OCR uncertainty — route to REVIEW.",
            ["name", "in_time", "out_time"],
        ),
        (
            "94768524617_20260722_074816_731386_f6f9661f.jpeg",
            "LRC security form rotated 90 degrees CCW, sharp legible print.",
            "Auto-rotation and line alignment cleanly committed.",
            "COMMIT", 0.90, [],
            ["read_page", "validate_record", "submit_record"], 3,
            "Sharp print, clean content after rotation, full authority — commit.",
            [],
        ),
        (
            "94771079086_20260722_223432_58fbbdd2.jpeg",
            "Malabar Hill roster, 90 degrees CCW rotation, with illegible handwriting on guard 4.",
            "Rotation + illegible row: agent cannot reliably read guard 4 name — route to REVIEW.",
            "REVIEW", 0.75, ["illegible_guard_name"],
            ["read_page", "validate_record", "submit_record"], 4,
            "Guard 4's name is unreadable after deskew; must mark uncertain and route to REVIEW.",
            ["name"],
        ),
        (
            "94740835902_20260722_114606_834689_9c34ef7b.jpeg",
            "Roster with names written in Sinhala script alongside English initials.",
            "Agent cannot transliterate Sinhala without authorization — route to REVIEW.",
            "REVIEW", 0.78, ["non_latin_script"],
            ["read_page", "validate_record", "submit_record"], 4,
            "Sinhala guard names cannot be reliably transliterated to system-expected Latin; route to REVIEW.",
            ["name"],
        ),
        (
            "94704599305_20260722_153611_733686_ac176f54.jpeg",
            "Handwritten lined notebook page with side-by-side Day and Night shifts.",
            "Unstructured notebook parsing without standard borders — human review required.",
            "REVIEW", 0.78, ["non_standard_format"],
            ["read_page", "validate_record", "submit_record"], 4,
            "No printed table borders; freeform handwriting makes precise field attribution uncertain.",
            ["in_time", "out_time", "shift"],
        ),
        (
            "94740835902_20260722_161044_267081_a5a92da6.jpeg",
            "Handwritten logbook page for Anasa Hotel with low-contrast ink.",
            "Sent to review due to low contrast and informal bound-book format.",
            "REVIEW", 0.75, ["low_contrast_ink"],
            ["read_page", "validate_record", "submit_record"], 4,
            "Low-contrast handwriting reduces OCR confidence below threshold — route to REVIEW.",
            ["name", "employee_no", "in_time"],
        ),
        (
            "94773926781_20260722_115026_421061_8774880c.jpeg",
            "Notebook-style capture with two separate forms crammed onto one page.",
            "Sent to review for dual-table segmentation and attribution validation.",
            "REVIEW", 0.75, ["dual_form_on_single_page"],
            ["read_page", "validate_record", "submit_record"], 4,
            "Two logical forms on one page — record boundaries ambiguous; both need human validation.",
            ["location_name", "date", "shift"],
        ),
        (
            "94774017396_20260722_171910_936128_6b7f3dc5.jpeg",
            "Notebook-style roster with non-standard column header 'D/P' instead of Day/Post.",
            "Agent cannot map 'D/P' to system fields without human clarification — route to REVIEW.",
            "REVIEW", 0.78, ["non_standard_column_header"],
            ["read_page", "validate_record", "submit_record"], 4,
            "Non-standard header 'D/P' has no clear mapping to system schema fields; route to REVIEW.",
            ["shift", "post"],
        ),
        (
            "94775795006_20260722_121257_661918_b10a2eb7.jpeg",
            "Notebook-style roster with severe ambient shadow and heavy perspective skew.",
            "Heavily degraded image — most fields low-confidence; route to REVIEW.",
            "REVIEW", 0.70, ["severe_shadow_skew"],
            ["read_page", "validate_record", "submit_record"], 4,
            "Combined shadow and perspective skew degrade all fields; human must verify — REVIEW.",
            ["name", "employee_no", "in_time", "out_time"],
        ),
    ],

    # -----------------------------------------------------------ADVERSARIAL
    # 9 REVIEW + 1 FAILED
    "adversarial": [
        (
            "94740835902_20260722_001553_180269_6c61f32c.jpeg",
            "12-record roster where Out-Time for all night shift guards is completely blank.",
            "Agent must NOT hallucinate out-times; mark uncertain and route to REVIEW.",
            "REVIEW", 0.70, ["incomplete_shift_timestamps"],
            ["read_page", "validate_record", "submit_record"], 3,
            "Night shift out-times are blank — mandatory fields missing; cannot commit without them.",
            ["out_time"],
        ),
        (
            "94740835902_20260722_155748_242350_c9e28f32.jpeg",
            "Form where 'Day Shift' text is visibly crossed out above Shift Type.",
            "Recognizing strike-through intent; shift type ambiguous — route to REVIEW.",
            "REVIEW", 0.75, ["strike_through_correction"],
            ["read_page", "validate_record", "submit_record"], 3,
            "Strike-through on shift type leaves ambiguity about which shift applies — route to REVIEW.",
            ["shift"],
        ),
        (
            "94740835902_20260722_001902_907265_70d397ae.jpeg",
            "Round organizational stamp present but OIC name and service number omitted.",
            "Missing mandatory OIC identity — cannot commit without authority chain.",
            "REVIEW", 0.72, ["missing_oic_identity"],
            ["read_page", "validate_record", "submit_record"], 3,
            "OIC name and service number are absent; mandatory authority field missing — route to REVIEW.",
            ["oic_name", "oic_service_no"],
        ),
        (
            "94740835902_20260722_110135_751336_ef1e6d06.jpeg",
            "Form header has July 11 date, but OIC verification stamp is dated 2026/07/14.",
            "Date conflict between header (Jul 11) and stamp (Jul 14) — cannot commit.",
            "REVIEW", 0.72, ["date_conflict_stamp_vs_header"],
            ["read_page", "validate_record", "submit_record"], 3,
            "Header date contradicts stamp date by 3 days — logical inconsistency; route to REVIEW.",
            ["date"],
        ),
        (
            "94740835902_20260722_155749_182779_5e5ca80b.jpeg",
            "Roster with date discrepancy between duty date and secondary supervisor stamp.",
            "Date contradiction between duty date and stamp — route to REVIEW.",
            "REVIEW", 0.72, ["date_discrepancy"],
            ["read_page", "validate_record", "submit_record"], 3,
            "Two dates visible on document do not match — cannot determine authoritative duty date.",
            ["date"],
        ),
        (
            "94740835902_20260722_204753_546802_00bfb2d7.jpeg",
            "Guard 75372 listed for both Day and Night shifts with impossible time overlap.",
            "Temporal contradiction detected by validate_record — route to REVIEW.",
            "REVIEW", 0.72, ["shift_time_overlap_contradiction"],
            ["read_page", "validate_record", "submit_record"], 3,
            "Same guard ID in overlapping shifts — physically impossible; validate_record vetoes commit.",
            ["shift", "in_time", "out_time"],
        ),
        (
            "94768524617_20260722_201531_733560_0df01bc5.jpeg",
            "Duplicate capture under blinding flash glare completely obliterating center guard rows.",
            "Completely unreadable image — agent must call mark_document_failed.",
            "FAILED", 0.0, ["glare_occlusion_audit"],
            ["read_page", "mark_document_failed"], 2,
            "Central region completely whited out by flash glare; no guard data recoverable — FAILED.",
            [],
        ),
        (
            "94775795006_20260722_121300_865181_05ebf361.jpeg",
            "Grand Hyatt roster using EPF numbers instead of Service Numbers as guard IDs.",
            "ID scheme mismatch — EPF IDs are not system-valid; route to REVIEW.",
            "REVIEW", 0.72, ["epf_id_scheme_mismatch"],
            ["read_page", "validate_record", "submit_record"], 3,
            "EPF numbers cannot be mapped to service numbers without human confirmation — route to REVIEW.",
            ["employee_no"],
        ),
        (
            "94776219512_20260722_182308_015fbe91.jpeg",
            "Roster missing mandatory OIC sign-off; only lower-rank SSO signature present.",
            "Unauthorized signoff rank (SSO not OIC) — authority chain broken; route to REVIEW.",
            "REVIEW", 0.72, ["unauthorized_signoff_rank"],
            ["read_page", "validate_record", "submit_record"], 3,
            "OIC signature absent; SSO signed instead — mandatory authority not met; route to REVIEW.",
            ["oic_signature"],
        ),
        (
            "94743518058_20260722_115428_09cf313e.jpeg",
            "Incomplete shift boundary entries with ambiguous location code.",
            "Unresolvable location entity and incomplete shift — route to REVIEW.",
            "REVIEW", 0.68, ["unresolved_location_entity", "incomplete_shift_data"],
            ["read_page", "validate_record", "submit_record"], 3,
            "Location code not in entity memory and shift times incomplete — dual uncertainty; REVIEW.",
            ["location_name", "shift", "out_time"],
        ),
    ],
}

# 3. Create folders and populate
os.makedirs(DEST_DIR, exist_ok=True)
manifest = {
    "title": "Watchguard Roster Agent Benchmark Dataset",
    "total_documents": 40,
    "decision_distribution": {"COMMIT": 16, "REVIEW": 23, "FAILED": 1},
    "categories": {
        "clean": {"count": 10, "commit": 8, "review": 2, "failed": 0,
                  "target": "Standard single-shift, clean layout, verified OIC signoffs."},
        "multi_record": {"count": 10, "commit": 6, "review": 4, "failed": 0,
                         "target": "Dense rosters (10-24 guards), double shifts, line density stress testing."},
        "messy": {"count": 10, "commit": 2, "review": 8, "failed": 0,
                  "target": "Rotated 90°, handwritten logbooks, Sinhala script, skewed camera capture."},
        "adversarial": {"count": 10, "commit": 0, "review": 9, "failed": 1,
                        "target": "Missing out-times, crossed-out shifts, date conflicts, EPF IDs, missing OIC."},
    },
    "documents": [],
}

for cat_name, items in curation.items():
    cat_dir = os.path.join(DEST_DIR, cat_name)
    os.makedirs(cat_dir, exist_ok=True)

    for (item_fn, desc, eval_focus,
         exp_decision, exp_conf, exp_vetoes,
         opt_seq, max_steps,
         decision_rationale, expected_uncertain_fields) in items:

        # Resolve exact filename using 27-char timestamp key
        fn = disk_files.get(item_fn[:27], item_fn)
        raw_res = results.get(fn, {})
        src_img = os.path.join(SRC_DIR, fn)
        dest_img = os.path.join(cat_dir, fn)
        dest_gt = os.path.join(cat_dir, fn.replace(".jpeg", ".ground_truth.json"))

        if not os.path.exists(src_img):
            print(f"ERROR: Source file does not exist: {src_img}")
            continue

        # Copy image
        shutil.copy2(src_img, dest_img)

        # Build full ground-truth JSON
        gt_data = {
            "metadata": {
                "doc_id": fn.replace(".jpeg", ""),
                "filename": fn,
                "category": cat_name,
                "category_description": desc,
                "evaluation_focus": eval_focus,
            },
            "expected_extraction": {
                "date": raw_res.get("date"),
                "location_no": raw_res.get("location_no"),
                "location_name": raw_res.get("location_name"),
                "shift": raw_res.get("shift"),
                "record_count": len(raw_res.get("records", [])),
                "records": raw_res.get("records", []),
                "oic_details": raw_res.get("oic_details", {}),
                "notes_and_annotations": raw_res.get("notes"),
            },
            "expected_agent_trajectory": {
                "expected_decision": exp_decision,
                "expected_confidence_min": exp_conf,
                "expected_hard_vetoes": exp_vetoes,
                "optimal_tool_sequence": opt_seq,
                "max_acceptable_steps": max_steps,
                "requires_human_escalation": exp_decision in ("REVIEW", "FAILED"),
                "decision_rationale": decision_rationale,
                "expected_uncertain_fields": expected_uncertain_fields,
            },
        }

        with open(dest_gt, "w", encoding="utf-8") as f:
            json.dump(gt_data, f, indent=2, ensure_ascii=False)

        manifest["documents"].append({
            "filename": fn,
            "category": cat_name,
            "record_count": len(raw_res.get("records", [])),
            "expected_decision": exp_decision,
            "expected_confidence_min": exp_conf,
            "decision_rationale": decision_rationale,
            "relative_image_path": f"{cat_name}/{fn}",
            "relative_gt_path": f"{cat_name}/{fn.replace('.jpeg', '.ground_truth.json')}",
        })

# Write manifest
manifest_path = os.path.join(DEST_DIR, "benchmark_manifest.json")
with open(manifest_path, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2, ensure_ascii=False)

print(f"\nSuccessfully curated benchmark documents into {DEST_DIR}!")
print("Manifest written to:", manifest_path)
# Print distribution summary
dist = {"COMMIT": 0, "REVIEW": 0, "FAILED": 0}
for doc in manifest["documents"]:
    dist[doc["expected_decision"]] = dist.get(doc["expected_decision"], 0) + 1
print(f"\nFinal decision distribution: {dist}")
print(f"Total documents processed: {sum(dist.values())}")
