"""The case counts stated in the docs must match the case files.

`evals/README.md` carries one table of counts between the `case-counts` markers. This test
recomputes every cell from the case files, the PLAN.md type columns and the recorded runs,
checks that the Total row is the sum of the rows, and checks the counts stated in prose in
the README files and docs/QUALITY.md against the table.
"""

import json
import re
from collections import Counter
from pathlib import Path

import pytest

from precertly.evals import EVALS_DIR, load_cases

ROOT = EVALS_DIR.parent
README = EVALS_DIR / "README.md"
PROSE_FILES = [ROOT / "README.md", README, ROOT / "docs" / "QUALITY.md"]
TABLE_RE = re.compile(r"<!-- case-counts -->\n(.*?)\n<!-- /case-counts -->", re.DOTALL)

# set names as written in prose -> folder under evals/cases
SET_FOLDERS = {
    "facet": "facet",
    "pap": "pap",
    "bariatric": "bariatric",
    "scs": "spinal-cord-stimulators",
    "spinal cord stimulator": "spinal-cord-stimulators",
}
SET_NAMES = "|".join(sorted(SET_FOLDERS, key=len, reverse=True))
NUMBER_WORDS = (
    "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen"
    "|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|(?:twenty|thirty|forty)-\\w+"
    "|\\d+"
)


def _ints(text: str) -> list[int]:
    return [int(n) for n in re.findall(r"\d+", text)]


def _stated_table() -> dict[str, dict]:
    """Rows of the documented table by folder name, plus the Total row under 'total'."""
    match = TABLE_RE.search(README.read_text(encoding="utf-8"))
    assert match, "evals/README.md lost its <!-- case-counts --> table"
    rows = {}
    for line in match.group(1).splitlines()[2:]:  # header and separator
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        link = re.search(r"cases/([a-z-]+)", cells[1])
        key = link.group(1) if link else "total"
        rows[key] = {
            "cases": int(cells[2]),
            "labeled": int(cells[3]),
            "overall": _ints(cells[4]),
            "mix": _ints(cells[5]),
            "scored": int(cells[6]),
        }
    return rows


def _plan_types(folder: Path) -> Counter:
    """clean / adversarial counts from the Type column of a PLAN.md case table."""
    counts: Counter = Counter()
    for line in (folder / "PLAN.md").read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 6 and re.fullmatch(r"`[a-z]+-\d+`", cells[0]):
            counts[cells[5].split(",")[0]] += 1
    return counts


def _scored_case_ids(cases: dict) -> set[str]:
    """Cases with a verdict for every labeled criterion in one complete results file.

    A canary or partial file does not count: the file must be complete and must cover every
    case of the case's policy, not only some of them.
    """
    scored: set[str] = set()
    for path in sorted((EVALS_DIR / "results").glob("*.json")):
        run = json.loads(path.read_text(encoding="utf-8"))
        if run.get("complete") is not True:
            continue
        verdicts = {
            c["case_id"]: {v["criterion"] for v in c["verdicts"]["verdicts"]} for c in run["cases"]
        }
        for case_id, got in verdicts.items():
            case = cases[case_id]
            policy_cases = {c.id for c in cases.values() if c.policy == case.policy}
            covers_policy = policy_cases <= set(verdicts)
            if covers_policy and {label.criterion for label in case.expected} <= got:
                scored.add(case_id)
    return scored


def _computed() -> dict[str, dict]:
    cases = load_cases()
    scored = _scored_case_ids(cases)
    rows: dict[str, dict] = {}
    for folder in sorted(p for p in (EVALS_DIR / "cases").iterdir() if p.is_dir()):
        ids = {i for i, case in cases.items() if (folder / f"{i}.yaml").exists()}
        overall = Counter(cases[i].expected_overall.value for i in ids)
        types = _plan_types(folder)
        rows[folder.name] = {
            "cases": len(ids),
            "labeled": sum(len(cases[i].expected) for i in ids),
            "overall": [overall["met"], overall["not_met"], overall["insufficient"]],
            "mix": [types["clean"], types["adversarial"]],
            "scored": len(ids & scored),
        }
    return rows


