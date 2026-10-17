# Bariatric surgery: case plan

Six cases against `data/policies/bariatric-surgery.yaml` (NCD 100.1 v5), labeled under `evals/GUIDELINES.md`. Each case changes one or two facts from a clean baseline; every other scored criterion is `met`. The request block carries the dates, the procedure in plain words and the code that routes the variant (`43644`, `43770` and `43846` for `bariatric`, `43775` for `sleeve-gastrectomy`); it has no technique, region or levels. The one request-stage criterion, `nationally-covered-procedure`, is judged from `request.procedure` and `request.codes`.

## Template patient

The shared background patient (`evals/template/patient.json`), aged 65 and on Medicare. The NCD has no age criterion. It cannot serve these cases as it is, so a bariatric bundle starts from the template with the changes below. The template file, and the facet and PAP bundles, are not changed.

| Template fact | Bears on | Handling |
| --- | --- | --- |
| Weight 83.6 kg and BMI 27.97 at the annual exams of 2023-10-24, 2024-07-09, 2025-07-15 and 2026-07-21; height 172.9 cm | `bmi-35-plus`, `lsg-bmi-35-plus`: would contradict every BMI of 35 or more | Rewritten. The build script sets the 4 weight and 4 BMI Observations from the case facts. Ids, codes, dates and height stay. BMI is weight / 1.729^2, stored to 2 decimals, and asserted |
| Condition "Body mass index 30+ - obesity" (SNOMED 162864005), active since 1995 | none: it does not state 35 or more, and obesity alone is not a co-morbidity | Kept. Every BMI in every case is 30 or more (lowest 30.37), and the script fails otherwise |
| Ischemic heart disease, an abnormal cardiac imaging finding, angiography, a coronary intervention, 4 cardiac medications | `obesity-comorbidity`: the NCD names only type 2 diabetes, and does not say whether ischemic heart disease counts, an undefined boundary | Removed from every bariatric bundle (list below) |
| The note pair of 2026-07-21 that mentions the ischemic heart disease and the four prescriptions | as above | Removed with the cluster |
| Lipid panel 2026-07-21 (total cholesterol 243.42 and LDL 163.86 mg/dL); blood pressures 135/90, 136/84, 139/88, 137/86 | could be read as dyslipidemia or hypertension, co-morbidities the NCD does not name | Panel removed. The 4 blood pressures rewritten to 118/76, 116/74, 114/76, 118/76 |
| Care plan of 2023-10-12 for acute bronchitis: "Recommendation to avoid exercise", "Deep breathing and coughing exercises" | similar but irrelevant to `failed-medical-treatment` | Kept, as the decoy in `bariatric-04` |
| No bariatric or gastric surgery, no weight-loss medication, no diet or weight program | `failed-medical-treatment` | None needed: the case facts decide it |
| Background notes list a history without diabetes | could read as contradicting a diabetes diagnosis | Type 2 diabetes is diagnosed on 2026-07-28. Every background note dated before it predates it, and the notes dated after it are cardiac and are removed |

### Removed from every bariatric bundle

29 resources, listed in `BARIATRIC_REMOVED` in `evals/build_cases.py`; a test checks that this list and the constant agree and that none is left in a bariatric bundle. That leaves 139 background resources.

Cardiac cluster (22):

