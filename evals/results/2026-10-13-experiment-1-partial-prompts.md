# Experiment 1: Partial prompts where rule-plus-model and the shadow verdict disagree

Each prompt below is rebuilt from the ordered evidence list stored in the results file, with the code at the commit that ran (`206d985`). The results file stores the list, not the prompt text; prompt building is deterministic, and the evidence list is identical in runs 1a and 1b, so one prompt serves both. The system prompt is `SYSTEM` in `src/precertly/judgment/prompt.py`, unchanged from the baseline.

## facet-03: `second-diagnostic-2-weeks-after-first`

Rule result: partial. Inputs: Procedure/mbb1, request.service_date.

### Prompt

````text
CRITERION
Id: second-diagnostic-2-weeks-after-first
Statement: The second diagnostic procedure is performed at least 2 weeks after the first, unless clinical circumstances requiring an exception are clearly documented.
Policy text (LCD L38773): The second diagnostic procedure may only be performed a minimum of 2 weeks after the initial diagnostic procedure. Clinical circumstances that necessitate an exception to the two-week duration may be considered on an individual basis and must be clearly documented in the medical record.
Thresholds: min_interval_weeks=2, documented_exception_allowed=True
Condition: none; the criterion always applies.

REQUEST DATE: 2026-10-05

EVIDENCE
[E1] (request)
Request date: 2026-10-05

[E2] (request)
Planned date of service: 2026-10-08

[E3] (request)
Requested procedure: second diagnostic medial branch block

[E4] (request)
Technique: medial_branch_block

[E5] (request)
Procedure codes: 64493, 64494

[E6] (request)
Spinal region: lumbar

[E7] (request)
Levels: L4-5, L5-S1

[E8] (request)
Other blocks planned for the same day: none

[E9] (chart, Procedure, dated 2026-08-02)
Procedure: Referral to cardiology service (procedure) (SNOMED 183519002)
Status: completed
Performed: 2026-08-02
Reason: Abnormal findings diagnostic imaging heart+coronary circulat (finding) (SNOMED 274531002)

[E10] (chart, DocumentReference, dated 2026-09-30)
2026-09-30
Pain clinic follow-up

Result of the first diagnostic medial branch block at L4-5 and L5-S1 on 2026-09-28, scored on the Numerical Rating Scale (NRS) 0-10: pain 7/10 before and 1/10 after, (7 - 1) / 7 = 86% relief, lasting about 6 hours, consistent with bupivacaine.

This was the patient's first diagnostic facet procedure.
Plan: a second, confirmatory medial branch block at the same levels (L4-5 and L5-S1, lumbar region) on 2026-10-08, 10 days after the first block. No other injections are planned for that day.

[E11] (chart, DocumentReference, dated 2024-07-09)
## Plan
Patient was given the following immunizations: influenza, split virus, trivalent, pf. 
The following procedures were conducted:
- medication reconciliation (procedure)
- assessment of health and social care needs (procedure)
- assessment of anxiety (procedure)
- screening for domestic abuse (procedure)
- depression screening (procedure)
- depression screening (procedure)
- assessment of substance use (procedure)
- assessment using alcohol use disorders identification test - consumption (procedure)
The following lab reports were completed:
- generalized anxiety disorder 7 item (gad-7)
- humiliation, afraid, rape, and kick questionnaire [hark]
- patient health questionnaire 2 item (phq-2) [reported]
- alcohol use disorder identification test - consumption [audit-c]

[E12] (chart, Procedure, dated 2026-07-21)
Procedure: Depression screening (procedure) (SNOMED 171207006)
Status: completed
Performed: 2026-07-21

[E13] (chart, DocumentReference, dated 2026-07-21)
## Plan
Patient was given the following immunizations: influenza, split virus, trivalent, pf, pneumococcal conjugate pcv 13. 
The following procedures were conducted:
- assessment of health and social care needs (procedure)
- assessment of anxiety (procedure)
- assessment using morse fall scale (procedure)
- screening for domestic abuse (procedure)
- depression screening (procedure)
- depression screening (procedure)
The following lab reports were completed:
- lipid panel with direct ldl - serum or plasma
- generalized anxiety disorder 7 item (gad-7)
- morse fall scale panel
- humiliation, afraid, rape, and kick questionnaire [hark]
- patient health questionnaire 2 item (phq-2) [reported]
The patient was prescribed the following medications:
- clopidogrel 75 mg oral tablet
- simvastatin 20 mg oral tablet
- 24 hr metoprolol succinate 100 mg extended release oral tablet
- nitroglycerin 0.4 mg/actuat mucosal spray

[E14] (chart, Procedure, dated 2024-07-09)
Procedure: Depression screening (procedure) (SNOMED 171207006)
Status: completed
Performed: 2024-07-09

[E15] (chart, Procedure, dated 2024-07-09)
Procedure: Medication reconciliation (procedure) (SNOMED 430193006)
Status: completed
Performed: 2024-07-09

[E16] (chart, DocumentReference, dated 2026-08-02)
2026-08-02

# Chief Complaint
No complaints.

# History of Present Illness
Lucila204 Kanesha23 is a 65 year-old nonhispanic white female. Patient has a history of streptococcal sore throat (disorder), teething syndrome, medication review due (situation), unemployed (finding), viral sinusitis (disorder), victim of intimate partner abuse (finding), gingival enlargement, dental caries, generalised chronic periodontitis, part-time employment (finding), acute bronchitis (disorder), limited social contact (finding), not in labor force (finding), full-time employment (finding), social isolation (finding), stress (finding).