def test_table_matches_the_case_files():
    stated = _stated_table()
    computed = _computed()
    assert {k: v for k, v in stated.items() if k != "total"} == computed


def test_total_row_is_the_sum_of_the_sets():
    stated = _stated_table()
    total = stated.pop("total")
    assert total["cases"] == sum(r["cases"] for r in stated.values())
    assert total["labeled"] == sum(r["labeled"] for r in stated.values())
    assert total["scored"] == sum(r["scored"] for r in stated.values())
    for field in ("overall", "mix"):
        columns = zip(*(r[field] for r in stated.values()), strict=True)
        assert total[field] == [sum(col) for col in columns]
    assert total["cases"] == sum(total["overall"]) == sum(total["mix"])


def _claims(text: str) -> list[tuple[str, str, str]]:
    """(kind, matched text, key) for each count the prose states about the case files."""
    claims = []
    # "29 labeled cases", "29 labeled eval cases", "11 labeled facet cases"
    for m in re.finditer(
        rf"\b({NUMBER_WORDS})\s+labeled\s+(?:eval\s+)?(?:({SET_NAMES})\s+)?(cases|criteria)\b",
        text,
        re.IGNORECASE,
    ):
        claims.append((f"labeled {m[3].lower()}", m[0], f"{m[1]}|{(m[2] or '').lower()}"))
    # "11 facet cases", "6 PAP cases", "11 facet drafts"
    for m in re.finditer(
        rf"\b({NUMBER_WORDS})\s+({SET_NAMES})\s+(cases|drafts)\b", text, re.IGNORECASE
    ):
        claims.append(("set cases", m[0], f"{m[1]}|{m[2].lower()}"))
    # "N of the M labeled cases have (not) been scored"
    for m in re.finditer(
        rf"\b({NUMBER_WORDS})\s+of\s+the\s+({NUMBER_WORDS})\s+labeled\s+(?:eval\s+)?cases\b[^.]*?"
        r"(have not been scored|have been scored)",
        text,
        re.IGNORECASE,
    ):
        claims.append(("scored split", m[0], f"{m[1]}|{m[2]}|{m[3].lower()}"))
    # "184 scored criteria"
    for m in re.finditer(rf"\b({NUMBER_WORDS})\s+scored\s+criteria\b", text, re.IGNORECASE):
        claims.append(("scored criteria", m[0], m[1]))
    return claims


@pytest.mark.parametrize("path", PROSE_FILES, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_counts_stated_in_prose_match_the_table(path):
    stated = _stated_table()
    total = stated["total"]
    sets = {k: v for k, v in stated.items() if k != "total"}
    wrong = []
    for kind, text, key in _claims(path.read_text(encoding="utf-8")):
        if not key.split("|")[0].isdigit():
            wrong.append(f"{text!r}: write the count as a number, so it can be checked")
            continue
        number = int(key.split("|")[0])
        if kind == "labeled cases" or kind == "set cases":
            folder = SET_FOLDERS.get(key.split("|")[1])
            expected = sets[folder]["cases"] if folder else total["cases"]
            if number != expected:
                wrong.append(f"{text!r}: the case files have {expected}")
        elif kind == "labeled criteria":
            folder = SET_FOLDERS.get(key.split("|")[1])
            expected = sets[folder]["labeled"] if folder else total["labeled"]
            if number != expected:
                wrong.append(f"{text!r}: the case files have {expected}")
        elif kind == "scored criteria":
            if number not in {r["labeled"] for r in stated.values()}:
                wrong.append(f"{text!r}: not the labeled-criteria count of any set")
        elif kind == "scored split":
            _, whole, phrase = key.split("|")
            if int(whole) != total["cases"]:
                wrong.append(f"{text!r}: the case files have {total['cases']} labeled cases")
            scored = total["scored"]
            expected = scored if phrase == "have been scored" else total["cases"] - scored
            if number != expected:
                wrong.append(f"{text!r}: the recorded runs give {expected}")
    assert wrong == []


def test_the_docs_state_the_counts_they_are_checked_for():
    """A guard against the scan silently matching nothing."""
    total = _stated_table()["total"]["cases"]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"{total} labeled eval cases" in readme
    assert f"{total} labeled cases" in README.read_text(encoding="utf-8")
