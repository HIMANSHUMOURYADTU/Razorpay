# Finance Controller Agent — Architecture

Reconciliation agent for **Razorpay Buildathon**, track **AI Finance Controller**.

Start here for the full story (problem, why this PS, solution, mermaid architecture): **[PROJECT.md](PROJECT.md)**.

This file is extra LLD / runbook: problem recap, HLD, LLD, data, pipeline, taxonomy, LLM contract, exception memory, metrics, and how to run it.

---

## 1. Problem

Match a Razorpay-style **settlement export** (source A) to an **internal ledger** (source B) on a 50+ record batch, and produce:

1. A **stage-wise match rate** (rule / learned_rule / fuzzy / classifier / LLM-assisted)
2. A **precision estimate** on the hard matches (scored against hidden ground truth the matcher never sees)
3. A **complete exception list** — every unresolved record has a taxonomy code and a one-sentence reason
4. A **compounding effect** — close 2 and close 3 are **full-size reruns** (same n, new IDs) that get faster because a human labeled close-1 exceptions into Exception Memory (human-validated policy, not ML)

The bar we build against: *throughput plus measured accuracy plus an honest exception list. One cherry-picked match proves nothing.*

---

## 2. Core design principle

**Deterministic matching first.** LLM calls only as a **confidence-gated assist** on residual ambiguous records.

- Never a silent black box
- Never force-match below a confidence threshold (fuzzy floor 0.70, LLM floor 0.75)
- Every match and every exception is logged with: record ids, stage, confidence, taxonomy (if exception), reason, timestamp
- The audit trail is the product, not polish

---

## 3. High-level design (HLD)

```mermaid
flowchart TD
    SA[Source A: Razorpay settlement export] --> ING[Ingestion and normalization]
    SB[Source B: Internal ledger] --> ING

    ING --> S1[Stage 1: Rule matcher<br/>order_ref + amount ±0.01 + date ≤ 3d<br/>same calendar month · conf 1.0]
    S1 -->|matched| AT[Audit trail JSONL]
    S1 -->|unmatched| LR[Learned rules<br/>Exception Memory · conf 0.95]

    LR -->|matched| AT
    LR -->|unmatched| S2[Stage 2: Fuzzy matcher<br/>amount bands 0.05 / 0.25 / 1.00<br/>date ≤ 7d · description tiebreaker]

    S2 -->|matched if conf ≥ 0.70| AT
    S2 -->|still unmatched| S3[Stage 3: Exception classifier<br/>exactly one taxonomy code + reason]

    S3 -->|SPLIT sum-match| AT
    S3 -->|classified residue| GATE{Plausibly resolvable<br/>and not UNRESOLVED / SPLIT group?}

    GATE -->|yes| S4[Stage 4: LLM assist<br/>provider-agnostic classify_match<br/>JSON only]
    GATE -->|no| EX[Exception list]

    S4 -->|is_match and conf ≥ 0.75| AT
    S4 -->|below threshold or no| EX

    EX --> EM[Exception Memory]
    EM -.human labels a rule.-> LR
    AT --> RPT[Report + Streamlit control room]
```

```mermaid
flowchart TD
    CTRL[Finance Controller agent]
    CTRL --> RECON[Reconciliation engine]
    CTRL --> EXR[Exception reasoning]
    CTRL --> CASH[Cash impact]
    RECON --> DEC[Decision / policy]
    EXR --> DEC
    CASH --> DEC
    DEC --> AUTO[AUTO_RESOLVE]
    DEC --> HUM[HOLD / ESCALATE · human review]
    HUM --> EM[Exception Memory · human-validated]
    EM --> RECON
```

The **matcher is unchanged**: rule → learned_rule → fuzzy → classifier → gated `classify_match`. The controller layer sits **above** it: it does not invent matches; it decides what the leftover *means* for a close (HOLD vs ESCALATE), ranks ₹ at risk, and turns a human sentence into the next close's `learned_rule`.

- Every match traces to *which* stage resolved it
- The LLM only sees the hardest residue, with a stated confidence and reason
- Exception Memory is the compounding piece: labeled patterns skip fuzzy/LLM on the next batch

---

## 4. Record-level sequence (HLD)