# Social History
Patient is single. Patient quit smoking at age 16.
 Patient identifies as heterosexual.

Patient comes from a high socioeconomic background.
 Patient is a college graduate.
Patient currently has Medicare.

# Allergies
No Known Allergies.

# Medications
acetaminophen 325 mg oral tablet

# Assessment and Plan
Patient is presenting with abnormal findings diagnostic imaging heart+coronary circulat (finding). 


## Plan

The following procedures were conducted:
- consultation for treatment (procedure)
- angiography of coronary artery (procedure)
- post anesthesia care management (procedure)
- referral to cardiology service (procedure)

COMPUTED FACT
The planned service date 2026-10-08 is 10 days after the coded facet injection on 2026-09-28; the minimum is 14 days. Only a clearly documented clinical exception can satisfy the criterion.
This was computed from the coded record and is correct. Do not recompute it, contradict it, or decide the criterion against it. Judge only what it leaves open, from the evidence above. You may answer without a citation if the computed fact alone settles the criterion.

Judge the criterion from this evidence.
````

### Run 1a

- Model's outcome: `not_met`, confidence 1.0
- Rationale: The planned second diagnostic medial branch block is scheduled for 2026-10-08, which is 10 days after the first block on 2026-09-28. The policy requires a minimum of 2 weeks (14 days) between diagnostic procedures. The computed fact confirms the interval is insufficient, and there is no documentation of clinical circumstances requiring an exception to the two-week duration. Therefore, the criterion is not met.
- Verified citations kept: none
- Invalidation: cited evidence 'COMPUTED FACT' was not in the prompt
- Final outcome: `insufficient`
- Shadow (model-only) outcome: `not_met`; shadow invalidation: none

### Run 1b

- Model's outcome: `not_met`, confidence 1.0
- Rationale: The planned second diagnostic medial branch block is scheduled for 2026-10-08, which is 10 days after the first block on 2026-09-28. The policy requires a minimum of 2 weeks (14 days) between diagnostic procedures. The computed fact confirms the interval is insufficient, and there is no documentation of clinical circumstances requiring an exception to the two-week duration. Therefore, the criterion is not met.
- Verified citations kept: none
- Invalidation: cited evidence 'COMPUTED FACT' was not in the prompt
- Final outcome: `insufficient`
- Shadow (model-only) outcome: `not_met`; shadow invalidation: none

## facet-03: `diagnostic-sessions-max-4`

Rule result: partial. Inputs: Procedure/mbb1, request.service_date.

### Prompt

````text
CRITERION
Id: diagnostic-sessions-max-4
Statement: No more than 4 diagnostic sessions per covered spinal region per rolling 12 months.
Policy text (LCD L38773): For each covered spinal region no more than four (4) diagnostic joint sessions will be reimbursed per rolling 12 months, in recognition that the pain generator cannot always be identified with the initial and confirmatory diagnostic procedure.
Thresholds: max_sessions=4, window_months=12, per=spinal region
Condition: none; the criterion always applies.

REQUEST DATE: 2026-10-05

EVIDENCE
[E1] (request)
Request date: 2026-10-05

[E2] (request)
Planned date of service: 2026-10-08

[E3] (request)
Requested procedure: second diagnostic medial branch block

[E4] (request)
Technique: medial_branch_block

[E5] (request)
Procedure codes: 64493, 64494

[E6] (request)
Spinal region: lumbar

[E7] (request)
Levels: L4-5, L5-S1

[E8] (request)
Other blocks planned for the same day: none

[E9] (chart, DocumentReference, dated 2026-08-10)
2026-08-10
Pain clinic initial evaluation

History: low back pain since 2026-04-03, now present for 4 months. The pain is predominantly axial, across the lower lumbar spine, moderate to severe, worse with extension, rotation and prolonged standing, and eased by sitting. It does not radiate below the buttocks. The patient can no longer stand longer than 15 minutes, has stopped gardening and needs help with housework.

Conservative management: naproxen 500 mg twice daily since 2026-04-10 with minimal benefit; a 12-week course of physical therapy (2026-05-04 to 2026-08-03) with no meaningful improvement in pain or function.

Pain and function at baseline:
- Pain: 7/10 on the Numerical Rating Scale (NRS) 0-10.
- Disability: 48% on the Oswestry Disability Index (ODI), severe disability.

Past history: no prior spine surgery, including no lumbar fusion. No history of fibromyalgia or any widespread or centralized pain syndrome. No prior radiofrequency ablation or spinal injections.

Neurological exam: strength 5/5 in both legs. Sensation intact to light touch. Reflexes 2+ and symmetric at the knees and ankles. Straight-leg raise negative on both sides. No radicular symptoms and no neurogenic claudication.
Spine exam: pain reproduced by lumbar extension and rotation; tenderness over the L4-5 and L5-S1 facet joints on both sides.

Imaging: lumbar MRI of 2026-07-20 shows facet arthropathy at L4-5 and L5-S1 with no fracture, tumor, infection or significant deformity.

[E10] (chart, DocumentReference, dated 2026-09-28)
2026-09-28
Procedure note, diagnostic medial branch block 1

Indication: chronic axial low back pain with suspected lumbar facet syndrome.
Levels: bilateral L4-5 and L5-S1 (medial branches of L3 and L4 and the L5 dorsal ramus).
Technique: fluoroscopic guidance, 0.5 mL of 0.5% bupivacaine at each target nerve. No corticosteroid. No sedation. No other injections were performed today.

