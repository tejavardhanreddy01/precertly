# Draft PAS requests

For each facet case, a draft [Da Vinci Prior Authorization Support](https://hl7.org/fhir/us/davinci-pas/2.2.1/) request for human review, built from the recorded verdicts in `evals/results/2026-10-14-experiment-1.1a-rescored-issue4.json`. No model is called. **Nothing here is ever submitted, and nothing calls a payer.**

```bash
uv run python -m precertly.pas            # rebuild the 22 files here
uv run python -m precertly.pas --check    # fail if a committed file is stale
uv run python scripts/validate_pas.py     # HL7 FHIR Validator, offline; what CI runs
uv run python scripts/validate_pas.py --tx   # also asks tx.fhir.org; a report, not a gate
```

Each case has two files:

- `<case>.draft.json`: a Bundle conforming to `profile-pas-request-bundle` (PAS 2.2.1, package `hl7.fhir.us.davinci-pas#2.2.1`, pinned by sha256). It holds the Claim, the beneficiary, a placeholder Coverage, insurer and requestor, and every note cited by any verdict.
- `<case>.envelope.json`: the review envelope. It is not FHIR. It holds the verdicts, readiness, attachments and who cites them, the diagnosis records, placeholders, X12 codes and reviewer tasks.

## What the draft says, and what it doesn't

- **No verdicts in the Bundle.** PAS has no element for a provider-side determination. A met, not_met or rationale in a payer-facing field would read as an attestation by the provider. The verdicts are in the envelope.
- **Draft status.** The profile fixes `Claim.status` to `active`, so `draft` is not allowed. The Bundle and the Claim instead carry `meta.tag` `review-state#draft-pending-human-approval`. The envelope says `approval: pending` and `readiness.ready: false`, with the reasons. There is no submission code path.
- **Attachments.** Every note or report cited by any verdict is attached, whatever its outcome. The tool never filters out unfavourable evidence; the reviewer decides what to send. Content is copied verbatim. Only the US Core classification is added: a LOINC document type from the reviewed table in `src/precertly/pas/builder.py`, and category `clinical-note`. Encounter links are dropped from the copies and recorded in the envelope. An MRI report's presentedForm is attached as a DocumentReference.
- **Coded evidence is listed, not attached.** The synthetic fixtures' Conditions, Procedures and Observations do not conform to US Core, and changing them would change the judgment prompts. The envelope lists them under `cited_not_attached`.
- **Diagnoses.** These come only from `src/precertly/pas/diagnosis_map.yaml`, a reviewed mapping to billing article A58364 v42's covered ICD-10-CM list. The one candidate Condition, chronic low back pain, has no equivalent on that list, so no draft carries a diagnosis. Every envelope is `not ready: no covered diagnosis code (A58364 v42)`, with a reviewer task that suggests no code.
- **Placeholders.** The fixtures contain no payer, coverage or provider. Those resources are labelled placeholders, and every identifier is under `https://github.com/tejavardhanreddy01/precertly/synthetic/`, a namespace this project owns, so no value can be taken for or collide with a real one. No NPI, TIN or NAIC appears: a Luhn-valid NPI placeholder could be a real provider's.
- **X12 codes.** X12 terminology is X12's copyright and its code lists are for members (PAS 2.2.1, "X12 Terminology"). The drafts use only codes that appear in the IG's own examples, as codes without displays, and the envelope says which example each comes from. CPT codes are codes only too.
- **Place of service.** Comes from the case request (`place_of_service`, `11` in every facet case). The judgment prompt never shows it.

## Validation

`scripts/validate_pas.py` runs the official HL7 FHIR Validator 6.10.4 (`validator_cli.jar`, sha256-checked) with `-ig` on the pinned PAS package, offline (`-tx n/a`), over all 11 drafts at once. It fails on any error, and on any warning not in `validator-allowlist.yaml`. Each allowed warning carries its reason: X12 code systems and value sets the validator cannot see, the base R4 identifier-type binding, and the narrative best-practice note. CI runs it in the `pas-validate` job.

Offline mode cannot check codes from external code systems such as CPT, LOINC and the place-of-service codes. `--tx` adds tx.fhir.org for those, as a report.