```mermaid
sequenceDiagram
    participant A as Settlement row
    participant B as Ledger row
    participant S1 as Rule matcher
    participant MEM as Exception Memory
    participant S2 as Fuzzy matcher
    participant S3 as Classifier
    participant LLM as LLM provider
    participant AT as Audit trail

    A->>S1: candidate
    B->>S1: candidate
    alt exact order_ref + amount + date window
        S1->>AT: match stage=rule conf=1.0
    else residual
        S1->>MEM: check learned patterns
        alt vendor fee / widened lag / OOP rule hits
            MEM->>AT: match stage=learned_rule conf=0.95
        else still open
            MEM->>S2: residual
            alt within amount band + date + description
                S2->>AT: match stage=fuzzy conf 0.70–0.99
            else still open
                S2->>S3: classify
                alt SPLIT amounts sum
                    S3->>AT: match stage=classifier conf=0.92
                else FEE_NET / TIME_LAG / PARTIAL / OOP / DUP / UNRESOLVED
                    S3->>AT: exception + taxonomy + reason
                    opt LLM-resolvable and counterpart free
                        S3->>LLM: two records + taxonomy
                        alt conf ≥ 0.75
                            LLM->>AT: match stage=llm_assisted
                        else
                            LLM->>AT: exception + LLM reason, not force-matched
                        end
                    end
                end
            end
        end
    end
```

---

## 5. Low-level design (LLD)

### 5.1 Module map

```mermaid
flowchart LR
    subgraph ingest
        IN[src/ingestion.py]
    end
    subgraph match
        CFG[src/config.py]
        U[src/match_utils.py]
        R[src/matcher_rules.py]
        F[src/matcher_fuzzy.py]
        LRN[src/learned_rules.py]
        C[src/exception_classifier.py]
    end
    subgraph llm
        BASE[src/llm_providers/base.py]
        FAC[get_provider]
        G[gemini]
        Q[groq]
        O[ollama]
        AN[anthropic]
        ASSIST[src/llm_assist.py]
    end
    subgraph persist
        AT[src/audit_trail.py]
        EM[src/exception_memory.py]
    end
    subgraph out
        PIPE[src/pipeline.py]
        REP[reports/report_generator.py]
        UI[app.py]
    end

    IN --> PIPE
    CFG --> PIPE
    R --> PIPE
    LRN --> PIPE
    F --> PIPE
    C --> PIPE
    ASSIST --> PIPE
    FAC --> G & Q & O & AN
    BASE --> FAC
    ASSIST --> FAC
    PIPE --> AT
    PIPE --> EM
    PIPE --> REP --> UI
```

### 5.2 Pipeline order (code path)

`src/pipeline.py :: run_pipeline`

1. `load_source_a` / `load_source_b` — drop any accidental ground-truth columns
2. `match_rules` — 1:1 greedy by `(date_delta, amount_delta, id)`
3. `apply_learned_rules` — FEE_NET reconstruct, TIME_LAG window, OOP adjacent month
4. `match_fuzzy` — scored globally, then greedy by confidence
5. `classify_exceptions` — SPLIT sum-match; everything else coded, not force-matched
6. `run_llm_assist` — one `classify_match` per A/B pair that still has a free counterpart
7. Invariant: every remaining exception has non-empty `taxonomy_code` and `reason`
8. Persist exceptions to `data/last_exceptions.json` for the label CLI

Learned rules sit **after Stage 1 and before fuzzy**, so batch 2 never pays the fuzzy/LLM cost for a pattern already labeled.

### 5.3 Matching gates

| Stage | Amount | Date | Other | Confidence | Force-match? |
|---|---|---|---|---|---|
| Rule | ≤ ₹0.01 | ≤ 3 days, same month | exact `order_ref`, same currency | 1.00 | only if all gates pass |
| Learned FEE_NET | reconstruct gross at stored % | not required | vendor substring | 0.95 | only if reconstruction hits |
| Learned TIME_LAG | ≤ ₹0.01 | ≤ stored window (default 14d) | same `order_ref` | 0.95 | only if amount matches |
| Fuzzy | bands 0.05 / 0.25 / 1.00 | ≤ 7 days, same month | `token_set_ratio` ≥ 40 | 0.90 − 0.10×band − date penalty, cap 0.99 | discarded if &lt; 0.70 |
| Classifier SPLIT | combo sum ≤ ₹0.05 | — | 2–4 legs, same `order_ref` | 0.92 | only exact combinatorial sum |
| LLM | model opinion only | — | taxonomy already assigned | model 0–1 | accepted only if `is_match` and ≥ 0.75 **and** counterpart still free |

1:1 assignment is absolute: a ledger row already taken cannot be assigned again, including by the LLM (covers DUP).

### 5.4 Exception classifier (LLD)

Priority on residuals after Stage 2:

```mermaid
flowchart TD
    U[Unmatched A/B] --> DUP{Unmatched A clones an already-matched order_ref+amount?}
    DUP -->|yes| D[DUP · do not match]
    DUP -->|no| SP{≥2 unmatched A and 1+ B same order_ref?}
    SP -->|sum within 0.05| SM[Match stage=classifier SPLIT]
    SP -->|sum fails| SX[SPLIT exception · no 1:1]
    SP -->|1:1 pair| P{amount vs dates}
    P -->|amount match, different month| OOP[OOP]
    P -->|amount match, date > 7d| TL[TIME_LAG]
    P -->|reconstructs at 2% or 2.36%| FN[FEE_NET · evidence only, no auto-match]
    P -->|A/B ratio in 50–90%| PA[PARTIAL]
    P -->|delta ≤ ₹1 leftover| FX[FX_ROUND]
    P -->|no counterpart at all| UN[UNRESOLVED]
```

FEE_NET is **proven** by reconstructing gross from 2.00% and 2.36% (Razorpay 2% + GST = 2.36%) and written into the reason, but it is **not auto-matched**. That leaves a human-labelable exception so Exception Memory can fire on batch 2.

---

## 6. Data model

```mermaid
erDiagram
    SOURCE_A {
        string txn_id PK
        string order_ref
        decimal amount
        date settlement_date
        string description
        string currency
    }
    SOURCE_B {
        string ledger_id PK
        string order_ref
        decimal amount
        date posting_date
        string description
        string currency
    }
    MATCH {
        string txn_id
        string ledger_id
        string stage
        float confidence
        string reason
        string taxonomy_code
        string provider
    }
    EXCEPTION {
        string exception_id PK
        string taxonomy_code
        string reason
        string status
    }
    MEMORY {
        string pattern_id PK
        string taxonomy_code
        string rule
        string vendor
        string fee_rate
        int date_window_days
        int times_applied
    }
    SOURCE_A ||--o{ MATCH : matched
    SOURCE_B ||--o{ MATCH : matched
    SOURCE_A ||--o{ EXCEPTION : may_raise
    EXCEPTION ||--o| MEMORY : labeled_into
```

Ground truth lives only in `data/ground_truth.csv` (`gt_group`, `taxonomy`, expected ids). Ingestion **drops** those columns if someone concatenates them onto a source file. Precision is computed in `reports/report_generator.py` after the fact.

### Seeded traps (batch 1)

| Taxonomy | Groups | Intent |
|---|---|---|
| CLEAN | 55 | Stage 1 exact |
| DUP | 4 | 1:1 leaves one extra settlement |
| SPLIT | 3 | two A rows sum to one B |
| FX_ROUND | 6 | ±0.01 to ±0.05 paise |
| FEE_NET | 4 | CloudStack 2%, Nimbus 2.36% |
| TIME_LAG | 3 | T+9 / T+10, outside 7d window |
| PARTIAL | 3 | 20–40% refunded |
| OOP | 2 | July books, August settlement |
| UNRESOLVED | 2 | one orphan A, one orphan B |

Batch 2 and batch 3 are **full-size twins** of batch 1 (same trap mix, new IDs). Compare 71/88 to 76/88, never 16/18 next to 71/88.

Eval packs live in `data/eval/` (easy / adversarial / shifted). Live inject rows: `data/stress_scenarios.json`.

---

## 7. LLM provider contract

All providers implement one method and return one shape:

```text
classify_match(record_a, record_b, taxonomy_code) ->
  { "is_match": bool, "confidence": float, "reason": str }
```

The prompt template is **identical** across Gemini, Groq, Ollama, and Anthropic. Switching `LLM_PROVIDER` changes the model, not the matching policy.

```mermaid
flowchart LR
    ENV[LLM_PROVIDER] --> F[get_provider]
    F -->|gemini| GE[GEMINI_API_KEY]
    F -->|groq| GR[GROQ_API_KEY]
    F -->|anthropic| AN[ANTHROPIC_API_KEY]
    F -->|ollama| OL[localhost:11434 · no key]
    F -->|missing key| ERR[ProviderConfigError<br/>no silent fallback]
    GE --> P[parse strict JSON]
    GR --> P
    AN --> P
    OL --> P
    P --> GATE{conf ≥ LLM_CONFIDENCE_THRESHOLD}
    GATE -->|yes and counterpart free| M[match llm_assisted]
    GATE -->|no| X[exception + LLM reason]
```

HTTP layer: retry with exponential backoff on 429/5xx, then a clear rate-limit error. Free tiers throttle; the run must not die silently.

This repo is configured to use **Groq** via `.env` (`LLM_PROVIDER=groq`, model `openai/gpt-oss-20b` — Llama 3.1 8B Instant was retired on free/dev tiers in August 2026). The key stays in `.env` (gitignored). Do not paste keys into docs, CSVs, or the audit log.