Pain assessment:
- Before the block: 7/10 on the Numerical Rating Scale (NRS) 0-10, index low back pain with extension and rotation.
- 30 minutes after the block: 1/10 on the same scale, with the same provocative movements.
- Relief of index pain, from today's pre-block score: (7 - 1) / 7 = 86%.
The patient kept a pain diary; relief lasted about 6 hours, consistent with the duration of bupivacaine, and pain then returned to its earlier level.

No complications. Discharged in stable condition.

[E11] (chart, DocumentReference, dated 2026-08-10)
Assessment: chronic axial low back pain, most consistent with lumbar facet syndrome at L4-5 and L5-S1.
Plan: diagnostic medial branch blocks at L4-5 and L5-S1; if two blocks confirm the diagnosis, radiofrequency ablation at those levels. Pain will be recorded on the same Numerical Rating Scale (NRS) 0-10 before and after each block. No prior diagnostic facet procedures. No other injections are planned on the day of a block.

[E12] (chart, DocumentReference, dated 2026-09-30)
2026-09-30
Pain clinic follow-up

Result of the first diagnostic medial branch block at L4-5 and L5-S1 on 2026-09-28, scored on the Numerical Rating Scale (NRS) 0-10: pain 7/10 before and 1/10 after, (7 - 1) / 7 = 86% relief, lasting about 6 hours, consistent with bupivacaine.

This was the patient's first diagnostic facet procedure.
Plan: a second, confirmatory medial branch block at the same levels (L4-5 and L5-S1, lumbar region) on 2026-10-08, 10 days after the first block. No other injections are planned for that day.

[E13] (chart, DocumentReference, dated 2024-07-09)
## Plan
Patient was given the following immunizations: influenza, split virus, trivalent, pf. 
The following procedures were conducted:
- medication reconciliation (procedure)
- assessment of health and social care needs (procedure)
- assessment of anxiety (procedure)
- screening for domestic abuse (procedure)
- depression screening (procedure)
- depression screening (procedure)
- assessment of substance use (procedure)
- assessment using alcohol use disorders identification test - consumption (procedure)
The following lab reports were completed:
- generalized anxiety disorder 7 item (gad-7)
- humiliation, afraid, rape, and kick questionnaire [hark]
- patient health questionnaire 2 item (phq-2) [reported]
- alcohol use disorder identification test - consumption [audit-c]

[E14] (chart, DocumentReference, dated 2026-07-21)
## Plan
Patient was given the following immunizations: influenza, split virus, trivalent, pf, pneumococcal conjugate pcv 13. 
The following procedures were conducted:
- assessment of health and social care needs (procedure)
- assessment of anxiety (procedure)
- assessment using morse fall scale (procedure)
- screening for domestic abuse (procedure)
- depression screening (procedure)
- depression screening (procedure)
The following lab reports were completed:
- lipid panel with direct ldl - serum or plasma
- generalized anxiety disorder 7 item (gad-7)
- morse fall scale panel
- humiliation, afraid, rape, and kick questionnaire [hark]
- patient health questionnaire 2 item (phq-2) [reported]
The patient was prescribed the following medications:
- clopidogrel 75 mg oral tablet
- simvastatin 20 mg oral tablet
- 24 hr metoprolol succinate 100 mg extended release oral tablet
- nitroglycerin 0.4 mg/actuat mucosal spray

[E15] (chart, DocumentReference, dated 2026-08-03)
2026-08-03
Physical therapy discharge summary

Course: 2026-05-04 to 2026-08-03 (12 weeks), 22 of 24 scheduled sessions attended.
Treatment: core stabilization, lumbar flexion-based exercise, manual therapy and a home exercise program, performed as instructed.
Outcome: no meaningful improvement in pain or function. Pain 7/10 at discharge, unchanged from the start of therapy. Still unable to stand longer than 15 minutes.
Recommendation: return to the referring physician; consider referral to a pain specialist.

[E16] (chart, Procedure, dated 2026-09-28)
Procedure: Diagnostic lumbar medial branch block, bilateral L4-5 and L5-S1 (CPT 64493)
Status: completed
Performed: 2026-09-28
Body site: Lumbar facet joints L4-5 and L5-S1, bilateral
Reason: Chronic low back pain (SNOMED 278860009)

COMPUTED FACT
Coded facet injection sessions in the requested region in the 12 months before the planned service date 2026-10-08: 1. The limit is 4 diagnostic sessions including the requested one. The codes do not say whether a session was diagnostic or therapeutic.
This was computed from the coded record and is correct. Do not recompute it, contradict it, or decide the criterion against it. Judge only what it leaves open, from the evidence above. You may answer without a citation if the computed fact alone settles the criterion.

Judge the criterion from this evidence.
````

### Run 1a

- Model's outcome: `met`, confidence 1.0
- Rationale: The computed fact shows only 1 coded facet injection session in the lumbar region in the 12 months before the planned service date of 2026-10-08. The requested second diagnostic medial branch block would be the second session, which is below the limit of 4 diagnostic sessions per rolling 12 months for the lumbar spinal region. Therefore, the criterion is met.
- Verified citations kept: none
- Invalidation: cited evidence 'computed' was not in the prompt
- Final outcome: `insufficient`
- Shadow (model-only) outcome: `met`; shadow invalidation: none

### Run 1b