- `Condition/37b75dda-e187-ae40-4f8f-f53efc54cfc9`: ischemic heart disease, 2026-07-21
- `Condition/37b75dda-e187-ae40-149d-10660d15eefd`: abnormal findings on diagnostic imaging of heart and coronary circulation, 2026-08-02
- 2026-07-28 encounter `Encounter/37b75dda-e187-ae40-919a-852251f5aff3`, with `Procedure/37b75dda-e187-ae40-c852-d61e8e01c819` (referral to cardiology), `Observation/37b75dda-e187-ae40-3757-ee628716a210` (operative status), the note `DocumentReference/8a804714-d88f-1efa-b374-09a2ded1640b` and its twin `DiagnosticReport/842f4763-17a4-7525-d5ef-0fabb9c9939d`
- 2026-08-02 encounter `Encounter/37b75dda-e187-ae40-065e-55ee51cb2573`, with `Procedure/37b75dda-e187-ae40-f6e7-8385846bf707` (coronary angiography), `Procedure/37b75dda-e187-ae40-1674-33cf97dc9b5c` (consultation for treatment), `Procedure/37b75dda-e187-ae40-5b70-175be4e23f55` (post-anesthesia care), `Procedure/37b75dda-e187-ae40-63b7-95497c5dd0b4` (referral to cardiology), the note `DocumentReference/8ce34cbb-7dcb-26e3-5bed-84b486df6b87` and its twin `DiagnosticReport/ea559e77-5506-0eef-e976-c0ffe97ff036`
- 2026-08-09 encounter `Encounter/37b75dda-e187-ae40-4c99-3aab45f0cd0d`, with `Procedure/37b75dda-e187-ae40-c5ed-5ffe4c89afec` (percutaneous coronary intervention), the note `DocumentReference/771daebe-064d-3403-07ae-f308433dd783` and its twin `DiagnosticReport/cf4a10ce-e5a8-9a4e-5891-aee3559aac77`
- The 4 cardiac prescriptions of 2026-07-28: `MedicationRequest/37b75dda-e187-ae40-7172-b7842d7a2b47` (clopidogrel), `MedicationRequest/37b75dda-e187-ae40-c0f4-15273b5391f3` (simvastatin), `MedicationRequest/37b75dda-e187-ae40-acd3-2a7639746de8` (metoprolol), `MedicationRequest/37b75dda-e187-ae40-d1b7-2b381bed9ed0` (nitroglycerin)

The annual-exam note pair that describes the cluster (2):

- `DocumentReference/98a61531-2b8f-f251-ab35-515a0bf416a2` and its twin `DiagnosticReport/703eb6d6-25bc-c714-f55a-b35aafdf18fb`

The lipid panel (5):

- `DiagnosticReport/37b75dda-e187-ae40-f188-4c73123a16a4` with its results `Observation/37b75dda-e187-ae40-1295-7a60d2d1e589` (total cholesterol), `Observation/37b75dda-e187-ae40-8772-5ec7ebe1237e` (triglycerides), `Observation/37b75dda-e187-ae40-6a38-cd02fbcb8a50` (LDL) and `Observation/37b75dda-e187-ae40-42cc-ce6e2cf7a872` (HDL)

### Rewritten in every bariatric bundle

12 Observations, same ids, codes and dates; only the values change. Weights and BMIs come from the case (series below); a test checks each BMI against its weight.

- Weight: `Observation/37b75dda-e187-ae40-6dd1-642a3ad7f638`, `Observation/37b75dda-e187-ae40-e2d6-107608c22f3e`, `Observation/37b75dda-e187-ae40-43e5-394059e22092`, `Observation/37b75dda-e187-ae40-03d2-a769b8248cd8`
- BMI: `Observation/37b75dda-e187-ae40-6e7f-02bcda7c4638`, `Observation/37b75dda-e187-ae40-ef05-fe4cb44a431a`, `Observation/37b75dda-e187-ae40-ed66-ed3e7b93f2f4`, `Observation/37b75dda-e187-ae40-d89f-acacabb1a078`
- Blood pressure: `Observation/37b75dda-e187-ae40-e8a9-54254ae1d38e`, `Observation/37b75dda-e187-ae40-c0e6-affb1ac63403`, `Observation/37b75dda-e187-ae40-c7b9-de2e6272a781`, `Observation/37b75dda-e187-ae40-ef88-4756014e7d74`

### Checks on the stripped background

- **Dangling references.** Apart from the removed note pair, no kept resource references or mentions a removed one. The kept resources were searched for the removed ids and for ischemic, coronary, cardiolog, clopidogrel, metoprolol, nitroglycerin, simvastatin, angiograph and percutaneous. A test asserts that a bariatric bundle has no reference to a removed resource and no dangling reference beyond those the template already has (the trim left references to dropped providers, which are harmless).
- **Other candidate co-morbidities.** Coded fields and decoded note text were searched for: diabetes, glucose, A1c, insulin, metformin, prediabetes, hypertension, apnea, sleep, snoring, lipid, cholesterol, triglyceride, dyslipidemia, hyperlipidemia, GERD, reflux, esophagitis, heartburn, fatty liver, steatosis, NAFLD, NASH, hepatitis, gallbladder, gallstones, arthritis, osteoarthritis, knee, hip, gout, asthma, PCOS, infertility, venous stasis, incontinence, heart failure, kidney, renal, pseudotumor, hypoventilation. No hits in any note, and the only coded hits were the blood pressures and the lipid panel, handled above. The 29 remaining Conditions are chronic sinusitis, BMI 30+ obesity, and resolved dental, social, infection, stress and pregnancy-history findings. A test repeats the search.
- **Treatment terms.** Searched for diet, exercise, nutrition, counseling, weight loss, orlistat, phentermine, semaglutide, GLP-1, bariatric (and lifestyle, dietitian, weight management). 4 hits, all in the care plan of 2023-10-12 for acute bronchitis ("Recommendation to avoid exercise" and "Deep breathing and coughing exercises"); none in a note, medication, procedure or condition. They affect no label: they are not treatment for obesity. They are the decoy in `bariatric-04`.