---

## 8. Exception Memory (the compounding piece)

```mermaid
flowchart LR
    E[Batch 1 exception] --> H[Human labels a rule]
    H --> J[data/exception_memory.json]
    J --> B2[Batch 2 Stage learned_rule]
    B2 -->|CloudStack net of 2%| FEE[FEE_NET reconstruct and match]
    B2 -->|14-day lag allowed| LAG[TIME_LAG amount match]
```

CLI:

```bash
python -m src.exception_memory label exc-A-pay_A00076 "CloudStack SaaS settlements are net of 2% fee" --taxonomy FEE_NET --vendor "CloudStack SaaS" --fee-rate 0.02
python -m src.exception_memory label exc-A-pay_A00080 "Allow 14-day settlement lag for delayed payouts" --taxonomy TIME_LAG --date-window-days 14
```

Vendor is bound **only** for FEE_NET. A TIME_LAG sentence containing the word “settlement” is not parsed as a vendor name.

---

## 9. Metrics (what we show at demo)

Precision is measured **only on matched pairs**. We buy a high number by refusing to guess on the rest — that is why the exception list matters as much as the match rate. Objective: **zero false-positive matches**, not artificial 100% coverage.

| Metric | Close 1 (no LLM, typical) | Close 2 after CloudStack 2% + 14d lag |
|---|---|---|
| Match rate A | 80.7% (**71/88**) | higher on **the same n=88**, new IDs |
| Verified precision | 100% (**68/68** pairs, **0** false-positive) | 100% on matched pairs |
| A-side exceptions | coded + ranked by ₹ | fewer FEE_NET / TIME_LAG |
| Learned rule | 0 | 2+ |
| LLM calls | residue only | drop when memory hits first |

Scale (labeled **estimates**): wall-clock is measured; $/1,000 records assumes this exception mix and ~$0.0002 per LLM call. Rules are ~free; LLM assist is the expensive tail.

Exception Memory is **human-validated policy**, not ML. Do not pitch it as a trained model.

---

## 10. Demo script (3 minutes)

Story, not a feature tour. Full lines: [docs/PITCH.md](PITCH.md).

1. Verification problem, not matching. Refuse to fabricate certainty.
2. Close 1. Say **80.7% (71/88)** and **100% (68/68 matched pairs, 0 false-positive)** plus the precision-scope sentence.
3. Exceptions: orphan ESCALATE, DUP, FEE_NET HOLD, one LLM refusal if present.
4. Label CloudStack 2% + 14-day lag. Call it policy memory, not ML.
5. Close 2 (same n). Learning curve. Stress inject if a judge offers a scenario.

---

## 11. Repository map

```text
finance-controller-agent/
├── app.py                          Streamlit control room
├── components/                     dashboard views + charts
├── styles/theme.css
├── data/
│   ├── generate_synthetic_data.py
│   ├── source_a.csv / source_b.csv
│   ├── batch2_source_*.csv / batch3_source_*.csv
│   ├── eval/                       easy · adversarial · shifted
│   ├── stress_scenarios.json       live inject
│   ├── ground_truth.csv            scoring only
│   └── exception_memory.json
├── src/
│   ├── pipeline.py
│   ├── controller_policy.py        HOLD / ESCALATE · ₹ impact · severity
│   ├── observability.py            LLM funnel · labeled $/1k estimates
│   ├── matcher_*.py / exception_*.py / llm_*
├── reports/report_generator.py
├── tests/
├── docs/ARCHITECTURE.md            this file
└── .env.example
```

---

## 12. Runbook

```bash
python -m pip install -r requirements.txt
python data/generate_synthetic_data.py
copy .env.example .env          # then set LLM_PROVIDER + the matching key
python -m pytest -q
python -m src.pipeline          # uses .env; add --no-llm to skip
streamlit run app.py
```

Environment:

| Variable | Required when |
|---|---|
| `LLM_PROVIDER` | always (default `gemini`; this demo uses `groq`) |
| `GROQ_API_KEY` | `LLM_PROVIDER=groq` |
| `GEMINI_API_KEY` | `gemini` |
| `ANTHROPIC_API_KEY` | `anthropic` |
| `OLLAMA_HOST` / `OLLAMA_MODEL` | optional for `ollama` |
| `LLM_CONFIDENCE_THRESHOLD` | optional, default `0.75` |

Missing key for the **selected** provider is a hard error at `get_provider()`, surfaced as “LLM assist skipped: …” in the pipeline — never a silent hop to another vendor.