- Model's outcome: `met`, confidence 1.0
- Rationale: The evidence shows only one prior diagnostic medial branch block in the lumbar region within the rolling 12 months before the planned service date of 2026-10-08 [computed fact]. The requested second diagnostic session would be the second session, which is within the allowed limit of four sessions per rolling 12 months per spinal region [E6, E12]. Therefore, the criterion is met.
- Verified citations kept: none
- Invalidation: cited evidence 'computed' was not in the prompt
- Final outcome: `insufficient`
- Shadow (model-only) outcome: `met`; shadow invalidation: none

## facet-04: `diagnostic-sessions-max-4`

Rule result: partial. Inputs: Procedure/mbb1, request.service_date.

### Prompt

````text
CRITERION
Id: diagnostic-sessions-max-4
Statement: No more than 4 diagnostic sessions per covered spinal region per rolling 12 months.
Policy text (LCD L38773): For each covered spinal region no more than four (4) diagnostic joint sessions will be reimbursed per rolling 12 months, in recognition that the pain generator cannot always be identified with the initial and confirmatory diagnostic procedure.
Thresholds: max_sessions=4, window_months=12, per=spinal region
Condition: none; the criterion always applies.

REQUEST DATE: 2026-10-05

EVIDENCE
[E1] (request)
Request date: 2026-10-05

[E2] (request)
Planned date of service: 2026-10-08

[E3] (request)
Requested procedure: second diagnostic medial branch block

[E4] (request)
Technique: medial_branch_block

[E5] (request)
Procedure codes: 64493, 64494

[E6] (request)
Spinal region: lumbar

[E7] (request)
Levels: L4-5, L5-S1

[E8] (request)
Other blocks planned for the same day: none

[E9] (chart, DocumentReference, dated 2026-09-30)
2026-09-30
Pain clinic follow-up

Result of the first diagnostic medial branch block at L4-5 and L5-S1 on 2026-09-28, scored on the Numerical Rating Scale (NRS) 0-10: pain 7/10 before and 1/10 after, (7 - 1) / 7 = 86% relief, lasting about 6 hours, consistent with bupivacaine.

This was the patient's first diagnostic facet procedure.
Plan: a second, confirmatory medial branch block at the same levels (L4-5 and L5-S1, lumbar region) on 2026-10-08, 10 days after the first block. No other injections are planned for that day.

Exception to the two-week interval: the patient starts therapeutic anticoagulation on 2026-10-12 ahead of a planned cardiac procedure, and spinal injections will have to be deferred for at least three months after that. To complete the diagnostic work-up before anticoagulation begins, the second block is scheduled 10 days after the first instead of the usual minimum of two weeks. The patient understands and agrees.

[E10] (chart, DocumentReference, dated 2026-08-10)
2026-08-10
Pain clinic initial evaluation

History: low back pain since 2026-04-03, now present for 4 months. The pain is predominantly axial, across the lower lumbar spine, moderate to severe, worse with extension, rotation and prolonged standing, and eased by sitting. It does not radiate below the buttocks. The patient can no longer stand longer than 15 minutes, has stopped gardening and needs help with housework.

Conservative management: naproxen 500 mg twice daily since 2026-04-10 with minimal benefit; a 12-week course of physical therapy (2026-05-04 to 2026-08-03) with no meaningful improvement in pain or function.

Pain and function at baseline:
- Pain: 7/10 on the Numerical Rating Scale (NRS) 0-10.
- Disability: 48% on the Oswestry Disability Index (ODI), severe disability.

Past history: no prior spine surgery, including no lumbar fusion. No history of fibromyalgia or any widespread or centralized pain syndrome. No prior radiofrequency ablation or spinal injections.

Neurological exam: strength 5/5 in both legs. Sensation intact to light touch. Reflexes 2+ and symmetric at the knees and ankles. Straight-leg raise negative on both sides. No radicular symptoms and no neurogenic claudication.
Spine exam: pain reproduced by lumbar extension and rotation; tenderness over the L4-5 and L5-S1 facet joints on both sides.

Imaging: lumbar MRI of 2026-07-20 shows facet arthropathy at L4-5 and L5-S1 with no fracture, tumor, infection or significant deformity.

[E11] (chart, DocumentReference, dated 2026-09-28)
2026-09-28
Procedure note, diagnostic medial branch block 1

Indication: chronic axial low back pain with suspected lumbar facet syndrome.
Levels: bilateral L4-5 and L5-S1 (medial branches of L3 and L4 and the L5 dorsal ramus).
Technique: fluoroscopic guidance, 0.5 mL of 0.5% bupivacaine at each target nerve. No corticosteroid. No sedation. No other injections were performed today.

Pain assessment:
- Before the block: 7/10 on the Numerical Rating Scale (NRS) 0-10, index low back pain with extension and rotation.
- 30 minutes after the block: 1/10 on the same scale, with the same provocative movements.
- Relief of index pain, from today's pre-block score: (7 - 1) / 7 = 86%.
The patient kept a pain diary; relief lasted about 6 hours, consistent with the duration of bupivacaine, and pain then returned to its earlier level.

No complications. Discharged in stable condition.

[E12] (chart, DocumentReference, dated 2026-08-10)
Assessment: chronic axial low back pain, most consistent with lumbar facet syndrome at L4-5 and L5-S1.
Plan: diagnostic medial branch blocks at L4-5 and L5-S1; if two blocks confirm the diagnosis, radiofrequency ablation at those levels. Pain will be recorded on the same Numerical Rating Scale (NRS) 0-10 before and after each block. No prior diagnostic facet procedures. No other injections are planned on the day of a block.