## Cases

Request in every case: `date` 2026-10-05, `service_date` 2026-10-19.

| Case | Variant | Change from template | Labels that change | Overall | Type |
| --- | --- | --- | --- | --- | --- |
| `bariatric-01` | bariatric | none: laparoscopic Roux-en-Y gastric bypass (`43644`), BMI 41.1, type 2 diabetes, a 12-month supervised program with orlistat that failed | none | met | clean, meets |
| `bariatric-02` | bariatric | weight 95.0 kg, BMI 31.8 (every BMI 30.4 to 31.8); laparoscopic adjustable gastric banding (`43770`) | `bmi-35-plus`: not_met | not_met | clean, fails |
| `bariatric-03` | sleeve-gastrectomy | weight 104.3 kg, BMI 34.89 (34.9 in the note), no BMI at 34.95 or above; stand-alone laparoscopic sleeve gastrectomy (`43775`) | `lsg-bmi-35-plus`: not_met | not_met | adversarial, near miss on a stated threshold |
| `bariatric-04` | sleeve-gastrectomy | no record of any medical treatment for obesity; BMI 40.7; `43775` | `lsg-failed-medical-treatment`: insufficient | insufficient | adversarial, missing evidence |
| `bariatric-05` | bariatric | a clinic note of 2025-08-04 says the patient declined enrollment and the referral was closed; the surgical evaluation of 2026-09-14 says the 12-month program was completed; nothing reconciles them; open Roux-en-Y gastric bypass (`43846`), BMI 42.2 | `failed-medical-treatment`: insufficient | insufficient | adversarial, contradiction |
| `bariatric-06` | bariatric | no diabetes and no other co-morbidity coded or written, and the evaluation lists no history; BMI 39.3; program failed; `43644` | `obesity-comorbidity`: insufficient | insufficient | adversarial, missing evidence |

Mix: 1 met, 2 not_met, 3 insufficient; 2 clean, 4 adversarial. Variants covered: bariatric (4), sleeve-gastrectomy (2). All four routing codes appear.

## Labels

Scored criteria: 4 for `bariatric` (`nationally-covered-procedure` at request stage, the other three at chart stage) and 3 for `sleeve-gastrectomy`. `lsg-covered-by-local-mac` is at payer stage, is not labeled and goes on the reviewer checklist. No criterion in either variant has a `condition`, so no case has a `not_applicable` label.

Policy quotes the labels follow from:

- **Q1** (`nationally-covered-procedure`, `bmi-35-plus`, `failed-medical-treatment`): "Effective for services performed on and after February 21, 2006, Open and laparoscopic Roux-en-Y gastric bypass (RYGBP), open and laparoscopic Biliopancreatic Diversion with Duodenal Switch (BPD/DS) or Gastric Reduction Duodenal Switch (BPD/GRDS), and laparoscopic adjustable gastric banding (LAGB) are covered for Medicare beneficiaries who have a body-mass index ≥ 35, have at least one co-morbidity related to obesity, and have been previously unsuccessful with medical treatment for obesity."
- **Q2** (`obesity-comorbidity`): "Medicare beneficiaries who have a body-mass index ≥ 35, have at least one co-morbidity related to obesity, and have been previously unsuccessful with medical treatment for obesity. [...] Effective for services performed on and after February 12, 2009, the Centers for Medicare & Medicaid Services (CMS) determines that Type 2 diabetes mellitus is a co-morbidity for purposes of this NCD."
- **Q3** (`lsg-bmi-35-plus`): "The beneficiary has a body-mass index (BMI) ≥ 35 kg/m2,"
- **Q4** (`lsg-obesity-comorbidity`): "The beneficiary has at least one co-morbidity related to obesity, and,"
- **Q5** (`lsg-failed-medical-treatment`): "The beneficiary has been previously unsuccessful with medical treatment for obesity."

