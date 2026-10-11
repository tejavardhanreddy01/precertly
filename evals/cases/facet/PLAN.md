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

For RFA variants, the template adds two medial branch blocks at the same levels, at least two weeks apart, each with ≥80% relief on the same NRS scale.

## Cases

| Case | Variant | Change from template | Labels that change | Overall | Type |
| --- | --- | --- | --- | --- | --- |
| `facet-01` | initial-rfa | none (blocks gave 85% and 90% relief, 3 weeks apart) | none | met | clean |
| `facet-02` | initial-rfa | second block gave 70% relief | `two-mbbs-80pct-sustained-relief`: not_met | not_met | clean, near miss |
| `facet-03` | second-diagnostic | second block 10 days after the first; no exception documented | `second-diagnostic-2-weeks-after-first`: not_met | not_met | clean, near miss |
| `facet-04` | second-diagnostic | second block 10 days after the first; note documents the clinical reason for the exception | none (`second-diagnostic-2-weeks-after-first`: met via the exception clause) | met | adversarial |
| `facet-05` | initial-rfa | post-block pain recorded on VAS 0–100 while baseline and first block used NRS 0–10 | `pain-scale-after-each-diagnostic`: not_met; `two-mbbs-80pct-sustained-relief`: insufficient | not_met | adversarial |
| `facet-06` | first-diagnostic | free-text note: "pain radiating down the left leg with numbness"; no workup, diagnosis or treatment; template's neuro exam removed | `no-untreated-radiculopathy`: insufficient | insufficient | adversarial, free text only |
| `facet-07` | first-diagnostic | MRI report shows an acute L2 compression fracture | `no-non-facet-pathology`: not_met | not_met | clean |
| `facet-08` | first-diagnostic | request covers three levels (L3-4, L4-5, L5-S1) | `levels-1-to-2`: not_met | not_met | clean, request stage |
| `facet-09` | repeat-rfa | prior RFA 14 months ago at the same levels; 60% pain improvement on NRS for 8 months; no RFA in the last 12 months | none (`diagnostics-repeated-after-2-years`: not_applicable) | met | clean, conditional |
| `facet-10` | repeat-rfa | as facet-09, but improvement lasted 4 months, recorded only in a progress note | `prior-rfa-50pct-6-months`: not_met | not_met | adversarial, free text only |
| `facet-11` | first-diagnostic | one note says "completed 12 weeks of PT, no improvement"; a later note says the patient attended 2 of 12 sessions; neither is reconciled | `conservative-care-failed`: insufficient | insufficient | adversarial, contradiction |

Mix: 3 met, 6 not_met, 2 insufficient; 6 clean, 5 adversarial. Variants covered: first-diagnostic, second-diagnostic, initial-rfa, repeat-rfa. `therapeutic-injection` is not covered in this round.

## Checks before a case is accepted

- Every changed label cites the quoted sentence it follows from.
- Every scored criterion in the variant has a label.
- The overall outcome computed from the labels matches the table.
- The bundle contains the evidence the labels cite, with the resource ids listed.
