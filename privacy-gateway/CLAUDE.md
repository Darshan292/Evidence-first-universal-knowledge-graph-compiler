# ESU Privacy Gateway — Claude Code notes

**Phase 0: design only, no code yet.** Read [README.md](README.md) for the document order and how decisions are tracked.

## Decision hygiene

Decisions live in `ADR/NNNN-*.md`. Each must have:
- A header with `Status:` and `Date:` fields (or inherited from amendments)
- An "Alternatives rejected" section

When proposing a new decision:
1. Assign the next sequential number (check the highest existing ADR)
2. Draft ADR/NNNN-*.md with the structure above
3. Add a row to the table in [DESIGN.md § 10](DESIGN.md#10-decision-index)
4. Update [README.md](README.md) item 2 to list it

## Evaluation gates

Gates are pre-registered in [EVALUATION_PLAN.md](EVALUATION_PLAN.md). They may only change via a **dated amendment** inside that same file; do not edit gates in ROADMAP without recording why.

## Data policy

**Never put real personal data in this repository.** Use synthetic examples only. Verify before committing.
