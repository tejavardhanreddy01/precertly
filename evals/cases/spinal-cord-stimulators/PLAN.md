# Spinal cord stimulators: case plan

Six cases against `data/policies/spinal-cord-stimulator.yaml` (NCD 160.7 v1, LCD L36204 v23, Article A57792 v12), labeled under `evals/GUIDELINES.md`. Each case changes one or two facts from its variant's clean baseline; every other scored criterion is `met`, or `not_applicable` where its condition does not hold. Both variants are covered: trial (2 cases) and permanent (4 cases).

## Context

The policy has 17 criteria. Per variant, 11 are scored (chart or request stage) and 1 is unscored:

| | `trial` (code `63650`) | `permanent` (`63650`, `63655`, `63685`) |
| --- | --- | --- |
| Scored, chart stage | `late-resort-chronic-intractable-pain`, `other-modalities-tried-or-unsuitable`, `multidisciplinary-screening`, `screening-documented-in-chart`, `no-active-substance-abuse`, `patient-education-and-disclosure`, `psychological-screening`, `trials-max-2-per-region-lifetime`, `repeat-trial-extenuating-circumstances` (conditional), `diagnosis-and-prior-therapies-in-record` | `late-resort-chronic-intractable-pain`, `other-modalities-tried-or-unsuitable`, `multidisciplinary-screening`, `screening-documented-in-chart`, `diagnosis-and-prior-therapies-in-record`, `trial-pain-relief-before-permanent`, `trial-success-50pct-and-function`, `positive-trial-documented`, `one-permanent-stimulator-per-lifetime` |
| Scored, request stage | `leads-max-2-or-16-contacts` | `leads-max-2-or-16-contacts`, `permanent-placed-in-asc-or-hospital` |
| Provider attestation (reviewer checklist, unlabeled) | `facilities-and-personnel-available` | `facilities-and-personnel-available` |
| Payer stage | none | none |

- **Jurisdiction.** LCD L36204 and Article A57792 are Noridian Jurisdiction F documents. The policy file has no payer-stage criterion, so jurisdiction is not checked, labeled or on the checklist; only `facilities-and-personnel-available` is on the reviewer checklist.
- `63650` routes to both variants, so the case file's `variant` decides. `diagnosis-and-prior-therapies-in-record` is `severity: warning`, but the overall outcome ignores severity, so it counts in `all_of` like any other criterion. No case makes it the only failing label.
- Only `repeat-trial-extenuating-circumstances` has a `condition` (`A previous spinal cord stimulator trial failed.`, `condition_evidence: chart`). It is `not_applicable` in both trial cases, and the evaluation note states "No previous spinal cord stimulator trial or implant.", which is the chart citation the verifier requires. No permanent criterion has a condition, so permanent cases have no `not_applicable` label.
- The request block carries the dates, the procedure in plain words, the codes, and three stimulator fields: `leads`, `contacts` and `place_of_service` (see Request fields). It has no technique, region or levels.

## Request fields

Two scored criteria are judged from the request (rule 7): `leads-max-2-or-16-contacts` and `permanent-placed-in-asc-or-hospital`.

- **Setting: `place_of_service` carries it.** It is an optional two-digit CMS code that already existed for the PAS draft. For a stimulator request it is required and must be `11` office, `19` off-campus outpatient hospital, `21` inpatient hospital, `22` on-campus outpatient hospital or `24` ambulatory surgical center. It is rendered in plain words (`Place of service: ambulatory surgical center (code 24)`) for stimulator requests only; facet, PAP and bariatric requests stay unrendered, so their prompts are unchanged. There is no second field for the setting, so a request cannot hold two values that disagree.
- **`leads` and `contacts`** (total across leads) are required for a stimulator request and rejected on any other. A request is a stimulator request if it has a code in `SCS_CODES` (`63650`, `63655`, `63685`); `EvalCase` also requires the fields by policy id.
- **Notes and request agree.** The leads, contacts and planned setting stated in `note-permanent-plan` and `note-trial-request` come from the same facts as the request, and a test asserts they are equal in every case.
- **Goldens.** The request chunks of the facet, PAP and bariatric cases and the prompt hash of every scored criterion of the 23 earlier cases were recorded before these fields were added; tests keep them byte-identical.

## Template patient

The shared background patient (`evals/template/patient.json`) has 168 resources: a 65-year-old woman on Medicare. An SCS bundle starts from the template with the changes below. The template file, and the facet, PAP and bariatric bundles, are not changed.

**Search.** The coded fields (the full resource JSON) and the decoded text of every note and report were searched, and the template was chunked to see what the model receives. Terms: pain, chronic, intractab, back, neck, leg, neuropath, radicul, CRPS, complex regional, reflex sympathetic, failed back, laminect, fusion, spine/spinal, surg, operat, physical therapy, physiotherap, conservative, opioid, oxycod, hydrocod, morphin, tramadol, fentanyl, gabapentin, pregabalin, duloxetin, amitriptyl, NSAID, ibuprofen, naproxen, acetaminophen, analgesic, psycholog, psychiatr, mental, depress, anxiety, PHQ, GAD-7, mood, behavio, counsel, substance, alcohol, drug, AUDIT, DAST, cannabis, marijuana, tobacco, smok, stimulat, electrode, lead, implant, device, pacemaker, defibrillat, stent, MRI, magnetic, CT, x-ray, radiograph, imaging, anticoag, warfarin, apixaban, rivaroxaban, heparin, clopidogrel, aspirin, antiplatelet, bleed, coagul, INR, platelet, infect, sepsis, fever, abscess, sinusitis, immunosupp, steroid, education, risks and benefits, consent, multidisciplin, team, trial, stress, abuse, isolation, anesthe.