[E13] (chart, DocumentReference, dated 2024-07-09)
## Plan
Patient was given the following immunizations: influenza, split virus, trivalent, pf. 
The following procedures were conducted:
- medication reconciliation (procedure)
- assessment of health and social care needs (procedure)
- assessment of anxiety (procedure)
- screening for domestic abuse (procedure)
- depression screening (procedure)
- depression screening (procedure)
- assessment of substance use (procedure)
- assessment using alcohol use disorders identification test - consumption (procedure)
The following lab reports were completed:
- generalized anxiety disorder 7 item (gad-7)
- humiliation, afraid, rape, and kick questionnaire [hark]
- patient health questionnaire 2 item (phq-2) [reported]
- alcohol use disorder identification test - consumption [audit-c]

[E14] (chart, DocumentReference, dated 2026-08-03)
2026-08-03
Physical therapy discharge summary

Course: 2026-05-04 to 2026-08-03 (12 weeks), 22 of 24 scheduled sessions attended.
Treatment: core stabilization, lumbar flexion-based exercise, manual therapy and a home exercise program, performed as instructed.
Outcome: no meaningful improvement in pain or function. Pain 7/10 at discharge, unchanged from the start of therapy. Still unable to stand longer than 15 minutes.
Recommendation: return to the referring physician; consider referral to a pain specialist.

[E15] (chart, DocumentReference, dated 2026-07-21)
## Plan
Patient was given the following immunizations: influenza, split virus, trivalent, pf, pneumococcal conjugate pcv 13. 
The following procedures were conducted:
- assessment of health and social care needs (procedure)
- assessment of anxiety (procedure)
- assessment using morse fall scale (procedure)
- screening for domestic abuse (procedure)
- depression screening (procedure)
- depression screening (procedure)
The following lab reports were completed:
- lipid panel with direct ldl - serum or plasma
- generalized anxiety disorder 7 item (gad-7)
- morse fall scale panel
- humiliation, afraid, rape, and kick questionnaire [hark]
- patient health questionnaire 2 item (phq-2) [reported]
The patient was prescribed the following medications:
- clopidogrel 75 mg oral tablet
- simvastatin 20 mg oral tablet
- 24 hr metoprolol succinate 100 mg extended release oral tablet
- nitroglycerin 0.4 mg/actuat mucosal spray

[E16] (chart, Procedure, dated 2026-09-28)
Procedure: Diagnostic lumbar medial branch block, bilateral L4-5 and L5-S1 (CPT 64493)
Status: completed
Performed: 2026-09-28
Body site: Lumbar facet joints L4-5 and L5-S1, bilateral
Reason: Chronic low back pain (SNOMED 278860009)

COMPUTED FACT
Coded facet injection sessions in the requested region in the 12 months before the planned service date 2026-10-08: 1. The limit is 4 diagnostic sessions including the requested one. The codes do not say whether a session was diagnostic or therapeutic.
This was computed from the coded record and is correct. Do not recompute it, contradict it, or decide the criterion against it. Judge only what it leaves open, from the evidence above. You may answer without a citation if the computed fact alone settles the criterion.

Judge the criterion from this evidence.
````

### Run 1a

- Model's outcome: `met`, confidence 1.0
- Rationale: The evidence shows only one prior diagnostic medial branch block in the lumbar region within the rolling 12 months before the planned service date of 2026-10-08 [computed fact]. The requested second block would be the second session, which is within the allowed limit of four diagnostic sessions per spinal region per rolling 12 months. Therefore, the criterion is met.
- Verified citations kept: none
- Invalidation: cited evidence 'computed' was not in the prompt
- Final outcome: `insufficient`
- Shadow (model-only) outcome: `met`; shadow invalidation: none

### Run 1b

- Model's outcome: `met`, confidence 1.0
- Rationale: The computed fact shows only one coded facet injection session in the lumbar region in the 12 months before the planned service date (2026-10-08). The requested second diagnostic medial branch block would be the second session, which is within the allowed limit of four diagnostic sessions per rolling 12 months for the lumbar spinal region. Therefore, the criterion is met.
- Verified citations kept: none
- Invalidation: cited evidence 'computed' was not in the prompt
- Final outcome: `insufficient`
- Shadow (model-only) outcome: `met`; shadow invalidation: none

## facet-09: `rfa-sessions-max-2`

Rule result: partial. Inputs: request.service_date.

### Prompt

````text
CRITERION
Id: rfa-sessions-max-2
Statement: No more than 2 radiofrequency sessions per covered spinal region per rolling 12 months.
Policy text (LCD L38773): For each covered spinal region, no more than two (2) radiofrequency sessions will be reimbursed per rolling 12 months
Thresholds: max_sessions=2, window_months=12, per=spinal region
Condition: none; the criterion always applies.

REQUEST DATE: 2026-10-05

EVIDENCE
[E1] (request)
Request date: 2026-10-05

[E2] (request)
Planned date of service: 2026-10-19

[E3] (request)
Requested procedure: repeat thermal radiofrequency ablation

[E4] (request)
Technique: radiofrequency_ablation

[E5] (request)
Procedure codes: 64635, 64636

[E6] (request)
Spinal region: lumbar

[E7] (request)
Levels: L4-5, L5-S1

[E8] (request)
Other blocks planned for the same day: none

[E9] (chart, DocumentReference, dated 2026-09-21)
Plan: request prior authorization for repeat thermal radiofrequency ablation at the same levels (L4-5 and L5-S1, lumbar region, two levels). No other injections are planned for that day.