| Criterion | 01 | 02 | 03 | 04 | 05 | 06 |
| --- | --- | --- | --- | --- | --- | --- |
| `nationally-covered-procedure` (Q1: "Open and laparoscopic Roux-en-Y gastric bypass (RYGBP) [...] and laparoscopic adjustable gastric banding (LAGB) are covered") | met: laparoscopic RYGB | met: laparoscopic adjustable banding | | | met: open RYGB | met: laparoscopic RYGB |
| `bmi-35-plus` (Q1: "have a body-mass index ≥ 35") | met: 41.1 | **not_met**: 31.8 | | | met: 42.2 | met: 39.3 |
| `obesity-comorbidity` (Q2: "have at least one co-morbidity related to obesity"; "Type 2 diabetes mellitus is a co-morbidity for purposes of this NCD") | met: type 2 diabetes | met | | | met | **insufficient**: none documented, none assessed |
| `failed-medical-treatment` (Q1: "have been previously unsuccessful with medical treatment for obesity") | met: 12 months, +0.6 kg | met | | | **insufficient** (rule 4): clinic note against evaluation | met |
| `lsg-bmi-35-plus` (Q3) | | | **not_met**: 34.9 | met: 40.7 | | |
| `lsg-obesity-comorbidity` (Q4, and Q2 for type 2 diabetes) | | | met | met | | |
| `lsg-failed-medical-treatment` (Q5) | | | met | **insufficient**: not addressed | | |

The evidence lists in the case files are generated by `evals/build_cases.py` from the same facts as the bundles.

## Weight series

Height 172.9 cm; BMI = kg / 1.729^2, coded to 2 decimals; a note gives one decimal, rounded from the unrounded value. Rows marked * are the rewritten Observations of the annual exams. Each series rises slowly from 2023, dips 3.2 kg by month 6 of a program, regains it by month 12 (0.6 kg above the intake weight) and ends at the weight of the surgical evaluation.

| Date | 01 | 02 | 03 | 04 | 05 | 06 |
| --- | --- | --- | --- | --- | --- | --- |
| 2023-10-24* | 120.3 / 40.24 | 92.4 / 30.91 | 101.7 / 34.02 | 119.0 / 39.81 | 123.7 / 41.38 | 114.9 / 38.44 |
| 2024-07-09* | 121.1 / 40.51 | 93.2 / 31.18 | 102.5 / 34.29 | 119.8 / 40.07 | 124.5 / 41.65 | 115.7 / 38.70 |
| 2025-07-15* | 122.0 / 40.81 | 94.1 / 31.48 | 103.4 / 34.59 | 120.7 / 40.38 | 125.4 / 41.95 | 116.6 / 39.00 |
| 2025-07-28 | 122.0 / 40.81 | 94.1 / 31.48 | 103.4 / 34.59 | | 125.4 / 41.95 | 116.6 / 39.00 |
| 2025-08-04 | 121.9 / 40.78 | 94.0 / 31.44 | 103.3 / 34.55 | | | 116.5 / 38.97 |
| 2026-02-02 | 118.7 / 39.71 | 90.8 / 30.37 | 100.1 / 33.48 | | | 113.3 / 37.90 |
| 2026-07-21* | 122.3 / 40.91 | 94.4 / 31.58 | 103.7 / 34.69 | 121.0 / 40.48 | 125.7 / 42.05 | 116.9 / 39.10 |
| 2026-07-28 | 122.4 / 40.94 | 94.5 / 31.61 | 103.8 / 34.72 | 121.1 / 40.51 | 125.8 / 42.08 | |
| 2026-08-03 | 122.5 / 40.98 | 94.6 / 31.64 | 103.9 / 34.76 | | | 117.1 / 39.17 |
| 2026-08-17 | | | | 121.3 / 40.58 | | |
| 2026-09-14 | 122.9 / 41.11 | 95.0 / 31.78 | 104.3 / 34.89 | 121.6 / 40.68 | 126.3 / 42.25 | 117.5 / 39.31 |