**No hits:** pain, intractable, neck, leg, every neuropathic or radicular term, CRPS, spine, surgery, physical therapy, conservative, every opioid and neuropathic drug, NSAID, psychological, psychiatric, mental, mood, counseling, cannabis, stimulator, electrode, lead, implant, device, pacemaker, stent, MRI, CT, any anticoagulant other than clopidogrel, aspirin, bleeding, infection, fever, immunosuppression, consent, multidisciplinary, trial. Two terms matched harmlessly: "back" matched the word "background" in the removed notes, and "chronic" matched chronic sinusitis and a resolved chronic periodontitis. There are no Device resources.

| Template fact | Bears on | Handling |
| --- | --- | --- |
| 11 background notes with their 11 twin reports (2023-10-12 to 2026-08-09): "Chief Complaint: No complaints." and "Medications: acetaminophen 325 mg oral tablet" (the first says "No Active Medications" and prescribes it) | `other-modalities-tried-or-unsuitable`, `late-resort-chronic-intractable-pain`, `diagnosis-and-prior-therapies-in-record`, the analgesic branch of `trial-success-50pct-and-function`: they contradict any pain history that overlaps 2023–2026, and acetaminophen is an analgesic | **Removed** (22 resources, list below) |
| Ischemic heart disease, abnormal cardiac imaging finding, 3 cardiac encounters, angiography, a coronary intervention (2026-08-09), cardiology referrals, post-anesthesia care, operative status, clopidogrel, metoprolol, simvastatin, nitroglycerin | A trial lead is a neuraxial procedure, and an antiplatelet drug after a coronary intervention would defer it. No criterion covers it, but "unsuitable or contraindicated" in `other-modalities-tried-or-unsuitable` invites the model to reason about it | **Removed** (16 resources, list below) |
| DAST-10 total score 2 (2025-07-15), with its report and the "Screening for drug abuse" procedure | `no-active-substance-abuse`: a score of 2 sits on the undefined "active substance abuse issues" boundary, and "abuse" matches the criterion's retrieval query | **Value rewritten to 0** |
| AUDIT-C 0 (2023-10-24, 2024-07-09); "Assessment of substance use" ×3; AUDIT-C procedures ×2 | `no-active-substance-abuse` (assessments with no finding) | Kept. Not cited: they predate the pain history's evaluation and "active" is about now |
| PHQ-2 0, 1, 2; GAD-7 4, 2; "Depression screening" ×6; "Assessment of anxiety" ×2; Stress (finding) resolved 2026-07-21; PRAPARE stress level from "Quite a bit" (2023) to "Not at all" (2026) | `psychological-screening`, `multidisciplinary-screening`: similar but irrelevant | Kept as decoys. Every case has a dedicated pre-implant psychological evaluation, so none relies on them and none is cited |
| Victim of intimate partner abuse (resolved 2023-07-04); "Screening for domestic abuse" ×2; HARK 0 | "abuse" decoy for `no-active-substance-abuse` | Kept: clearly irrelevant |
| "Received higher education (finding)"; PRAPARE "Highest level of education" | "education" decoy for `patient-education-and-disclosure` | Kept: clearly irrelevant |
| Social isolation, limited social contact (resolved 2018, 2020); "Risk activity involvement (finding)", active since 1976 | psychological context | Kept: resolved, or vague and matching no substance term |
| Ex-smoker (tobacco status ×4) | substance use, broadly | Kept: not substance abuse |
| Chronic sinusitis (active since 1962); generalised chronic periodontitis, bronchitis (2023), viral sinusitis (2019) and strep throat (2025), all resolved | infection as a clinical contraindication | Kept: no criterion, no active systemic infection |
| Care plan of 2023-10-12 for bronchitis: "Recommendation to avoid exercise", "Deep breathing and coughing exercises" | `other-modalities-tried-or-unsuitable` (physical therapy) | Kept as a similar-but-irrelevant decoy |
| BMI 27.97, obesity Condition, blood pressures, lipid panel | none | Kept unchanged |

### Removed from every SCS bundle

38 resources, listed in `SCS_REMOVED` in `evals/build_cases.py`; a test checks that this list and the constant agree and that none is left in an SCS bundle. That leaves 130 background resources. No kept resource references a removed one (checked against the template).