[E10] (chart, DocumentReference, dated 2026-09-21)
2026-09-21
Pain clinic follow-up

History: low back pain at L4-5 and L5-S1, treated with radiofrequency ablation on 2025-08-04, which gave (7 - 3) / 7 = 57% improvement in pain (7/10 to 3/10 on the Numerical Rating Scale (NRS) 0-10) that lasted 8 months, with better function (Oswestry Disability Index (ODI) 22% at six months against 48% at baseline). The pain returned around May 2026 and is now back to its earlier level. This has been the patient's only radiofrequency ablation.

Conservative management since the pain returned: naproxen 500 mg twice daily and a daily home exercise program since 2026-06-22, three months, with no meaningful improvement in pain or function.

Today: pain 7/10 on the Numerical Rating Scale (NRS) 0-10, moderate to severe, predominantly axial, across the lower lumbar spine, worse with extension and rotation, not radiating below the buttocks. Disability: 46% on the Oswestry Disability Index (ODI). The patient again cannot stand longer than 15 minutes.
Neurological exam: strength 5/5 in both legs, sensation intact, reflexes symmetric, straight-leg raise negative on both sides. No radicular symptoms and no neurogenic claudication.
No new trauma, fever or weight loss, and no change on exam to suggest a cause of pain other than the facet joints; the lumbar MRI of 2025-02-17 showed no fracture, tumor, infection or significant deformity. No prior spine surgery, including no lumbar fusion. No fibromyalgia or any widespread or centralized pain syndrome.

[E11] (chart, DocumentReference, dated 2025-08-04)
2025-08-04
Procedure note, radiofrequency ablation

Indication: lumbar facet syndrome at L4-5 and L5-S1, confirmed by two diagnostic medial branch blocks.
- Block 1 on 2025-04-07: pain 7/10 before and 1/10 after on the Numerical Rating Scale (NRS) 0-10, (7 - 1) / 7 = 86% relief.
- Block 2 on 2025-04-28, 3 weeks after the first: pain 7/10 before and 1/10 after on the Numerical Rating Scale (NRS) 0-10, (7 - 1) / 7 = 86% relief.
In each case relief lasted about 6 hours, consistent with bupivacaine.
Procedure: thermal radiofrequency ablation of the medial branch nerves at L4-5 and L5-S1, bilateral, under fluoroscopic guidance. Lesions at 80 degrees Celsius for 90 seconds at each site. Local anesthetic only, no sedation.
Pain before the procedure: 7/10 on the Numerical Rating Scale (NRS) 0-10.
No complications.

[E12] (chart, DocumentReference, dated 2025-11-03)
2025-11-03
Pain clinic follow-up

Follow-up 3 months after radiofrequency ablation at L4-5 and L5-S1 on 2025-08-04.
Pain today: 3/10 on the Numerical Rating Scale (NRS) 0-10, against 7/10 before the ablation: (7 - 3) / 7 = 57% improvement, unchanged since the first follow-up visit.
Function: standing tolerance about one hour; the patient has returned to gardening and housework.

[E13] (chart, DocumentReference, dated 2025-09-01)
2025-09-01
Pain clinic follow-up

Follow-up 1 months after radiofrequency ablation at L4-5 and L5-S1 on 2025-08-04.
Pain today: 3/10 on the Numerical Rating Scale (NRS) 0-10, against 7/10 before the ablation: (7 - 3) / 7 = 57% improvement, unchanged since the first follow-up visit.
Function: standing tolerance about one hour; the patient has returned to gardening and housework.

[E14] (chart, DocumentReference, dated 2025-03-17)
2025-03-17
Pain clinic initial evaluation

History: low back pain since 2024-11-04, now present for 4 months. The pain is predominantly axial, across the lower lumbar spine, moderate to severe, worse with extension, rotation and prolonged standing, and eased by sitting. It does not radiate below the buttocks. The patient can no longer stand longer than 15 minutes, has stopped gardening and needs help with housework.

Conservative management: naproxen 500 mg twice daily since 2024-11-11 with minimal benefit; a 12-week course of physical therapy (2024-12-02 to 2025-03-03) with no meaningful improvement in pain or function.

Pain and function at baseline:
- Pain: 7/10 on the Numerical Rating Scale (NRS) 0-10.
- Disability: 48% on the Oswestry Disability Index (ODI), severe disability.

Past history: no prior spine surgery, including no lumbar fusion. No history of fibromyalgia or any widespread or centralized pain syndrome. No prior radiofrequency ablation or spinal injections.

Neurological exam: strength 5/5 in both legs. Sensation intact to light touch. Reflexes 2+ and symmetric at the knees and ankles. Straight-leg raise negative on both sides. No radicular symptoms and no neurogenic claudication.
Spine exam: pain reproduced by lumbar extension and rotation; tenderness over the L4-5 and L5-S1 facet joints on both sides.

Imaging: lumbar MRI of 2025-02-17 shows facet arthropathy at L4-5 and L5-S1 with no fracture, tumor, infection or significant deformity.

[E15] (chart, DocumentReference, dated 2026-02-02)
2026-02-02
Pain clinic follow-up

Follow-up 6 months after radiofrequency ablation at L4-5 and L5-S1 on 2025-08-04.
Pain today: 3/10 on the Numerical Rating Scale (NRS) 0-10, against 7/10 before the ablation: (7 - 3) / 7 = 57% improvement, unchanged since the first follow-up visit.
Function: standing tolerance about one hour; the patient has returned to gardening and housework.

