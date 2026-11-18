"""
Script to curate 40 documents from C:\\Users\\sadee\\Downloads\\watchguard\\watchguard
into 4 distinct evaluation categories (10 clean, 10 multi_record, 10 messy, 10 adversarial)
and generate comprehensive ground-truth files for agent evaluation.
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

# 2. Define the 40 specific filenames and their characteristics
curation = {
    "clean": [
        ("94704599305_20260722_154026_811094_fd6e4104.jpeg",
         "Clean standard printed Daily Attendance Form, upright orientation, 2 guards, clear signatures.",
         "High confidence standard single-shift extraction with minimal step count.",
         "COMMIT", 0.95, [], ["read_page", "submit_record"], 2),

        ("94704599305_20260722_154029_096572_72d78ce8.jpeg",
         "Clean printed form, 2 guards, clear in/out times (07:00-19:00, 19:00-07:00), signed OIC.",
         "Extracting distinct Day & Night guard shifts on clean layout.",
         "COMMIT", 0.95, [], ["read_page", "submit_record"], 2),

        ("94704599305_20260722_154033_135129_f5b10d03.jpeg",
         "Standard printed form, 2 guards, clean tabular columns, OIC signed at bottom.",
         "Field-level exact match across tabular ranks and employee IDs.",
         "COMMIT", 0.95, [], ["read_page", "submit_record"], 2),

        ("94704599305_20260722_154035_679167_0912e457.jpeg",
         "Upright printed form, 2 guards, clear ranks (OIC / SSO), OC signed at bottom right.",
         "Rank hierarchy extraction (OIC vs SSO vs JSO).",
         "COMMIT", 0.95, [], ["read_page", "submit_record"], 2),

        ("94704599305_20260722_153817_160558_8c28faa3.jpeg",
         "Clean single guard roster, Nihal Samarasekara, complete service no 58155.",
         "Single-line extraction without hallucinating phantom rows.",
         "COMMIT", 0.95, [], ["read_page", "submit_record"], 2),

        ("94740835902_20260722_070016_886976_d285cb76.jpeg",
         "Clean standard single-entry duty form, clear timestamps 07:00-19:00.",
         "Exact timing extraction and signature detection.",
         "COMMIT", 0.95, [], ["read_page", "submit_record"], 2),

        ("94740835902_20260722_071031_248195_24fd1593.jpeg",
         "Standard 2-guard duty roster, clearly printed headers, verified signatures.",
         "Standard multi-column table extraction.",
         "COMMIT", 0.95, [], ["read_page", "submit_record"], 2),

        ("94740835902_20260722_071641_280077_3bebbbf5.jpeg",
         "Clean 3-guard roster, SO signature at bottom, clear service IDs.",
         "Sequential 3-row tabular parsing and verification.",
         "COMMIT", 0.95, [], ["read_page", "submit_record"], 2),

        ("94740835902_20260722_114200_113177_14fcfbcd.jpeg",
         "Clean 2-guard roster with explicit Day and Night split, OIC signed.",
         "Accurate split between Day and Night shift types.",
         "COMMIT", 0.95, [], ["read_page", "submit_record"], 2),

        ("94743518058_20260722_115406_24a1b0b5.jpeg",
         "Clean 6-guard printed roster for Sofia Land, structured rows with complete timestamps.",
         "Multi-row scaling on pristine tabular layout.",
         "COMMIT", 0.95, [], ["read_page", "submit_record"], 2),
    ],

    "multi_record": [
        ("94776219512_20260722_182317_50df2c5e.jpeg",
         "Dense 24-guard roster for Vavuniya (largest roster in dataset).",
         "Stress-testing line-item extraction without row truncation or index drift.",
         "COMMIT", 0.90, [], ["read_page", "submit_record"], 3),

        ("94740835902_20260722_095453_843779_f05954c3.jpeg",
         "18-guard roster for Kahatagaha Graphite with OIC service no 75936.",
         "Accurate parsing of dense industrial security roster.",
         "COMMIT", 0.90, [], ["read_page", "submit_record"], 3),

        ("94740835902_20260722_160316_814412_3e30e16b.jpeg",
         "18-guard roster: 9 guards performing double shifts (Day & Night).",
         "Entity resolution matching identical employee IDs across distinct shifts.",
         "COMMIT", 0.90, [], ["read_page", "submit_record"], 3),

        ("94740835902_20260722_163112_032860_ebba4b9e.jpeg",
         "18-guard roster with dense handwriting in tabular grid.",
         "OCR grounding and row alignment across 18 consecutive rows.",
         "COMMIT", 0.90, [], ["read_page", "submit_record"], 3),

        ("94771079086_20260722_223900_34c56e3b.jpeg",
         "16-guard roster for EKHO Surf Bentota with OC signoff.",
         "High-volume hotel resort security roster extraction.",
         "COMMIT", 0.90, [], ["read_page", "submit_record"], 3),

        ("94740835902_20260722_211357_550570_f5ec3f64.jpeg",
         "14-guard roster for Hotel Santhani Kandy, dual shift (06:00-18:00 & 18:00-06:00).",
         "Handling non-standard 12-hour shift windows (06:00 instead of 07:00).",
         "COMMIT", 0.90, [], ["read_page", "submit_record"], 3),

        ("94776219512_20260722_182314_814234_d04a6fc6.jpeg",
         "13-guard night shift roster for Vavuniya with CSO signature.",
         "Large single-shift batch extraction.",
         "COMMIT", 0.90, [], ["read_page", "submit_record"], 3),

        ("94776219512_20260722_182310_08d2479e.jpeg",
         "13-guard roster for 11 Wilagall Hotel (5 Day guards, 8 Night guards).",
         "Asymmetric Day vs Night shift distribution.",
         "COMMIT", 0.90, [], ["read_page", "submit_record"], 3),

        ("94771079086_20260722_223847_043477_a141b712.jpeg",
         "12-guard roster for Uga Yala with designated security points (GIR, Car Park).",
         "Extracting station/post annotations within guard records.",
         "COMMIT", 0.90, [], ["read_page", "submit_record"], 3),

        ("94762340597_20260722_150728_215f62f8.jpeg",
         "10-guard roster for MERBOK MDF Lanka (5 Day guards, 5 Night guards).",
         "Balanced multi-guard extraction with precise in/out times.",
         "COMMIT", 0.92, [], ["read_page", "submit_record"], 2),
    ],

    "messy": [
        ("94704599305_20260722_154030_883528_9f00a738.jpeg",
         "Printed form rotated 90 degrees counter-clockwise.",
         "Agent reasoning to recognize orientation and read rotated tabular text.",
         "COMMIT", 0.88, [], ["read_page", "submit_record"], 2),

        ("94740835902_20260722_205305_128919_2f28fb84.jpeg",
         "Dense 18-record Kahatagaha roster rotated 90 degrees CCW.",
         "Combining high row count with 90-degree image rotation.",
         "COMMIT", 0.85, [], ["read_page", "submit_record"], 3),

        ("94768524617_20260722_074816_731386_f6f9661f.jpeg",
         "LRC security form rotated 90 degrees CCW.",
         "Auto-rotation and line alignment under distortion.",
         "COMMIT", 0.88, [], ["read_page", "submit_record"], 2),

        ("94771079086_20260722_223432_58fbbdd2.jpeg",
         "Malabar Hill roster rotated 90 degrees CCW, 7 guards.",
         "Reading rotated handwritten rows across Day and Night sections.",
         "COMMIT", 0.86, [], ["read_page", "submit_record"], 2),

        ("94740835902_20260722_114606_834689_9c34ef7b.jpeg",
         "Roster with names written in Sinhala script alongside English initials.",
         "Multilingual OCR robustness (handling non-Latin script without garbled output).",
         "COMMIT", 0.85, [], ["read_page", "submit_record"], 3),

        ("94704599305_20260722_153611_733686_ac176f54.jpeg",
         "Handwritten lined notebook page containing side-by-side Day and Night shifts.",
         "Unstructured notebook parsing without standard printed column borders.",
         "COMMIT", 0.85, [], ["read_page", "submit_record"], 3),

        ("94740835902_20260722_161044_267081_a5a92da6.jpeg",
         "Handwritten logbook page for Anasa Hotel with 12 records.",
         "Discerning low-contrast handwriting in an informal bound book.",
         "COMMIT", 0.85, [], ["read_page", "submit_record"], 3),

        ("94773926781_20260722_115026_421061_8774880c.jpeg",
         "Notebook-style capture with two separate forms crammed onto one page.",
         "Segmenting dual tables on a single unstructured notebook page.",
         "COMMIT", 0.86, [], ["read_page", "submit_record"], 3),

        ("94774017396_20260722_171910_936128_6b7f3dc5.jpeg",
         "Notebook-style roster with handwritten 'D/P' (Duty/Point) column.",
         "Handling idiosyncratic non-standard column headers in notebook.",
         "COMMIT", 0.85, [], ["read_page", "submit_record"], 3),

        ("94775795006_20260722_121257_661918_b10a2eb7.jpeg",
         "Notebook-style roster for Hindu College Colombo with shadow / low contrast.",
         "Extraction under ambient shadow and mobile camera perspective skew.",
         "COMMIT", 0.85, [], ["read_page", "submit_record"], 3),
    ],

    "adversarial": [
        ("94740835902_20260722_001553_180269_6c61f32c.jpeg",
         "12-record roster where Out-Time for all night shift guards is completely unentered.",
         "Agent must NOT hallucinate out-times. Must send to review or flag incomplete shifts.",
         "SEND_TO_REVIEW", 0.70, ["incomplete_shift_timestamps"], ["read_page", "submit_record"], 2),

        ("94740835902_20260722_155748_242350_c9e28f32.jpeg",
         "Form where 'Day Shift' text is visibly crossed out above Shift Type.",
         "Recognizing strike-through / crossed-out intent and resolving actual shift type.",
         "SEND_TO_REVIEW", 0.75, ["strike_through_correction"], ["read_page", "submit_record"], 2),

        ("94740835902_20260722_001902_907265_70d397ae.jpeg",
         "Round organizational stamp present, but OIC name and service number are omitted.",
         "Detecting missing OIC identity without hallucinating an officer name.",
         "SEND_TO_REVIEW", 0.75, ["missing_oic_identity"], ["read_page", "submit_record"], 2),

        ("94740835902_20260722_110135_751336_ef1e6d06.jpeg",
         "Form header has July 11 date, but OIC verification stamp is dated 2026/07/14.",
         "Detecting conflicting document date vs verification date.",
         "SEND_TO_REVIEW", 0.78, ["date_conflict_stamp_vs_header"], ["read_page", "submit_record"], 2),

        ("94740835902_20260722_155749_182779_5e5ca80b.jpeg",
         "Roster with date discrepancy between duty date and secondary supervisor stamp.",
         "Flagging date discrepancy for human review rather than blindly committing.",
         "SEND_TO_REVIEW", 0.78, ["date_discrepancy"], ["read_page", "submit_record"], 2),

        ("94740835902_20260722_204753_546802_00bfb2d7.jpeg",
         "Guard 75372 listed for both Day and Night shifts with impossible overlap in times.",
         "Arithmetic / temporal veto check: detecting double-shift time contradiction.",
         "SEND_TO_REVIEW", 0.72, ["shift_time_overlap_contradiction"], ["read_page", "submit_record"], 2),

        ("94768524617_20260722_201531_733560_0df01bc5.jpeg",
         "Duplicate capture of an earlier document under severe flash glare.",
         "Handling harsh glare occlusion and verifying document consistency.",
         "SEND_TO_REVIEW", 0.75, ["glare_occlusion_audit"], ["read_page", "submit_record"], 2),

        ("94775795006_20260722_121300_865181_05ebf361.jpeg",
         "Grand Hyatt roster using EPF numbers (Employee Provident Fund) instead of Service Numbers.",
         "Identifier mismatch: recognizing employee ID system discrepancy.",
         "SEND_TO_REVIEW", 0.75, ["epf_id_scheme_mismatch"], ["read_page", "submit_record"], 2),

        ("94776219512_20260722_182308_015fbe91.jpeg",
         "Roster missing mandatory OIC sign-off; only lower-rank SSO signature is present.",
         "Governance compliance: detecting lack of authorized sign-off rank.",
         "SEND_TO_REVIEW", 0.75, ["unauthorized_signoff_rank"], ["read_page", "submit_record"], 2),

        ("94743518058_20260722_115428_09cf313e.jpeg",
         "Incomplete shift boundary entries with ambiguous location code.",
         "Entity resolution failure check: flagging unresolvable location ID.",
         "SEND_TO_REVIEW", 0.70, ["unresolved_location_entity"], ["read_page", "submit_record"], 2),
    ],
}

# 3. Create folders and populate
os.makedirs(DEST_DIR, exist_ok=True)
manifest = {
    "title": "Watchguard Roster Agent Benchmark Dataset",
    "total_documents": 40,
    "categories": {
        "clean": {"count": 10, "target": "Standard single-shift, clean layout, complete fields, high confidence commits."},
        "multi_record": {"count": 10, "target": "Dense rosters (10-24 guards), double shifts, multi-shift stress testing."},
        "messy": {"count": 10, "target": "Rotated 90 deg, handwritten notebook logbooks, Sinhala script, skewed camera capture."},
        "adversarial": {"count": 10, "target": "Missing out-times, crossed-out shifts, date conflicts, EPF IDs, unverified OIC."},
    },
    "documents": [],
}

for cat_name, items in curation.items():
    cat_dir = os.path.join(DEST_DIR, cat_name)
    os.makedirs(cat_dir, exist_ok=True)

    for item_fn, desc, eval_focus, exp_decision, exp_conf, exp_vetoes, opt_seq, max_steps in items:
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
                "requires_human_escalation": exp_decision in ("SEND_TO_REVIEW", "FLAG"),
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
            "relative_image_path": f"{cat_name}/{fn}",
            "relative_gt_path": f"{cat_name}/{fn.replace('.jpeg', '.ground_truth.json')}",
        })

# Write manifest
manifest_path = os.path.join(DEST_DIR, "benchmark_manifest.json")
with open(manifest_path, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2, ensure_ascii=False)

print(f"\nSuccessfully curated 40 benchmark documents into {DEST_DIR}!")
print("Manifest written to:", manifest_path)