The build script asserts, for every case: each coded BMI is the rounded value for its weight; every BMI is at least 30; weights move by at most 1.0 kg between visits except into and out of the month-6 dip; in a program the dip is 3.2 kg and the month-12 weight is 0.6 kg above the intake weight. For `bariatric-01`, `-04`, `-05` and `-06` every BMI is at least 37; for `-02` every BMI is below 33; for `-03` the evaluation BMI is below 35 unrounded and no BMI is 34.95 or more.

## Timelines

Background events common to all six bundles: acute bronchitis visit 2023-10-12; annual exams 2023-10-24, 2024-07-09, 2025-07-15 and 2026-07-21; dental visit 2023-11-07; strep throat 2025-11-22; screening colonoscopy 2026-05-02. None bears on a criterion apart from the rewritten values.

`bariatric-01`, `-02` and `-03` (`-03` is a sleeve request; the dates are the same):

| Date | Event |
| --- | --- |
| 2025-07-28 | Primary care: obesity, referral to the medical weight management clinic; no treatment at this visit |
| 2025-08-04 | Clinic intake: 1,500 kcal/day diet with a dietitian, 150 minutes of walking a week, monthly counseling, orlistat 120 mg three times daily (`MedicationRequest/orlistat`, status `completed`) |
| 2026-02-02 | Month 6: 6 of 6 sessions, 3.2 kg below the intake weight; diet, walking, counseling and orlistat continue |
| 2026-07-28 | Primary care: type 2 diabetes diagnosed on an HbA1c of 7.4%, metformin started |
| 2026-08-03 | Month 12: 12 of 12 sessions, orlistat taken for the 12 months, weight 0.6 kg above intake; program complete, referral to bariatric surgery |
| 2026-09-14 | Surgical evaluation |
| 2026-10-05 | Request |

`bariatric-04`:

| Date | Event |
| --- | --- |
| 2026-07-28 | Primary care: type 2 diabetes diagnosed, metformin started |
| 2026-08-17 | Primary care: referral to bariatric surgery, with weight, BMI and the diabetes; nothing about prior treatment |
| 2026-09-14 | Surgical evaluation, also silent on prior treatment |
| 2026-10-05 | Request |

`bariatric-05`:

| Date | Event |
| --- | --- |
| 2025-07-28 | Primary care: obesity, referral to the medical weight management clinic |
| 2025-08-04 | `note-wm-declined`: telephone intake, the patient declined enrollment, referral closed |
| 2026-07-28 | Primary care: type 2 diabetes diagnosed, metformin started |
| 2026-09-14 | `note-surgical-eval`: states that the patient completed a 12-month program from 2025-08-04 to 2026-08-03 |
| 2026-10-05 | Request |

`bariatric-06`: as `bariatric-01` without the 2026-07-28 visit.

## Design notes

- **bariatric-05 and rule 4.** The clinic note and the evaluation are two clinical notes in the chart. Neither mentions the other and nothing reconciles them. The evaluation gives 2025-08-04 as the first day of the program, the day the clinic note records the refusal, so a later enrollment cannot reconcile them. The label is `insufficient` under GUIDELINES rule 4. That is our reading, not a rule CMS states, and the case is flagged for the blinded second annotation pass. The bundle has no program notes and no orlistat request.

  Text of `note-wm-declined`:

  > 2025-08-04
  > Weight management clinic, telephone intake
  >
  > Referral from primary care, sent 2025-07-28, received. Intake call with the patient today. The patient declined enrollment in the program and does not wish to schedule a first visit. Referral closed.

  The program paragraph of the surgical evaluation of 2026-09-14:

  > Medical weight management: completed a 12-month physician-supervised weight management program (2025-08-04 to 2026-08-03) with a dietitian-led diet, exercise and orlistat, without lasting weight loss.