[E16] (chart, DocumentReference, dated 2026-06-22)
2026-06-22
Pain clinic follow-up

History: low back pain at L4-5 and L5-S1, treated with radiofrequency ablation on 2025-08-04. The pain returned around May 2026 and is back to 7/10 on the Numerical Rating Scale (NRS) 0-10.
Plan: restart naproxen 500 mg twice daily with food. Resume the home exercise program from the earlier physical therapy course, daily. Review in three months.

COMPUTED FACT
Coded ablation sessions in the requested region in the 12 months before the planned service date 2026-10-19: 0. With the requested session that is 1 against a limit of 2. Sessions not coded in this chart are not counted.
This was computed from the coded record and is correct. Do not recompute it, contradict it, or decide the criterion against it. Judge only what it leaves open, from the evidence above. You may answer without a citation if the computed fact alone settles the criterion.

Judge the criterion from this evidence.
````

### Run 1a

- Model's outcome: `met`, confidence 1.0
- Rationale: The computed fact shows that there are 0 coded ablation sessions in the requested region in the 12 months before the planned service date, and the requested session would be the first session against a limit of 2. Therefore, the criterion is met.
- Verified citations kept: none
- Invalidation: cited evidence 'COMPUTED FACT' was not in the prompt
- Final outcome: `insufficient`
- Shadow (model-only) outcome: `insufficient`; shadow invalidation: quote not found in DocumentReference/note-repeat-rfa-planning#0: '2026-09-21\nPlan: request prior authorization for repeat thermal radiofrequency a'

### Run 1b

- Model's outcome: `met`, confidence 1.0
- Rationale: The computed fact shows that there are 0 coded ablation sessions in the requested region in the 12 months before the planned service date, and the requested session would be the first session against a limit of 2. Therefore, the criterion is met.
- Verified citations kept: none
- Invalidation: cited evidence 'COMPUTED FACT' was not in the prompt
- Final outcome: `insufficient`
- Shadow (model-only) outcome: `met`; shadow invalidation: none

## facet-10: `rfa-sessions-max-2`

Rule result: partial. Inputs: request.service_date.

### Prompt

````text
CRITERION
Id: rfa-sessions-max-2
Statement: No more than 2 radiofrequency sessions per covered spinal region per rolling 12 months.
Policy text (LCD L38773): For each covered spinal region, no more than two (2) radiofrequency sessions will be reimbursed per rolling 12 months
Thresholds: max_sessions=2, window_months=12, per=spinal region
Condition: none; the criterion always applies.

REQUEST DATE: 2026-10-05

EVIDENCE
[E1] (request)
Request date: 2026-10-05

[E2] (request)
Planned date of service: 2026-10-19

[E3] (request)
Requested procedure: repeat thermal radiofrequency ablation

[E4] (request)
Technique: radiofrequency_ablation

[E5] (request)
Procedure codes: 64635, 64636

[E6] (request)
Spinal region: lumbar

[E7] (request)
Levels: L4-5, L5-S1

[E8] (request)
Other blocks planned for the same day: none

[E9] (chart, DocumentReference, dated 2026-09-21)
2026-09-21
Pain clinic follow-up

History: low back pain at L4-5 and L5-S1, treated with radiofrequency ablation on 2025-08-04, which gave (7 - 3) / 7 = 57% improvement in pain (7/10 to 3/10 on the Numerical Rating Scale (NRS) 0-10) that lasted about 4 months, with no improvement in function. The pain returned around January 2026 and is now back to its earlier level. This has been the patient's only radiofrequency ablation.

Conservative management since the pain returned: naproxen 500 mg twice daily and a daily home exercise program since 2026-06-22, three months, with no meaningful improvement in pain or function.

Today: pain 7/10 on the Numerical Rating Scale (NRS) 0-10, moderate to severe, predominantly axial, across the lower lumbar spine, worse with extension and rotation, not radiating below the buttocks. Disability: 46% on the Oswestry Disability Index (ODI). The patient again cannot stand longer than 15 minutes.
Neurological exam: strength 5/5 in both legs, sensation intact, reflexes symmetric, straight-leg raise negative on both sides. No radicular symptoms and no neurogenic claudication.
No new trauma, fever or weight loss, and no change on exam to suggest a cause of pain other than the facet joints; the lumbar MRI of 2025-02-17 showed no fracture, tumor, infection or significant deformity. No prior spine surgery, including no lumbar fusion. No fibromyalgia or any widespread or centralized pain syndrome.

[E10] (chart, DocumentReference, dated 2026-09-21)
Plan: request prior authorization for repeat thermal radiofrequency ablation at the same levels (L4-5 and L5-S1, lumbar region, two levels). No other injections are planned for that day.

[E11] (chart, DocumentReference, dated 2025-08-04)
2025-08-04
Procedure note, radiofrequency ablation

Indication: lumbar facet syndrome at L4-5 and L5-S1, confirmed by two diagnostic medial branch blocks.
- Block 1 on 2025-04-07: pain 7/10 before and 1/10 after on the Numerical Rating Scale (NRS) 0-10, (7 - 1) / 7 = 86% relief.
- Block 2 on 2025-04-28, 3 weeks after the first: pain 7/10 before and 1/10 after on the Numerical Rating Scale (NRS) 0-10, (7 - 1) / 7 = 86% relief.
In each case relief lasted about 6 hours, consistent with bupivacaine.
Procedure: thermal radiofrequency ablation of the medial branch nerves at L4-5 and L5-S1, bilateral, under fluoroscopic guidance. Lesions at 80 degrees Celsius for 90 seconds at each site. Local anesthetic only, no sedation.
Pain before the procedure: 7/10 on the Numerical Rating Scale (NRS) 0-10.
No complications.