The 11 background notes and their twin reports (22; each twin's text is identical to its note):

- 2023-10-12: `DocumentReference/c51717c9-a06a-4067-edf7-7bedf17ccfa3`, `DiagnosticReport/97583fea-5ec2-bc35-1be2-623c4647d832`
- 2023-10-24: `DocumentReference/7fc433c4-3564-21a6-91ec-bd0bd3789198`, `DiagnosticReport/29af9190-d89b-c5a0-52a3-541edede0ab1`
- 2023-11-07: `DocumentReference/f4723689-faca-1ed4-6e53-7d891beca769`, `DiagnosticReport/9b135c75-6e31-1c97-7b77-80c680c058c0`
- 2024-07-09: `DocumentReference/c42a95c7-ee92-41a6-d653-1f0f8ca6b198`, `DiagnosticReport/0a26b754-d88f-a285-331a-79e2ded1e796`
- 2025-07-15: `DocumentReference/ab44a957-6c29-abeb-f2e2-bdc75e3bd474`, `DiagnosticReport/3790268a-1756-917a-c596-68cf287f853c`
- 2025-11-22: `DocumentReference/f6ba0ccb-1752-effe-1bd8-851cd71b38a1`, `DiagnosticReport/2d959dc6-9c73-5be5-37cd-96cfb0b7ffb4`
- 2026-05-02: `DocumentReference/ad8b6791-c5e2-872f-9f9d-901b0d809b9f`, `DiagnosticReport/b5a9e535-f29f-fe02-c6d2-d8f8809e4047`
- 2026-07-21: `DocumentReference/98a61531-2b8f-f251-ab35-515a0bf416a2`, `DiagnosticReport/703eb6d6-25bc-c714-f55a-b35aafdf18fb`
- 2026-07-28: `DocumentReference/8a804714-d88f-1efa-b374-09a2ded1640b`, `DiagnosticReport/842f4763-17a4-7525-d5ef-0fabb9c9939d`
- 2026-08-02: `DocumentReference/8ce34cbb-7dcb-26e3-5bed-84b486df6b87`, `DiagnosticReport/ea559e77-5506-0eef-e976-c0ffe97ff036`
- 2026-08-09: `DocumentReference/771daebe-064d-3403-07ae-f308433dd783`, `DiagnosticReport/cf4a10ce-e5a8-9a4e-5891-aee3559aac77`

The cardiac coded resources (16):

- Conditions: `Condition/37b75dda-e187-ae40-4f8f-f53efc54cfc9` (ischemic heart disease), `Condition/37b75dda-e187-ae40-149d-10660d15eefd` (abnormal cardiac imaging finding)
- Encounters: `Encounter/37b75dda-e187-ae40-919a-852251f5aff3` (2026-07-28), `Encounter/37b75dda-e187-ae40-065e-55ee51cb2573` (2026-08-02), `Encounter/37b75dda-e187-ae40-4c99-3aab45f0cd0d` (2026-08-09)
- Procedures: `Procedure/37b75dda-e187-ae40-c852-d61e8e01c819`, `Procedure/37b75dda-e187-ae40-63b7-95497c5dd0b4` (cardiology referrals), `Procedure/37b75dda-e187-ae40-f6e7-8385846bf707` (coronary angiography), `Procedure/37b75dda-e187-ae40-1674-33cf97dc9b5c` (consultation), `Procedure/37b75dda-e187-ae40-5b70-175be4e23f55` (post-anesthesia care), `Procedure/37b75dda-e187-ae40-c5ed-5ffe4c89afec` (coronary intervention)
- Observation: `Observation/37b75dda-e187-ae40-3757-ee628716a210` (operative status)
- Medications: `MedicationRequest/37b75dda-e187-ae40-7172-b7842d7a2b47` (clopidogrel), `MedicationRequest/37b75dda-e187-ae40-c0f4-15273b5391f3` (simvastatin), `MedicationRequest/37b75dda-e187-ae40-acd3-2a7639746de8` (metoprolol), `MedicationRequest/37b75dda-e187-ae40-d1b7-2b381bed9ed0` (nitroglycerin)

The lipid panel, blood pressures, vitals and immunizations of 2026-07-21 stay: they bear on no SCS criterion.

### Rewritten in every SCS bundle

One value: `Observation/37b75dda-e187-ae40-7379-2f446a673ba9`, the DAST-10 total score of 2025-07-15, from 2 to 0. Id, code, date and encounter stay, so it agrees with the two AUDIT-C scores of 0.

**Dependents checked.** The template holds no item-level DAST-10 answers: the Observation has no `component`, and its report (`DiagnosticReport/37b75dda-e187-ae40-1258-a981d5b37caf`) only references the total by display name, with no value and no conclusion. The procedure "Screening for drug abuse" (`Procedure/37b75dda-e187-ae40-91ce-e54a632ea650`) carries no result. The only other texts that mention DAST-10 are the 2025-07-15 note and its twin, which list the report by name without a value; both are removed. A test asserts that in an SCS bundle exactly these three resources mention DAST, 82666-9, 82667-7 or drug abuse, that the Observation has value 0 and no component, and that no other resource states a DAST-10 value.

### Checks on the stripped background

- **Dangling references.** A test asserts an SCS bundle has no reference to a removed resource and no dangling reference beyond those the template already has.
- **Terms.** The 130 kept resources were searched, coded fields and decoded note text, for every term listed under "No hits" and for ischemic, coronary, cardiolog, clopidogrel, metoprolol, nitroglycerin, simvastatin, angiograph, acetaminophen, opioid, analgesic and anticoagulants: no match. A test repeats the search on the background of each SCS bundle before the case history is added, and searches the whole bundle for the cardiac terms.

## Cases

Request in every case: `date` 2026-10-05, `service_date` 2026-10-19. Trial requests: code `63650`, 1 lead, 8 contacts. Permanent requests: codes `63650` and `63685` (percutaneous leads and the pulse generator).

| Case | Variant | Change from the clean case | Labels that change | Overall | Type |
| --- | --- | --- | --- | --- | --- |
| `scs-01` | permanent | none: 2 leads, 16 contacts, ambulatory surgical center (`24`); trial pain 8/10 to 2/10 (75%), function improved | none | met | clean, meets |
| `scs-02` | trial | the psychological evaluation of 2026-09-08 documents an active cocaine use disorder (urine drug screen positive that day, last use 2026-09-04); the team advises addiction medicine first; the request goes ahead; on-campus outpatient hospital (`22`) | `no-active-substance-abuse`: not_met | not_met | clean, fails |
| `scs-03` | permanent | 3 leads, 24 contacts; the trial gave exactly 8/10 to 4/10 (50%); on-campus outpatient hospital (`22`) | `leads-max-2-or-16-contacts`: not_met | not_met | adversarial, near miss on a stated threshold |
| `scs-04` | trial | no education, discussion or disclosure documented in any note; ambulatory surgical center (`24`) | `patient-education-and-disclosure`: insufficient | insufficient | adversarial, missing evidence |
| `scs-05` | permanent | the pain clinic evaluation of 2026-05-18 says "No previous spinal cord stimulator trial or implant."; the pre-anesthesia evaluation of 2026-09-28 says a stimulator generator was implanted in 2023; nothing reconciles them | `one-permanent-stimulator-per-lifetime`: insufficient | insufficient | adversarial, contradiction |
| `scs-06` | permanent | identical to `scs-01` except the setting: place of service `11` (office), and the surgical visit gives the planned setting as the office; no note mentions an ASC or a hospital | `permanent-placed-in-asc-or-hospital`: not_met | not_met | clean, fails |

Mix: 1 met, 3 not_met, 2 insufficient; 3 clean, 3 adversarial. In both trial cases `repeat-trial-extenuating-circumstances` is `not_applicable`, as in the clean trial baseline.

## Labels

Policy quotes the labels follow from (verbatim from the policy file):

- **Q1** (`late-resort-chronic-intractable-pain`): "The implantation of the stimulator is used only as a late resort (if not a last resort) for patients with chronic intractable pain;"
- **Q2** (`other-modalities-tried-or-unsuitable`): "With respect to item a, other treatment modalities (pharmacological, surgical, physical, or psychological therapies) have been tried and did not prove satisfactory, or are judged to be unsuitable or contraindicated for the given patient;"
- **Q3** (`multidisciplinary-screening`): "Patients have undergone careful screening, evaluation and diagnosis by a multidisciplinary team prior to implantation. (Such screening must include psychological, as well as physical evaluation);"
- **Q4** (`trial-pain-relief-before-permanent`): "Demonstration of pain relief with a temporarily implanted electrode precedes permanent implantation."
- **Q5** (`screening-documented-in-chart`): "Documentation of the history and careful screening must be available in the patient chart if requested."
- **Q6** (`no-active-substance-abuse`): "Patients being selected for a trial Must not have active substance abuse issues."
- **Q7** (`patient-education-and-disclosure`): "Patients being selected for a trial [...] Must undergo proper patient education, discussion, and disclosure including an extensive discussion of the risks and benefits of this therapy."
- **Q8** (`psychological-screening`): "Patients being selected for a trial [...] Must undergo appropriate psychological screening"
- **Q9** (`trials-max-2-per-region-lifetime`): "More than 2 SCS trials per anatomic spinal region per patient per lifetime is not considered reasonable and necessary."
- **Q10** (`leads-max-2-or-16-contacts`, request): "Noridian will reimburse for placement of a maximum of 2 leads or 16 “contacts,” and for 2 SCS trials per anatomic spinal region per patient per lifetime (with exceptions allowed for technical limitations for the initial trials or for use of different modalities of stimulation, including new technology)."
- **Q11** (`repeat-trial-extenuating-circumstances`, condition `A previous spinal cord stimulator trial failed.`): "If a trial fails, a repeat trial is not appropriate unless there are extenuating circumstances that lead to trial failure."
- **Q12** (`trial-success-50pct-and-function`): "A successful trial should be associated with at least a 50% reduction of target pain, or 50% reduction of analgesic medications, and show some element of functional improvement."
- **Q13** (`positive-trial-documented`): "Only patients who experience a positive response to a trial should proceed to a permanent implantation. All trials which proceed to permanent implant must have adequate documentation in the chart to support that decision."
- **Q14** (`permanent-placed-in-asc-or-hospital`, request): "Permanent neurostimulators must be placed in an ASC or hospital."
- **Q15** (`one-permanent-stimulator-per-lifetime`): "63655 - One permanent spinal cord stimulator per patient per lifetime and must be performed in an ASC, out-patient hospital or hospital."
- **Q16** (`diagnosis-and-prior-therapies-in-record`): "The clinical record should include the elements leading to the diagnosis and the therapies tried before the decision to use spinal cord stimulators (SCS)."

Scored criteria are 11 per variant. `facilities-and-personnel-available` ("All the facilities, equipment, and professional and support personnel required for the proper diagnosis, treatment training, and follow up of the patient [...] must be available") is at `provider_attestation` and goes on the reviewer checklist, unlabeled. No criterion is at `payer` stage.

| Criterion | 01 (perm) | 02 (trial) | 03 (perm) | 04 (trial) | 05 (perm) | 06 (perm) |
| --- | --- | --- | --- | --- | --- | --- |
| `late-resort-chronic-intractable-pain` (Q1) | met | met | met | met | met | met |
| `other-modalities-tried-or-unsuitable` (Q2) | met | met | met | met | met | met |
| `multidisciplinary-screening` (Q3) | met | met | met | met | met | met |
| `screening-documented-in-chart` (Q5) | met | met | met | met | met | met |
| `diagnosis-and-prior-therapies-in-record` (Q16) | met | met | met | met | met | met |
| `leads-max-2-or-16-contacts` (Q10, request) | met: 2 leads, 16 contacts | met: 1, 8 | **not_met**: 3, 24 | met: 1, 8 | met: 2, 16 | met: 2, 16 |
| `no-active-substance-abuse` (Q6) | | **not_met**: active cocaine use disorder | | met: assessed, none | | |
| `patient-education-and-disclosure` (Q7) | | met | | **insufficient**: not addressed | | |
| `psychological-screening` (Q8) | | met | | met | | |
| `trials-max-2-per-region-lifetime` (Q9) | | met: first trial | | met: first trial | | |
| `repeat-trial-extenuating-circumstances` (Q11) | | not_applicable | | not_applicable | | |
| `trial-pain-relief-before-permanent` (Q4) | met | | met | | met | met |
| `trial-success-50pct-and-function` (Q12) | met: 75% | | met: exactly 50% | | met: 75% | met: 75% |
| `positive-trial-documented` (Q13) | met | | met | | met | met |
| `permanent-placed-in-asc-or-hospital` (Q14, request) | met: `24` | | met: `22` | | met: `24` | **not_met**: `11` |
| `one-permanent-stimulator-per-lifetime` (Q15) | met: first | | met: first | | **insufficient** (rule 4) | met: first |

Why the changed labels follow from the text:

- **`scs-02` `no-active-substance-abuse`: not_met.** Q6 bars a trial for a patient with active substance abuse issues. The psychological evaluation records an active cocaine use disorder, a positive urine drug screen on 2026-09-08 and last use on 2026-09-04, and the visit of 2026-09-29 says the patient declined the referral. A documented excluded condition is `not_met` (rule 3). `psychological-screening` stays `met`: the evaluation happened.
- **`scs-03` `leads-max-2-or-16-contacts`: not_met.** Q10 allows a maximum of 2 leads or 16 contacts; the request has 3 leads and 24 contacts, over the maximum on either reading of "or" (rule 1). `trial-success-50pct-and-function` stays `met`: Q12 says "at least a 50% reduction" and 8 to 4 is exactly 50% (rule 1). The analgesic branch is unused (medications unchanged), so the pain boundary alone decides it.
- **`scs-04` `patient-education-and-disclosure`: insufficient.** Q7 is a positive requirement. No note records education, discussion or disclosure, and none says it did not happen, so the Outcomes table gives `insufficient` (the same reasoning as `pap-06` and `bariatric-06`). A note recording that no discussion took place would make it `not_met` (reserve R5).
- **`scs-05` `one-permanent-stimulator-per-lifetime`: insufficient.** The evaluation of 2026-05-18 says there was no previous trial or implant; the pre-anesthesia evaluation of 2026-09-28 says a generator was implanted in 2023. Both are clinic notes of the same standing, neither mentions the other, the surgical visit is silent on the question so the notes are one against one, and nothing reconciles them. The label rests only on that disagreement: under rule 4, which is our reading and not a CMS rule, a fact a criterion depends on that two documents contradict, with nothing resolving it, is `insufficient`. The label does not depend on which note is right, and it takes no position on a replacement (see known limitations). The case is flagged for the blinded second annotation pass.
- **`scs-06` `permanent-placed-in-asc-or-hospital`: not_met.** Q14 requires an ASC or hospital. The request's place of service is `11`, an office, and the surgical visit gives the planned setting as the office. Every other label is as in `scs-01`.

## Evidence lists and the evidence check

The evidence lists in the case files are generated by `evals/build_cases.py` from the same facts as the bundles. Every resource in a list must state the fact its label rests on, which is checked in both directions. The request-stage criteria cite the request fields (rule 7).

**The builder keeps, for each criterion, the resources that carry it and the statements each must contain.** The statements are the strings the note texts are written from, so a note and its statement cannot drift. The tests check:

1. **Each cited resource states the fact.** Every cited note's decoded text contains the statements listed for that criterion, and every cited coded resource carries them in its chunk text. This catches a note cited for a fact it does not state, as the bariatric work found for `note-wm-12mo`.
2. **No uncited note states it.** Some criteria have a marker (for example "late resort", "risks and benefits", "first permanent spinal cord stimulator"). For the criteria in the case's variant, any added note whose text contains a marker must be cited by that criterion's label.
3. **The chunks keep it.** Each statement appears whole inside one chunk of its note, with `char_start` set (notes are split at 1,500 characters), so the model can quote it.
4. **Numbers in a label's `fact` match the notes.** A computed value such as `(8 - 2) / 8 = 75%` appears verbatim in the cited note.

Key statements the notes carry:

| Note (date) | Statements |
| --- | --- |
| `note-pain-clinic-eval` (2026-05-18) | "Low back and left leg pain since 2020-06-08, now present for 71 months."; "Pain remains intractable: 8/10 on the Numerical Rating Scale (NRS) 0-10 despite the treatments above."; "spinal cord stimulation is being considered as a late resort"; the treatments tried with each result; "No previous spinal cord stimulator trial or implant."; "Physical evaluation, in person today:"; the MRI summary |
| `note-psych-eval` (permanent 2026-06-09; trial 2026-09-08) | "Psychological evaluation by a clinical psychologist"; no mood, anxiety or somatic symptom disorder; "Substance use assessment:" with "no current or past substance use disorder" and a urine drug screen consistent with the prescribed oxycodone (`scs-02`: "active cocaine use disorder, moderate", the positive screen and the date of last use) |
| `note-team-conference` (permanent 2026-06-23; trial 2026-09-15) | The attendees (pain physician, spine surgeon, clinical psychologist, physical therapist); the evaluations reviewed; "chronic intractable low back and left leg pain"; "spinal cord stimulation is a late resort for this patient"; the recommendation (`scs-02`: addiction medicine first) |
| `note-education` (permanent 2026-07-07; trial `scs-02` 2026-09-22; absent in `scs-04`) | "Extensive discussion of the risks and benefits of the therapy." with the risks, the limits of benefit and the signed consent for the trial |
| `note-trial-result` (2026-08-11) | "(8 - 2) / 8 = 75% reduction in target pain" (`scs-03`: "(8 - 4) / 8 = 50%"); "Analgesic medications unchanged during the trial."; "walking tolerance rose from 10 minutes to 40 minutes"; "Assessment: positive response to the trial." |
| `note-permanent-plan` (2026-09-01) | "positive response to the trial"; "This would be the patient's first permanent spinal cord stimulator." (absent in `scs-05`); the leads and contacts; "Planned setting:" |
| `note-preanesthesia` (2026-09-28) | The 2021 microdiscectomy; in `scs-05` also "Spinal cord stimulator generator in the right gluteal pocket (implanted 2023)." |
| `note-trial-request` (trial, 2026-09-29) | "This would be the patient's first trial."; the leads and contacts; "Planned setting:"; in `scs-02` the positive screen and "The patient declined the referral to addiction medicine." |

Evidence by criterion (resources that do not state the fact are left out; for example `note-pcp` does not say that naproxen failed):

| Criterion | Cited |
| --- | --- |
| `late-resort-chronic-intractable-pain` | `Condition/chronic-pain`, `note-pain-clinic-eval`, `note-team-conference` |
| `other-modalities-tried-or-unsuitable` | `MedicationRequest/naproxen`, `/gabapentin`, `/duloxetine`, `/oxycodone-apap`, `Procedure/physical-therapy-course`, `/lumbar-microdiscectomy`, `/cbt-course`, `note-pt-discharge`, `note-postop`, `note-pain-clinic-2021`, `note-cbt-discharge`, `note-pain-clinic-2022`, `note-pain-clinic-eval` |
| `multidisciplinary-screening` | `Encounter/enc-team-conference`, `note-team-conference`, `note-psych-eval`, `note-pain-clinic-eval` |
| `screening-documented-in-chart` | `note-pain-clinic-eval`, `note-psych-eval`, `note-team-conference` |
| `diagnosis-and-prior-therapies-in-record` | `Condition/chronic-pain`, `DiagnosticReport/mri-lumbar`, `note-pain-clinic-eval`, `note-pt-discharge`, `note-postop`, `note-pain-clinic-2021`, `note-cbt-discharge`, `note-pain-clinic-2022` |
| `no-active-substance-abuse` | met: `note-psych-eval`; `scs-02`: `Condition/cocaine-use-disorder`, `note-psych-eval`, `note-team-conference`, `note-trial-request` |
| `patient-education-and-disclosure` | `note-education`; `scs-04` (gap): `note-team-conference`, `note-trial-request` |
| `psychological-screening` | `Procedure/psychological-evaluation`, `note-psych-eval` |
| `trials-max-2-per-region-lifetime` | `note-pain-clinic-eval`, `note-trial-request` |
| `repeat-trial-extenuating-circumstances` | not_applicable: nothing listed |
| `trial-pain-relief-before-permanent` | `Procedure/scs-trial`, `Observation/pain-pre-trial`, `Observation/pain-end-trial`, `note-trial-procedure`, `note-trial-result` |
| `trial-success-50pct-and-function` | `Observation/pain-pre-trial`, `Observation/pain-end-trial`, `note-trial-result` |
| `positive-trial-documented` | `note-trial-result`, `note-permanent-plan` |
| `one-permanent-stimulator-per-lifetime` | met: `note-pain-clinic-eval`, `note-permanent-plan`; `scs-05`: `note-pain-clinic-eval`, `note-preanesthesia` |
| `leads-max-2-or-16-contacts` | `request.leads`, `request.contacts` |
| `permanent-placed-in-asc-or-hospital` | `request.place_of_service` |

The background screens (PHQ-2, GAD-7, AUDIT-C, DAST-10) are not cited: whether they are "appropriate psychological screening" is undefined, and "active" substance abuse is about now.

## Timelines

Common to all six bundles: the history the labels rest on (a woman aged 59 at onset). Every date is a Monday or Tuesday; every event precedes the request.

| Date | Event |
| --- | --- |
| 2020-06-08 | Onset of low back and left leg pain (`Condition/chronic-pain`, onset) |
| 2020-09-14 | Primary care: naproxen 500 mg twice daily (`MedicationRequest/naproxen`, status `completed`), referral to physical therapy (`note-pcp`) |
| 2020-10-05 to 2020-12-28 | Physical therapy, 12 weeks (84 days) (`Procedure/physical-therapy-course`); discharge 2020-12-28: no improvement, pain 8/10 (`note-pt-discharge`) |
| 2021-01-19 | Lumbar MRI: left L4-5 disc herniation with left L5 root compression (reported in `note-postop`, not a bundle resource) |
| 2021-04-12 | L4-5 left microdiscectomy (`Procedure/lumbar-microdiscectomy`) |
| 2021-07-13 | Surgical follow-up: relief for 6 weeks, then the pain returned; post-operative MRI of 2021-07-06 shows no recurrent herniation, post-surgical scarring (`note-postop`) |
| 2021-09-21 | Pain clinic: gabapentin and duloxetine increased to full doses (`MedicationRequest/gabapentin`, `/duloxetine`, status `active`; `note-pain-clinic-2021`) |
| 2022-02-07 to 2022-04-04 | Cognitive behavioral therapy for chronic pain, 8 weekly sessions (56 days) (`Procedure/cbt-course`); discharge 2022-04-04: skills learned, pain unchanged at 8/10 (`note-cbt-discharge`) |
| 2022-03-15 | Pain clinic: pain 8/10 on gabapentin and duloxetine at full doses; oxycodone 5 mg with acetaminophen 325 mg, up to 4 tablets a day, started (`MedicationRequest/oxycodone-apap`, `active`; `note-pain-clinic-2022`) |
| 2026-04-20 | Lumbar MRI: post-surgical changes at L4-5, no compressive lesion, no infection (`DiagnosticReport/mri-lumbar`) |
| 2026-05-18 | Pain clinic re-evaluation: pain 8/10 (`Observation/pain-nrs-baseline`), pain for 71 months, no previous stimulator trial or implant, referral for psychological evaluation and team review (`note-pain-clinic-eval`) |

`scs-01`, `scs-03`, `scs-05` and `scs-06` (permanent), continuing:

| Date | Event |
| --- | --- |
| 2026-06-09 | Psychological evaluation (`Procedure/psychological-evaluation`, `note-psych-eval`); no substance use disorder |
| 2026-06-23 | Multidisciplinary pain conference: proceed to a trial (`Encounter/enc-team-conference`, `note-team-conference`) |
| 2026-07-07 | Education and disclosure visit, consent for the trial (`note-education`) |
| 2026-08-04 | Trial: temporary percutaneous epidural leads at T8-T10 (`Procedure/scs-trial`, code `63650`, `note-trial-procedure`); pain 8/10 before (`Observation/pain-pre-trial`, 09:00) |
| 2026-08-11 | End of the 7-day trial, leads removed: 2/10 (`scs-03`: 4/10) (`Observation/pain-end-trial`, `note-trial-result`) |
| 2026-09-01 | Surgical visit, plan for permanent implantation (`note-permanent-plan`); `scs-05` omits the sentence about a first stimulator; `scs-06` gives the planned setting as the office |
| 2026-09-28 | Pre-anesthesia evaluation (`note-preanesthesia`); `scs-05` adds the 2023 generator |
| 2026-10-05 | Request; service 2026-10-19 |

`scs-02` and `scs-04` (trial), continuing:

| Date | Event |
| --- | --- |
| 2026-09-08 | Psychological evaluation (`Procedure/psychological-evaluation`, `note-psych-eval`); `scs-02`: urine drug screen positive for cocaine metabolite, last use 2026-09-04, `Condition/cocaine-use-disorder` (text-only code) |
| 2026-09-15 | Multidisciplinary pain conference (`note-team-conference`); `scs-02`: addiction medicine first |
| 2026-09-22 | `scs-02` only: education and disclosure visit (`note-education`) |
| 2026-09-29 | Pain clinic visit: request for the trial (`note-trial-request`); `scs-02`: the patient declined the referral |
| 2026-10-05 | Request; service 2026-10-19 |

**Consistency checks, asserted by the builder or a test:** every note's first line is its date, which equals its resource date and its encounter's date (a course ends on its note's date); every added event is before 2026-10-05; the trial lasted 7 days, therapy courses 84 and 56 days, and the pain duration at the evaluation is 71 months; the medications active on the request date are exactly gabapentin, duloxetine and oxycodone with acetaminophen, none started or stopped during the trial; the pre-trial score of 2026-08-04 equals the baseline of 8; in `scs-05` the evaluation and the pre-anesthesia note are the only notes about a previous device.

## Design notes

- **Computed facts.** The relief percentages come from a builder helper that asserts the percentage is a whole number: exactly 50% for `scs-03` (`(8 - 4) / 8`), 75% for the others (`(8 - 2) / 8`). The same file asserts the 7-day trial, the 84- and 56-day courses and the 71-month duration.
- **Codes.** No new LOINC codes. Pain scores reuse `72514-3` (the Numerical Rating Scale code the facet builder uses), physical therapy reuses SNOMED `91251008`, and the pain Condition reuses SNOMED `278860009` with its display. The microdiscectomy, cognitive behavioral therapy, psychological evaluation, cocaine use disorder and the medications are text-only (as the facet naproxen is). CPT `63650` is a code only, with plain-words `text` and no descriptor. Any code added later is confirmed first (the LOINC terminology server at tx.fhir.org is acceptable).
- **Retrieval.** Only notes, conditions, procedures and medication requests are embedded; observations are found by code and date. Every fact the model must retrieve is in a note: the trial result and the 75% or 50% figure are in `note-trial-result`, the pain scores are also coded Observations that support them, and the leads, contacts and setting are in the request and repeated in the plan notes.
- **`scs-04` and the notes.** No note in `scs-04` states or implies a discussion with the patient. The team conference may say the team reviewed the evaluations, and the request visit says only what is planned. The notes are not worded to avoid a test pattern; the pattern was written after them.
- **Near miss (`scs-03`).** Two things sit on stated boundaries: the trial result is exactly at 50% (met side) and the request is one lead over the maximum (not_met side). The analgesic branch is held constant so the pain figure alone decides `trial-success-50pct-and-function`.
- **Setting (`scs-06`).** The planned setting in `note-permanent-plan` is the same fact as the request: "Planned setting: office." No note in `scs-06` mentions an ASC, an ambulatory surgery center or a hospital. A permanent implant in an office is not clinically usual; the case exists to test the stated rule.

## Boundaries avoided (rule 2)

| Undefined term | Criterion | How the cases avoid it |
| --- | --- | --- |
| "late resort (if not a last resort)", "chronic intractable" (no duration) | `late-resort-chronic-intractable-pain` | Every case is 71 months of pain after four failed modalities and surgery; no case is near a duration or a "last resort" edge |
| "did not prove satisfactory" | `other-modalities-tried-or-unsuitable` | Pain stays 8/10 through each documented therapy; none gives partial relief |
| "careful screening", "multidisciplinary team" | `multidisciplinary-screening`, `screening-documented-in-chart` | Every case has a conference note naming four disciplines and the two evaluations |
| "active substance abuse issues" | `no-active-substance-abuse` | `scs-02` is a diagnosed, current disorder with a positive screen; other trial cases document an assessment with no finding; the DAST-10 of 2 is rewritten to 0; no case has occasional or past use |
| "proper education [...] extensive discussion" | `patient-education-and-disclosure` | `scs-02` has a full visit; `scs-04` has none; no case has a partial discussion |
| "appropriate psychological screening" | `psychological-screening` | Every case has a dedicated psychologist evaluation; no case is missing one or rests on a PHQ-2 alone (reserve R1) |
| "anatomic spinal region", the "technical limitations / new technology" exceptions | `trials-max-2-per-region-lifetime`, `leads-max-2-or-16-contacts` | No case has a prior trial, so the count is 0 and the exceptions never apply |
| "2 leads **or** 16 contacts" | `leads-max-2-or-16-contacts` | The pass cases are at or below both (1 lead and 8 contacts; 2 leads and 16 contacts), the fail case is above both (3 leads and 24 contacts); no case is over on one and not the other |
| "extenuating circumstances" | `repeat-trial-extenuating-circumstances` | No case has a previous trial, so the criterion is `not_applicable` (reserve R2) |
| "some element of functional improvement", "positive response", "adequate documentation" | `trial-success-50pct-and-function`, `positive-trial-documented` | Walking tolerance rises from 10 to 40 minutes in every permanent case, and the note says the response was positive; no case has a trial under 50% (reserve R4) |
| "ASC or hospital" | `permanent-placed-in-asc-or-hospital` | Codes `24`, `22` and `11` only. The article names "out-patient hospital" and the LCD "hospital", so an on-campus outpatient hospital is a hospital on the text; an office is neither |

Exactly stated boundaries used: "at least 50%" (`scs-03`, met at exactly 50%), "maximum of 2 leads or 16 contacts" (`scs-01`, `scs-05` and `scs-06` at the maximum, met; `scs-03` over it), "One permanent [...] per lifetime" (`scs-05`).

## Known limitations

- **`psychological-screening` is never `insufficient` or `not_met`** in these cases, so how the pipeline behaves when a psychological evaluation is missing is untested. The blocker is that "appropriate" is undefined and the background holds PHQ-2 and GAD-7 screens a reader could count (reserve R1).
- **Jurisdiction is not checked.** LCD L36204 and Article A57792 apply only in Noridian Jurisdiction F; other MACs have different LCDs (for example L35136 and L37632). The policy has no payer-stage criterion, so nothing flags this. `facilities-and-personnel-available` stays on the reviewer checklist, unlabeled.
- **Q15 is quoted with a "63655 -" prefix** while the permanent cases request `63650` and `63685`. The policy file applies `one-permanent-stimulator-per-lifetime` to the whole permanent variant, and the labels follow it. The LCD also allows replacing a working stimulator, and the article does not reconcile the two statements (policy note); no case is a replacement, and the `scs-05` label takes no position on one (reserve R3).
- **The repeat-trial path is untested.** `repeat-trial-extenuating-circumstances` is `not_applicable` in both trial cases; the case where its condition holds rests on the undefined "extenuating circumstances" (reserve R2).
- **Several criteria never fail:** `trials-max-2-per-region-lifetime` (the boundary at 2 is not exercised, reserve R6), `trial-pain-relief-before-permanent`, `trial-success-50pct-and-function` below 50% (R4), `positive-trial-documented`, `late-resort-chronic-intractable-pain`, `other-modalities-tried-or-unsuitable`, `multidisciplinary-screening`, `screening-documented-in-chart` and `diagnosis-and-prior-therapies-in-record`. `one-permanent-stimulator-per-lifetime` is never `not_met`. `patient-education-and-disclosure` is `not_met` nowhere (R5) and `no-active-substance-abuse` is `insufficient` nowhere (R6).
- Q12 says a successful trial "should be associated with" 50%; the label treats the threshold as the criterion, as the policy does.
- The SCS bundles differ from the shared background in 38 removed resources and 1 rewritten value, so they share a patient with neither the facet, PAP nor bariatric cases.
- Retrieval is top-8 hybrid search per criterion. `other-modalities-tried-or-unsuitable` and `diagnosis-and-prior-therapies-in-record` need several notes, so a miss there is a retrieval result, not a label problem.
- `scs-05` rests on our reading of rule 4 and awaits the second annotator; `scs-04` rests on the Outcomes table (a positive requirement with no evidence is `insufficient`).
- Single annotator; not clinically validated.

## Reserve cases

Not built; each closes a gap above.

- **R1** a trial request with no psychological evaluation: `psychological-screening` insufficient. Blocker: "appropriate" is undefined, and the PHQ-2 and GAD-7 in the background would have to be removed or ruled out.
- **R2** a repeat trial after a failed trial: `repeat-trial-extenuating-circumstances` applicable. Blocker: "extenuating circumstances" is undefined, so both a documented cause and a missing cause rest on that term.
- **R3** a second permanent system while one stays in place, or a replacement: `one-permanent-stimulator-per-lifetime` decided. Blocker: the LCD allows replacing a working stimulator and the article does not reconcile the two statements, so a replacement-versus-additional decision is needed first.
- **R4** a trial just under 50% (pain 9 to 5, 44%, medications unchanged): `trial-success-50pct-and-function` not_met. Blocker: `positive-trial-documented` ("positive response") has no stated threshold, so its label would sit on an undefined boundary.
- **R5** a note recording that no discussion of risks and benefits took place: `patient-education-and-disclosure` not_met.
- **R6** occasional or past substance use raised and not resolved: `no-active-substance-abuse` insufficient. Blocker: "active" is undefined.
- **R7** a third trial in one region: `trials-max-2-per-region-lifetime` not_met. Blocker: two prior trials bring in the repeat-trial criterion and the exceptions clause; a clean design needs two prior successful trials that did not lead to an implant, which is clinically odd.
- **R8** 2 leads with 32 contacts (paddle leads): blocker is the "or" reading of Q10, which would make the label depend on how the sentence is read.

## Checks before a case is accepted

- Every changed label cites the quoted sentence it follows from.
- Every scored criterion in the variant has a label.
- The overall outcome computed from the labels matches the table.
- The bundle contains the evidence the labels cite, and each cited resource states the fact (see the evidence check).
- The leads, contacts and setting stated in the plan and request notes equal the request fields.
