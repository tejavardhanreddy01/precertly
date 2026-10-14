# Facet joint interventions: case plan

Eleven cases against `data/policies/facet-joint-interventions.yaml` (LCD L38773 v32, article A58364 v42), labeled under `evals/GUIDELINES.md`. Each case changes one or two facts from a clean template; every other scored criterion is `met`, or `not_applicable` where its condition does not hold.

## Template patient

Clean for every variant used here:

- 6 months of predominantly axial low back pain, moderate to severe, Condition coded and described in notes.
- Baseline pain on NRS 0–10 (7/10) and baseline Oswestry Disability Index (48%), scale names written in the note.
- 3 months of physical therapy and NSAIDs documented with no meaningful improvement.
- Neurological exam documented: no radicular symptoms, negative straight-leg raise.
- Lumbar MRI: facet arthropathy at L4-5 and L5-S1; no fracture, tumor, infection or significant deformity.
- No fibromyalgia or centralized pain diagnosis; no prior lumbar fusion.
- Request: lumbar region only, L4-5 and L5-S1 (two levels), no other blocks the same day.

For RFA variants, the template adds two medial branch blocks at the same levels, three weeks apart, each with (7 − 1) / 7 = 86% relief on the same NRS scale.

Pain scores are whole numbers. Relief is always computed from the same session's pre-procedure score, and the note states the calculation. From a 7/10 score that gives 86% (7 to 1), 71% (7 to 2) and 57% (7 to 3).

For repeat RFA, the template adds a thermal RFA at the same levels 14 months before the request, with 57% improvement in pain for 8 months, then a renewed naproxen trial and home exercise after the pain returned, documented as failed. Conservative care for the recurrence does not rely on care from before the ablation.

## Request block

Request-stage criteria are judged from the case's `request` block, and labels cite the field they rest on (`request.levels`).

| Field | Use |
| --- | --- |
| `date` | Request date; all other dates are relative to it (2026-10-05 in every case) |
| `service_date` | Planned date of the requested service. The two-week interval for a second diagnostic block is judged from it, because that block has not happened when it is requested |
| `procedure` | The requested procedure in plain words. The codes do not say whether a diagnostic block is a medial branch block or an intraarticular one, and that decides whether `ia-diagnostic-only-if-mbb-not-possible` applies |
| `technique` | One of `medial_branch_block`, `intraarticular_injection` or `radiofrequency_ablation`. A structured field written in the case file, not parsed from `procedure`. It exists because the procedure codes are the same for a medial branch block and an intraarticular injection, and `ia-diagnostic-only-if-mbb-not-possible` depends on which it is |
| `codes`, `region`, `levels` | Procedure codes, spinal region and facet levels |
| `same_day_blocks` | Other blocks planned for the same day; empty in every case |

## Cases

| Case | Variant | Change from template | Labels that change | Overall | Type |
| --- | --- | --- | --- | --- | --- |
| `facet-01` | initial-rfa | none (both blocks gave 86% relief, 3 weeks apart) | none | met | clean |
| `facet-02` | initial-rfa | second block gave 71% relief (NRS 7 to 2) | `two-mbbs-80pct-sustained-relief`: not_met | not_met | clean, near miss |
| `facet-03` | second-diagnostic | second block planned 10 days after the first (`request.service_date`); no exception documented | `second-diagnostic-2-weeks-after-first`: not_met | not_met | clean, near miss |
| `facet-04` | second-diagnostic | second block planned 10 days after the first; note documents the clinical reason for the exception (anticoagulation starts four days later) | none (`second-diagnostic-2-weeks-after-first`: met via the exception clause) | met | adversarial |
| `facet-05` | initial-rfa | post-block pain recorded on VAS 0–100 while baseline and first block used NRS 0–10 | `pain-scale-after-each-diagnostic`: not_met; `two-mbbs-80pct-sustained-relief`: insufficient | not_met | adversarial |
| `facet-06` | first-diagnostic | free-text note: "pain radiating down the left leg with numbness"; no workup, diagnosis or treatment; template's neuro exam removed | `no-untreated-radiculopathy`: insufficient | insufficient | adversarial, free text only |
| `facet-07` | first-diagnostic | MRI report shows an acute L2 compression fracture | `no-non-facet-pathology`: not_met | not_met | clean |
| `facet-08` | first-diagnostic | request covers three levels (L3-4, L4-5, L5-S1), including a third-level code the policy file does not route | `levels-1-to-2`: not_met | not_met | clean, request stage |
| `facet-09` | repeat-rfa | none (prior RFA 14 months ago at the same levels; 57% pain improvement on NRS for 8 months; no RFA in the last 12 months) | none (`diagnostics-repeated-after-2-years`: not_applicable) | met | clean, conditional |
| `facet-10` | repeat-rfa | as facet-09, but improvement lasted about 4 months, recorded only in a progress note, which also states that function did not improve | `prior-rfa-50pct-6-months`: not_met | not_met | adversarial, free text only |
| `facet-11` | first-diagnostic | one note says the patient completed 12 weeks of PT with no improvement; a later records review shows 2 of 12 sessions attended; neither is reconciled; no medication trial in this record | `conservative-care-failed`: insufficient | insufficient | adversarial, contradiction |

Mix: 3 met, 6 not_met, 2 insufficient; 6 clean, 5 adversarial. Variants covered: first-diagnostic, second-diagnostic, initial-rfa, repeat-rfa. `therapeutic-injection` is not covered in this round.

## Design notes

- **facet-06:** `chronic-axial-pain-functional-deficit` stays `met`. The record still documents the pain as predominantly axial, and the leg symptoms do not negate that.
- **facet-10:** the LCD's alternative for repeat RFA, "consistent improvement in the ability to perform previously painful movements and ADLs", has no stated duration. The progress note says function did not improve at all, so that alternative is closed and the case rests only on the six-month pain requirement.
- **facet-11:** the chart has no NSAID or other medication trial. A failed medication trial alone could satisfy `conservative-care-failed`, which would hide the contradiction about physical therapy.

## Known limitations

- Routine pain scores (LOINC 72514-3) and analgesic prescriptions are left out of the background patient, because they bear directly on the pain and conservative-care criteria. Real charts have this noise; these cases do not test it.
- All eleven cases share one background patient.
- `therapeutic-injection` is not covered.
- Single annotator; not clinically validated.

## Checks before a case is accepted

- Every changed label cites the quoted sentence it follows from.
- Every scored criterion in the variant has a label.
- The overall outcome computed from the labels matches the table.
- The bundle contains the evidence the labels cite, with the resource ids listed.