- **bariatric-06 and the Outcomes table.** The surgical evaluation lists no history in any case: no past medical or surgical history, no review of systems and no statement about co-morbidities. In 06 nothing else in the chart addresses co-morbidity either. GUIDELINES rule 3 is written for criteria phrased as an absence; this criterion is a positive requirement, so the Outcomes table decides: `not_met` needs evidence that the criterion is not satisfied, and silence is not that. A note recording that no co-morbidity was found would make it `not_met` (reserve R6). Text of the evaluation:

  > Reason for visit: evaluation for bariatric surgery after a 12-month weight management program.
  > Measurements today: weight 117.5 kg, height 172.9 cm, BMI 39.3 kg/m2.
  > Medical weight management: completed a 12-month physician-supervised program (2025-08-04 to 2026-08-03: dietitian-led 1,500 kcal/day diet, 150 minutes of walking a week, monthly counseling, orlistat 120 mg three times daily). Weight at completion was 0.6 kg above the weight at intake; no lasting weight loss.
  > Assessment: obesity, BMI 39.3.
  > Plan: laparoscopic Roux-en-Y gastric bypass. Request prior authorization.

- **BMI (rules 1 and 2).** "≥ 35" is stated exactly, so the near miss sits there: 34.89, shown as 34.9. The NCD does not say when the BMI is measured, so in each case every BMI is on one side of 35 (`bariatric-03` has none at 34.95 or more). No class names ("class III", "morbid obesity") stand in for a number.
- **Co-morbidity.** The only co-morbidity used is type 2 diabetes, which the NCD names. No hypertension, sleep apnea, dyslipidemia, GERD or osteoarthritis is added anywhere.
- **Medical treatment.** The NCD states no type or duration, so the met cases use 12 months of physician-supervised diet, walking and counseling plus orlistat taken throughout: clearly medical treatment, clearly unsuccessful (0.6 kg above the intake weight at the end). No GLP-1 drug appears. Metformin's documented reason is diabetes, and the diabetes note gives no diet or exercise advice. No case has a partial program or a dropout, which would raise "how much treatment counts".
- **Procedure.** Only procedures the NCD names, so no case rests on the non-covered list or on a procedure left to the MACs. The "open" approach in `bariatric-05` is deliberate: open Roux-en-Y gastric bypass is covered, while open adjustable banding and open sleeve gastrectomy are not.
- **Sleeve.** `lsg-covered-by-local-mac` is a payer-stage criterion and is left to the reviewer.

## Known limitations

- **Type 2 diabetes in the sleeve variant.** `lsg-obesity-comorbidity` is `met` in `bariatric-03` and `-04` because of type 2 diabetes. The sentence that names it is quoted in the policy file under `obesity-comorbidity`, not under the sleeve criterion, whose own quote is "The beneficiary has at least one co-morbidity related to obesity, and,". The sentence says "for purposes of this NCD", which includes section D, and that is the reading these two labels follow. A policy change that adds the sentence to the sleeve criterion's quote would be a separate pull request, with `scripts/verify_policy_quotes.py`.
- `obesity-comorbidity` is never `not_met` in these six cases: the NCD defines only type 2 diabetes as a co-morbidity, and the not-met path needs a documented absence (reserve R6).
- `nationally-covered-procedure` is met in every case. A non-covered procedure has a code that no variant routes (reserve R2).
- No sleeve case is met, so the sleeve variant's met path is untested (reserve R1).
- The sleeve variant has no procedure criterion in the policy file, so a non-covered open sleeve gastrectomy cannot be scored.
- The bariatric bundles differ from the shared background in the 29 removed and 12 rewritten resources listed above, so they no longer share one patient with the facet and PAP cases.
- `bariatric-05` rests on our reading of rule 4 and awaits the second annotator.
- Single annotator; not clinically validated.

## Reserve cases

Not built; each closes a gap above.

- **R1** sleeve, clean and met (BMI 41, diabetes, program failed).
- **R2** a non-covered procedure (open adjustable gastric banding) with `nationally-covered-procedure` not_met. Needs a decision on how a code that no variant routes is judged.
- **R3** near miss on the met side: 104.7 kg gives BMI 35.02, met under rule 1.
- **R4** documented absence of treatment ("has not tried any medical weight management; wishes to proceed directly to surgery"): `failed-medical-treatment` not_met.
- **R5** type 2 diabetes only in the notes, with no coded Condition and no metformin request: met from free text.
- **R6** documented absence of co-morbidity ("no obesity-related co-morbidity identified on evaluation"): `obesity-comorbidity` not_met.

## Checks before a case is accepted

- Every changed label cites the quoted sentence it follows from.
- Every scored criterion in the variant has a label.
- The overall outcome computed from the labels matches the table.
- The bundle contains the evidence the labels cite, with the resource ids listed.
