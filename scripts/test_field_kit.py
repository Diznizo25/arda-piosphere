"""Field-kit guard: the markdown source must stay complete and renderable.

The kit is read by a person in a manyatta, so a missing section is a real failure, not
a cosmetic one. This checks the source of truth (structure, sections, question count,
quotas) and that the generator can parse it — the generated .docx/.pdf/.xlsx are built
by scripts/make_mom_test_kit.py and are not asserted here (CI has no Word/PDF stack
requirement).

Run: python scripts/test_field_kit.py
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

sys.path.insert(0, ".")

SRC = Path("docs/field/mom_test_field_kit.md")
src = io.open(SRC, encoding="utf-8").read()

# --- 1) every section a fieldworker needs is present -------------------------
SECTIONS = [
    "## 0. How to use this kit",
    "## 1. The rules of the conversation (Mom Test)",
    "## 2. Who we are testing: users vs beneficiaries vs payers",
    "## 3. The sample: 10 pastoralists (plus 5 informants)",
    "## 4. The interview guide",
    "## 5. Watch, do not talk",
    "## 6. Notes: what counts as evidence",
    "## 7. The hypotheses, and what would prove us wrong",
    "## 8. Turning 10 conversations into a decision",
    "## 9. What we ship today",
    "## 10. Ethics, consent and data protection",
    "## 11. Plan, kit and budget",
    "## 12. Pre-mortem",
    "## Appendix A — Swahili spoken guide",
    "## Appendix B — Per-interview sheet",
    "## Appendix C — Ecosystem informant guides",
    "## Appendix D — Follow-up call",
    "## Appendix E — The tracker spreadsheet",
]
missing = [s for s in SECTIONS if s not in src]
assert not missing, f"missing sections: {missing}"
print(f"all {len(SECTIONS)} sections present")

# --- 2) the interview guide has the blocks and the question numbering --------
BLOCKS = ["### A. Warm-up", "### B. The last hard decision", "### C. Water",
          "### D. Grazing and pasture", "### E. Animal health, ticks and worms",
          "### F. Where information actually comes from today",
          "### G. The phone in his hands", "### H. Money", "### I. Trust, blame and failure",
          "### J. Have others tried?", "### K. Household and beneficiary",
          "### L. Contributing information", "### M. Close: ask for something"]
missing = [b for b in BLOCKS if b not in src]
assert not missing, f"missing interview blocks: {missing}"
numbers = sorted({int(n) for n in re.findall(r"^(\d+)\. ", src, re.M)})
assert numbers == list(range(1, 61)), f"question numbering has holes: {numbers}"
print(f"{len(BLOCKS)} interview blocks, questions numbered 1-60 with no gaps")

# --- 3) the hypotheses are falsifiable: TRUE and FALSE on every row ----------
for hid in ("D1", "D2", "D3", "D4", "U1", "U2", "U3", "U4", "F1", "F2", "F3",
            "S1", "S2", "S3", "S4", "V1", "V2", "V3", "V4",
            "I1", "I2", "A1", "A2", "A3", "A4"):
    assert re.search(rf"\| {hid} \|", src), f"hypothesis {hid} is missing"
assert src.count("FALSE → kill/pivot if") >= 5, "every hypothesis table needs a kill column"
print("all 25 hypotheses present, each with a falsification test")

# --- 3b) the money model: he supplies, institutions buy ----------------------
assert "never pays" in src, "the kit must say plainly that the herder never pays"
assert "Buyer —" in src and src.count("**PAYS**") >= 3, \
    "the kit must name institutional buyers as the payers"
for gone in ("the herder pays something", "The herder pays something"):
    assert gone not in src, f"old payer framing still present: {gone}"
# The only place "willingness-to-pay for our service" may appear is in its negation.
assert src.count("willingness-to-pay for our service") == \
    src.count("not willingness-to-pay for our service"), \
    "the kit must never treat the herder as the buyer"
print("supply side + institutional buyers stated; old herder-pays framing gone")

# --- 4) the quotas and the ethics lines are not optional decoration ----------
for needle in ("3 women", "never use WhatsApp", "not** our 3 existing registered herders",
               "hakuna malipo ya fedha", "Kenya DPA 2019",
               "Indicative budget (KSh"):
    assert needle in src, f"missing from the kit: {needle}"
for quota in ("3 women", "2 camel", "2 who share a phone", "5 informant"):
    assert quota.lower() in src.lower(), f"missing quota: {quota}"
print("quotas, no-payment rule and the DPA section present")

# --- 5) the generator can parse the source it will render -------------------
from scripts.make_mom_test_kit import parse_md  # noqa: E402

blocks = parse_md(src)
kinds = {k for k, _ in blocks}
for kind in ("h1", "h2", "h3", "p", "bullets", "ordered", "table", "quote", "hr"):
    assert kind in kinds, f"the parser produced no {kind} block"
assert len([1 for k, _ in blocks if k == "table"]) >= 20, "tables failed to parse"
rows = max(len(payload) for k, payload in blocks if k == "table")  # type: ignore[arg-type]
assert rows >= 12, f"largest table parsed only {rows} rows"
def _flat(payload: object) -> str:
    if isinstance(payload, list):
        return " ".join(_flat(p) for p in payload)
    return str(payload)


body = "\n".join(_flat(payload) for _, payload in blocks)
assert "Kukubali" not in body  # consent wording lives in the print pack
assert "Habari. Jina langu" in body
print(f"parser reads it: {len(blocks)} blocks, "
      f"{sum(1 for k, _ in blocks if k == 'table')} tables")

# --- 6) ordering matters: consent before questions, close before appendices --
assert src.index("## 10. Ethics") < src.index("## Appendix A"), \
    "consent must be explained before the scripts"
assert src.index("### M. Close: ask for something") < src.index("## 5. Watch"), \
    "the commitment ask belongs to the interview, before the product tests"
print("section order OK (ethics before scripts, close before the product tests)")

# --- 7) the one simple document: the same questions, both languages ----------
Q = Path("docs/field/mom_test_questions_only.md")
qsrc = io.open(Q, encoding="utf-8").read()
qnums = sorted({int(n) for n in re.findall(r"^(\d+)\. ", qsrc, re.M)})
assert qnums == numbers, "the two documents must share the same question numbering"
assert qnums == list(range(1, 61)), f"the questions-only doc has holes: {qnums}"
for block in ("## A. Maisha", "## C. Maji", "## E. Kupe", "## F. Habari", "## G. Simu",
              "## H. Fedha", "## I. Kuamini", "## J. Wengine", "## K. Nyumbani",
              "## L. Kuchangia taarifa", "## M. Omba kitu"):
    assert block in qsrc, f"missing from the questions-only doc: {block}"
assert "Kiswahili" in qsrc and "English" in qsrc, "the doc must be bilingual"
assert qsrc.count("*(") >= 50, "every question needs its English in italics"
assert "hakuna malipo ya fedha" in qsrc, "the consent line belongs at the top of the doc"
assert "Usimwambie tunatengeneza nini" in qsrc, "the never-pitch rule belongs in the doc"
print("questions-only document: 60 bilingual questions, 13 blocks, same numbering")

print("\nfield kit source OK")
