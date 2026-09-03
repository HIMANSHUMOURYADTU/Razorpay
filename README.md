# AI Finance Controller

Reconciliation **agent** for the Razorpay Buildathon (AI Finance Controller track).

## The bar (30 seconds)

Judges score **throughput + measured accuracy + an honest exception list**.

| Bar | How we hit it |
|---|---|
| Throughput | Full close in seconds, stage-wise: rule → memory → fuzzy → classifier → gated LLM → human |
| Accuracy | Hidden-GT precision **on matched pairs only** — e.g. 100% (68/68), 0 false-positives. We buy that number by refusing to guess. |
| Honesty | Every leftover is DUP / SPLIT / FEE_NET / TIME_LAG / PARTIAL / OOP / UNRESOLVED + a reason + ₹ at risk |
| Compounding | Human-validated **Exception Memory** (not ML). Close 2 and 3 are the **same n as close 1**, new IDs. |

We do not generate a close. We verify one. The LLM is a gated classifier on residue; the **controller** decides AUTO_RESOLVE / HOLD / ESCALATE and owns cash impact.

One line: **zero bad matches, not fake 100% coverage.**

Simple English (why this PS, how it works, how to demo): [docs/GUIDE.md](docs/GUIDE.md)

Full story (problem, why this PS, solution, mermaid architecture): [docs/PROJECT.md](docs/PROJECT.md)

Speak-this: [docs/PITCH.md](docs/PITCH.md) · Extra LLD: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Setup

```
python -m pip install -r requirements.txt
python data/generate_synthetic_data.py
copy .env.example .env
```

Groq in `.env`:

```
LLM_PROVIDER=groq
GROQ_API_KEY=...
GROQ_MODEL=openai/gpt-oss-20b
LLM_CONFIDENCE_THRESHOLD=0.75
```

## Run

```
python -m pytest -q
python -m src.pipeline --no-llm
streamlit run app.py
```

Dashboard: **Close 1** → Exceptions (orphan + LLM refusal / hold) → save CloudStack 2% + 14-day lag → **Close 2** → optional Nimbus 2.36% → **Close 3**. Then **Stress this close** with a judge-dictated scenario.

## Provider switch

`LLM_PROVIDER=groq|gemini|ollama|anthropic`. Matching policy does not change — only the model behind `classify_match`.
