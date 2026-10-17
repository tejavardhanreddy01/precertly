"""The prompts of the facet, PAP and bariatric cases must not change.

tests/golden/prompt_hashes.json holds the sha256 of build_prompt for every scored criterion of
the 23 cases that existed before the spinal cord stimulator cases, with every chunk of the case
(the request chunks, then the chart chunks) in front of the model. It was captured from the code
as it stood before the spinal cord stimulator request fields were added. A request field, a
chunk change or a prompt edit that moves any of these hashes changes what an earlier run saw.

The facet and PAP hashes have not moved since. The 22 bariatric hashes were re-captured once,
when the note pairs of 2025-11-22 and 2026-05-02 were removed from the bariatric bundles
(they list acetaminophen as the only medication while orlistat is active). Each moved only
because those two notes left the evidence and the handles after them were renumbered;
the bariatric cases have no recorded results.
"""

import hashlib
import json
from pathlib import Path

import pytest

from precertly.evals import load_cases, scored_criteria
from precertly.evals.prompt_check import case_chunks
from precertly.judgment.prompt import build_prompt
from precertly.policies import load_policies

GOLDEN = json.loads((Path(__file__).parent / "golden" / "prompt_hashes.json").read_text())
CASES = load_cases()
POLICIES = load_policies()
EXISTING = sorted({key.split("/")[0] for key in GOLDEN})


def test_the_golden_covers_the_23_existing_cases_and_every_scored_criterion():
    assert len(EXISTING) == 23
    assert {CASES[case_id].policy for case_id in EXISTING} == {
        "facet-joint-interventions",
        "pap-sleep-apnea",
        "bariatric-surgery",
    }
    for case_id in EXISTING:
        case = CASES[case_id]
        scored = scored_criteria(POLICIES[case.policy], case.variant)
        assert sorted(k.split("/")[1] for k in GOLDEN if k.startswith(f"{case_id}/")) == sorted(
            scored
        )


@pytest.mark.parametrize("case_id", EXISTING)
def test_prompts_are_byte_identical_to_the_golden(case_id):
    case = CASES[case_id]
    policy = POLICIES[case.policy]
    chunks = list(case_chunks(case).values())
    for criterion_id in scored_criteria(policy, case.variant):
        prompt = build_prompt(policy.criterion(criterion_id), case.request, chunks)
        digest = hashlib.sha256(prompt.encode()).hexdigest()
        assert digest == GOLDEN[f"{case_id}/{criterion_id}"], f"{case_id} {criterion_id}"