[E12] (chart, DocumentReference, dated 2026-01-05)
2026-01-05
Pain clinic progress note

Progress note, 5 months after radiofrequency ablation at L4-5 and L5-S1 on 2025-08-04.
After the ablation the patient's pain fell from 7/10 to 3/10 on the Numerical Rating Scale (NRS) 0-10, (7 - 3) / 7 = 57% improvement, and stayed at that level for about 4 months. In the past month it has returned to 7/10.
Function did not improve after the ablation: the Oswestry Disability Index (ODI) was 46% at the six-week visit and is 46% today, against 48% at baseline. Standing tolerance has stayed at about 15 minutes throughout and the patient did not resume gardening or housework.

[E13] (chart, DocumentReference, dated 2025-03-17)
2025-03-17
Pain clinic initial evaluation

History: low back pain since 2024-11-04, now present for 4 months. The pain is predominantly axial, across the lower lumbar spine, moderate to severe, worse with extension, rotation and prolonged standing, and eased by sitting. It does not radiate below the buttocks. The patient can no longer stand longer than 15 minutes, has stopped gardening and needs help with housework.

Conservative management: naproxen 500 mg twice daily since 2024-11-11 with minimal benefit; a 12-week course of physical therapy (2024-12-02 to 2025-03-03) with no meaningful improvement in pain or function.

Pain and function at baseline:
- Pain: 7/10 on the Numerical Rating Scale (NRS) 0-10.
- Disability: 48% on the Oswestry Disability Index (ODI), severe disability.

Past history: no prior spine surgery, including no lumbar fusion. No history of fibromyalgia or any widespread or centralized pain syndrome. No prior radiofrequency ablation or spinal injections.

Neurological exam: strength 5/5 in both legs. Sensation intact to light touch. Reflexes 2+ and symmetric at the knees and ankles. Straight-leg raise negative on both sides. No radicular symptoms and no neurogenic claudication.
Spine exam: pain reproduced by lumbar extension and rotation; tenderness over the L4-5 and L5-S1 facet joints on both sides.

Imaging: lumbar MRI of 2025-02-17 shows facet arthropathy at L4-5 and L5-S1 with no fracture, tumor, infection or significant deformity.

[E14] (chart, DocumentReference, dated 2026-06-22)
2026-06-22
Pain clinic follow-up

History: low back pain at L4-5 and L5-S1, treated with radiofrequency ablation on 2025-08-04. The pain returned around January 2026 and is back to 7/10 on the Numerical Rating Scale (NRS) 0-10.
Plan: restart naproxen 500 mg twice daily with food. Resume the home exercise program from the earlier physical therapy course, daily. Review in three months.

[E15] (chart, DocumentReference, dated 2025-03-17)
Assessment: chronic axial low back pain, most consistent with lumbar facet syndrome at L4-5 and L5-S1.
Plan: diagnostic medial branch blocks at L4-5 and L5-S1; if two blocks confirm the diagnosis, radiofrequency ablation at those levels. Pain will be recorded on the same Numerical Rating Scale (NRS) 0-10 before and after each block. No prior diagnostic facet procedures. No other injections are planned on the day of a block.

[E16] (chart, DocumentReference, dated 2025-03-03)
2025-03-03
Physical therapy discharge summary

Course: 2024-12-02 to 2025-03-03 (12 weeks), 22 of 24 scheduled sessions attended.
Treatment: core stabilization, lumbar flexion-based exercise, manual therapy and a home exercise program, performed as instructed.
Outcome: no meaningful improvement in pain or function. Pain 7/10 at discharge, unchanged from the start of therapy. Still unable to stand longer than 15 minutes.
Recommendation: return to the referring physician; consider referral to a pain specialist.

COMPUTED FACT
Coded ablation sessions in the requested region in the 12 months before the planned service date 2026-10-19: 0. With the requested session that is 1 against a limit of 2. Sessions not coded in this chart are not counted.
This was computed from the coded record and is correct. Do not recompute it, contradict it, or decide the criterion against it. Judge only what it leaves open, from the evidence above. You may answer without a citation if the computed fact alone settles the criterion.

Judge the criterion from this evidence.
````

### Run 1a

- Model's outcome: `met`, confidence 1.0
- Rationale: The computed fact shows that there are 0 coded ablation sessions in the requested region in the 12 months before the planned service date, and the requested session would be the first. The limit is 2 sessions per rolling 12 months, so the request is within the allowed limit. The criterion is met.
- Verified citations kept: none
- Invalidation: cited evidence 'computed' was not in the prompt
- Final outcome: `insufficient`
- Shadow (model-only) outcome: `not_met`; shadow invalidation: none

### Run 1b

- Model's outcome: `met`, confidence 1.0
- Rationale: The computed fact shows that there are 0 coded ablation sessions in the requested region in the 12 months before the planned service date, and the requested session would be the first. The limit is 2 sessions per rolling 12 months, so the request is within the allowed limit. The criterion is met.
- Verified citations kept: none
- Invalidation: none
- Final outcome: `met`
- Shadow (model-only) outcome: `met`; shadow invalidation: none
