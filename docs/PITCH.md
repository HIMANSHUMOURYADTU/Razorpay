# Pitch — 3 minutes, a story, not a feature tour

Do not read the architecture doc. Do not explain every tab.

## 0:00–0:20 — The problem

Finance teams don’t have a matching problem. They have a **verification** problem.

A Razorpay settlement export and an internal ledger almost match — then fees are netted, payouts split, dates slip, and some rows have **no counterpart at all**. A generic LLM over the CSV will invent matches. A controller will not sign that.

We process an entire close and **refuse to fabricate certainty**.

## 0:20–1:05 — Close 1 (click ▶ Close 1)

Call the numbers **with n**:

- Match rate **80.7% (71/88)** — not “about 80%”.
- Verified precision **100% (68/68 matched pairs, 0 false-positive)**.
- Then the sentence before they ask: *Precision is measured only on matched pairs. We buy 100% by refusing to guess on the rest, which is why the exception list matters as much as the match rate.*

Point at ₹ at risk. Point at the Sankey: every settlement went to a stage or to human review.

Pipe: Rule first (confidence 1.0). Memory empty on purpose. Fuzzy. Classifier splits. LLM last, floor 0.75.

## 1:05–1:50 — Exceptions are the product

Command center, not a dump.

1. **UNRESOLVED** `pay_A00088` — ESCALATE, high ₹, no counterpart. We seeded the orphan so we cannot fake a 100% close.
2. **DUP** — extra settlement, ledger already consumed. 1:1 wins even if a model proposes a second glue.
3. **FEE_NET** `pay_A00076` CloudStack — reconstructed 2%, still HOLD. That is how it becomes policy instead of a silent guess.
4. If LLM is on: open **one refusal** (below 0.75, stayed exception). *Most demos only show matches. We show the gate saying no.*

## 1:50–2:20 — Memory (not ML)

Save: `CloudStack SaaS settlements are net of 2% fee`.  
Save: `Allow 14-day settlement lag for delayed payouts`.

Say: **human-validated exception memory** — experience-driven policy, not machine learning.

## 2:20–2:50 — Close 2 and Close 3 (same n)

**Close 2** is another **88-row** close, new IDs, same mix — not an 18-row slice. Typical after CloudStack 2% + 14-day lag: **80.7% (71/88) → 86.4% (76/88)** with **5 learned_rule** hits and **100% (73/73) precision**.

If time: label Nimbus 2.36%, run **Close 3**. The curve is the compounding proof.

## 2:50–3:00 — Stress it

**Stress this close**: 3% fee / T+20 / orphan. Judge can pick.

*If taxonomy generalizes, this is the only five seconds that matter.*

Stop on the learning chart.

## If they attack

| Attack | Answer |
|---|---|
| Where is the agent? | Matcher verifies. Controller decides AUTO_RESOLVE / HOLD / ESCALATE, ranks ₹ at risk, writes memory, owns the next close. |
| 100% precision? | On matched pairs only. Exceptions are intentional. Zero false-positives. |
| Different data? | Eval packs: easy / adversarial / shifted. Plus live inject. |
| Scale? | Scale tab: measured wall-clock; **estimates** per 1,000 records, LLM as the expensive tail. |
| Is memory ML? | No. Human-validated policy memory. |

## One line

**We don’t generate a close. We verify one — and we remember the exceptions.**
