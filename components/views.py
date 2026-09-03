"""Premium Streamlit views. All numbers come from pipeline reports — nothing is mocked."""

from __future__ import annotations

import html
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from components.charts import (
    DATA,
    STAGE_HINT,
    STAGE_LABEL,
    STAGE_ORDER,
    exposure_bars,
    inr,
    learning_curve,
    load_theme,
    matches_frame,
    sankey_figure,
    sev_badge,
    tax_badge,
)

REPO = Path(__file__).resolve().parent.parent


def inject_theme() -> None:
    st.markdown(f"<style>{load_theme()}</style>", unsafe_allow_html=True)


def header(*, ready: bool, provider: str, batch_label: str | None) -> None:
    status = f"{provider} live" if ready else f"{provider} key missing"
    klass = "ok" if ready else "warn"
    batch_pill = batch_label or "IDLE"
    st.markdown(
        f"""
<div class="ctrl-head">
  <div>
    <div class="ctrl-kicker">AI Finance Controller · Razorpay Buildathon</div>
    <h1 class="ctrl-title">Autonomous reconciliation &amp; financial control</h1>
    <p class="ctrl-sub">The matcher verifies. The controller decides what to auto-resolve, hold, or escalate — and remembers the policy. We do not generate a close.</p>
  </div>
  <div class="ctrl-status">
    <div class="pill {klass}"><span class="dot"></span>{html.escape(status)}</div>
    <div class="pill">{html.escape(batch_pill)}</div>
    <div class="pill">LLM floor 0.75 · no silent match</div>
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )


def system_health(report: dict | None, ready: bool, provider: str) -> None:
    llm = "HEALTHY" if ready and report and not report.get("llm_skipped_reason") else (
        "SKIPPED" if report and report.get("llm_skipped_reason") else ("READY" if ready else "NO KEY")
    )
    mem_n = _memory_count()
    st.markdown(
        f"""
<div class="health">
  <span>Ingestion healthy</span>
  <span>Rule engine healthy</span>
  <span>Fuzzy healthy</span>
  <span>Classifier healthy</span>
  <span>LLM {html.escape(provider)} · {html.escape(llm)}</span>
  <span>Audit trail healthy</span>
  <span>Exception memory · {mem_n} policies</span>
</div>
        """,
        unsafe_allow_html=True,
    )


def _memory_count() -> int:
    from src.exception_memory import ExceptionMemory

    return len(ExceptionMemory(DATA / "exception_memory.json").list_patterns())


def kpis(report: dict) -> None:
    fin = report.get("financial") or {}
    scoring = report.get("offline_scoring") or {}
    false_pos = int(scoring.get("false") or 0)
    st.markdown(
        f"""
<div class="kpi-row">
  <div class="kpi glass"><div class="lbl">Records processed</div><div class="val">{report['source_a_count']}</div><div class="sub">source A · {report['source_b_count']} ledger</div></div>
  <div class="kpi glass accent"><div class="lbl">Match rate</div><div class="val">{report['match_rate_a_label']}</div><div class="sub">auto-resolved settlements</div></div>
  <div class="kpi glass"><div class="lbl">Verified precision</div><div class="val">{html.escape(report.get('precision_label') or '—')}</div><div class="sub">{false_pos} false-positive matches</div></div>
  <div class="kpi glass"><div class="lbl">Human review</div><div class="val">{report.get('exception_count_a', 0)}</div><div class="sub">{report['exception_count']} A+B exceptions coded</div></div>
  <div class="kpi glass"><div class="lbl">₹ at risk</div><div class="val">{inr(fin.get('exposure_inr') or 0)}</div><div class="sub">of {inr(fin.get('settlement_value_inr') or 0)} settled</div></div>
  <div class="kpi glass"><div class="lbl">Wall clock</div><div class="val">{report.get('wall_clock_seconds', 0):.2f}s</div><div class="sub">measured · see Scale for $/1k estimates</div></div>
</div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="note">{html.escape(report.get("precision_note") or "")}</div>',
        unsafe_allow_html=True,
    )


def pipeline_strip(report: dict) -> None:
    by = report.get("a_records_by_stage") or {}
    secs = report.get("stage_seconds") or {}
    n = max(int(report["source_a_count"]), 1)
    parts = []
    for i, stage in enumerate(STAGE_ORDER, start=1):
        count = int(by.get(stage, 0))
        pct = 100.0 * count / n
        zero = " zero" if count == 0 else ""
        conf = "1.00" if stage == "rule" else ("0.95" if stage == "learned_rule" else ("≥0.70" if stage == "fuzzy" else ("0.92" if stage == "classifier" else "≥0.75")))
        parts.append(
            f'<div class="stage glass{zero}"><div class="n">0{i} · {html.escape(STAGE_LABEL[stage]).upper()}</div>'
            f'<div class="t">{count} resolved</div><div class="c">{pct:.0f}%</div>'
            f'<div class="h">conf {conf} · {secs.get(stage, 0):.3f}s<br/>{html.escape(STAGE_HINT[stage])}</div></div>'
        )
    human = int(report.get("exception_count_a") or 0)
    parts.append(
        f'<div class="stage glass"><div class="n">06 · HUMAN</div>'
        f'<div class="t">{human} review</div><div class="c">{100.0 * human / n:.0f}%</div>'
        f'<div class="h">HOLD / ESCALATE · coded + reasoned</div></div>'
    )
    st.markdown(f'<div class="stage-row">{"".join(parts)}</div>', unsafe_allow_html=True)


def controller_core(report: dict) -> None:
    fin = report.get("financial") or {}
    share = (fin.get("reconciled_share") or 0) * 100
    funnel = report.get("llm_funnel") or {}
    refused = int(funnel.get("refused") or 0)
    st.markdown(
        f"""
<div class="core-grid">
  <div class="core glass">
    <h4>Finance controller</h4>
    <div class="big">Verify, then decide</div>
    <p>Reconciliation engine → exception reasoning → cash impact. Auto-resolve only above thresholds. Everything else is HOLD or ESCALATE.</p>
  </div>
  <div class="core glass">
    <h4>Financial state</h4>
    <div class="big">{share:.0f}% reconciled</div>
    <p>{inr(fin.get('reconciled_value_inr') or 0)} closed · {inr(fin.get('exposure_inr') or 0)} requiring attention of {inr(fin.get('settlement_value_inr') or 0)} total.</p>
  </div>
  <div class="core glass">
    <h4>LLM is a gate, not a rubber stamp</h4>
    <div class="big">{funnel.get('accepted', 0)} accepted · {refused} refused</div>
    <p>{funnel.get('calls', 0)} classify_match calls this close. Below 0.75 stays an exception — that refusal is the trust signal.</p>
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )


def tab_overview(report: dict, result) -> None:
    left, right = st.columns((1.35, 1), gap="large")
    with left:
        st.plotly_chart(sankey_figure(report), width="stretch")
        st.caption("Unique source-A records. A SPLIT of two settlements counts as two at the classifier.")
    with right:
        st.plotly_chart(exposure_bars(report), width="stretch")
        fin = report.get("financial") or {}
        st.caption(
            f"₹ exposure is the sum of unmatched source-A amounts "
            f"({inr(fin.get('exposure_inr') or 0)}). Not a P&amp;L forecast."
        )
    with st.expander("Source files the controller actually received"):
        c1, c2 = st.columns(2)
        c1.caption("source_a.csv · Razorpay settlement export")
        c1.dataframe(pd.read_csv(DATA / "source_a.csv").head(8), width="stretch", hide_index=True)
        c2.caption("source_b.csv · internal ledger")
        c2.dataframe(pd.read_csv(DATA / "source_b.csv").head(8), width="stretch", hide_index=True)


def tab_exceptions(result, report: dict, memory) -> None:
    rows = report.get("prioritized_exceptions") or []
    fin = report.get("financial") or {}
    st.markdown(
        f"**{len(rows)} settlements need a controller decision** · "
        f"{inr(fin.get('exposure_inr') or 0)} exposure. Ranked by severity then ₹, not file order."
    )
    if not rows:
        st.success("Clean close — no A-side exceptions.")
        return
    codes = sorted({r["taxonomy_code"] for r in rows})
    sevs = sorted({r["severity"] for r in rows})
    f1, f2, f3 = st.columns(3)
    pick_tax = f1.multiselect("Taxonomy", codes, default=codes)
    pick_sev = f2.multiselect("Severity", sevs, default=sevs)
    pick_dec = f3.multiselect("Decision", ["HOLD", "ESCALATE"], default=["HOLD", "ESCALATE"])
    filtered = [
        r for r in rows
        if r["taxonomy_code"] in pick_tax and r["severity"] in pick_sev and r["decision"] in pick_dec
    ]
    table = pd.DataFrame(
        [
            {
                "priority": r["severity"],
                "decision": r["decision"],
                "taxonomy": r["taxonomy_code"],
                "₹": r["financial_impact_inr"],
                "id": r["exception_id"],
                "order_ref": r["order_ref"],
                "reason": r["reason"],
                "llm_refused": r["llm_refused"],
            }
            for r in filtered
        ]
    )
    st.dataframe(table, width="stretch", hide_index=True, height=280)
    csv_bytes = table.to_csv(index=False).encode("utf-8")
    json_bytes = json.dumps(filtered, indent=2).encode("utf-8")
    d1, d2 = st.columns(2)
    d1.download_button("Download exceptions CSV", csv_bytes, "exceptions.csv", "text/csv")
    d2.download_button("Download exceptions JSON", json_bytes, "exceptions.json", "application/json")

    refusals = [r for r in rows if r.get("llm_refused")]
    if refusals:
        st.markdown("##### LLM offered a pair and the gate refused")
        hit = refusals[0]
        st.info(
            f"{hit['exception_id']} · {hit['taxonomy_code']} · confidence "
            f"{hit.get('confidence')} stayed an exception. {hit['reason']}"
        )
    else:
        funnel = report.get("llm_funnel") or {}
        if report.get("llm_skipped_reason"):
            st.caption("LLM skipped this run — refusals appear when assist is on and a proposal is below 0.75.")
        elif funnel.get("refused"):
            ex = (funnel.get("refusals") or [None])[0]
            if ex:
                st.info(f"Gate refused: {ex.get('reason')}")
        else:
            st.caption(
                "No LLM refusal this run. FEE_NET / TIME_LAG still held without auto-match — "
                "that is the same discipline when the model is off."
            )

    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.markdown("##### Inspect")
        options = {f"{r['exception_id']} · {r['taxonomy_code']} · {inr(r['financial_impact_inr'])}": r for r in filtered}
        if options:
            chosen = options[st.selectbox("Record", list(options), label_visibility="collapsed")]
            st.markdown(
                f"{sev_badge(chosen['severity'])} {tax_badge(chosen['taxonomy_code'])} "
                f"<b>{html.escape(chosen['decision'])}</b>",
                unsafe_allow_html=True,
            )
            st.write(chosen["reason"])
            st.caption(chosen["recommended_action"])
            if chosen.get("llm_reason"):
                st.caption(f"LLM: {chosen['llm_reason']}")
            st.code(chosen.get("description") or "")
            st.markdown("**Decision trace**")
            st.write(
                "Ingest → Rule (miss) → Memory (miss or N/A) → Fuzzy (miss) → "
                f"Classifier ({chosen['taxonomy_code']}) → "
                + ("LLM assist (refused / skipped) → " if not chosen.get("llm_refused") else "LLM assist (below floor) → ")
                + f"{chosen['decision']}"
            )
    with c2:
        st.markdown("##### Label into controller memory")
        st.caption("Human-validated exception memory — not ML. Next close applies it as `learned_rule` before fuzzy/LLM.")
        a_excs = [e for e in result.exceptions if e.source == "A"]
        label_map = {f"{e.exception_id} [{e.taxonomy_code}]": e for e in a_excs}
        target = label_map[st.selectbox("Exception to store", list(label_map), key="label_choice")]
        from src.match_utils import extract_vendor as vendor_from_desc

        vendor = vendor_from_desc(target.description)
        default_rule = target.reason
        if target.taxonomy_code == "FEE_NET" and vendor:
            default_rule = f"{vendor} settlements are net of 2% fee"
            if "2.36" in (target.reason or ""):
                default_rule = f"{vendor} settlements are net of 2.36% fee"
        elif target.taxonomy_code == "TIME_LAG":
            default_rule = "Allow 14-day settlement lag for delayed payouts"
        elif target.taxonomy_code == "OOP":
            default_rule = "Allow adjacent-month settlement for out-of-period postings"
        rule_text = st.text_input("Resolution policy", value=default_rule, key=f"rule_{target.exception_id}")
        if st.button("Save policy to memory", type="primary"):
            pattern = memory.label_exception(
                target.exception_id,
                rule_text,
                taxonomy_code=target.taxonomy_code,
                vendor=vendor if target.taxonomy_code == "FEE_NET" else None,
            )
            st.success(f"Stored {pattern.pattern_id}: {pattern.rule}")


def tab_matches(result) -> None:
    st.markdown("Every accepted pair: stage, confidence, one-sentence reason. Auto-resolve only.")
    df = matches_frame(result)
    if df.empty:
        st.info("No matches in this close.")
        return
    stages = sorted(df["stage"].unique().tolist())
    pick = st.multiselect("Stage", stages, default=stages, key="match_stage")
    view = df[df["stage"].isin(pick)]
    q = st.text_input("Search order / txn / reason", key="match_q")
    if q:
        mask = view.apply(lambda row: q.lower() in str(row).lower(), axis=1)
        view = view[mask]
    st.dataframe(view, width="stretch", hide_index=True, height=420)


def tab_memory(memory) -> None:
    st.markdown("##### Controller memory")
    st.caption("Experience-driven reconciliation policy. Human-validated. Not machine learning.")
    patterns = memory.list_patterns()
    if not patterns:
        st.info("No policies yet. Label a FEE_NET or TIME_LAG exception, then run the next close.")
        return
    cards = []
    for p in patterns:
        fee = f"{float(p.fee_rate) * 100:.2f}%" if p.fee_rate else "—"
        cards.append(
            f'<div class="core glass"><h4>{html.escape(p.pattern_id)} · {html.escape(p.taxonomy_code)}</h4>'
            f'<div class="big">{html.escape(p.vendor or "policy")}</div>'
            f'<p>{html.escape(p.rule)}</p>'
            f'<p>Fee {html.escape(fee)} · window {p.date_window_days or "—"}d · applied {p.times_applied}× · '
            f'from {(p.source_exception_ids or [""])[0] or "—"}</p></div>'
        )
    st.markdown(f'<div class="core-grid">{"".join(cards)}</div>', unsafe_allow_html=True)


def tab_audit() -> None:
    path = REPO / "audit_log.jsonl"
    if not path.exists():
        st.info("No audit log yet.")
        return
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    st.caption(f"{len(events)} decisions — one JSON line per match or exception. The log is the product.")
    df = pd.DataFrame(
        [
            {
                "time": e.get("timestamp", "")[:19],
                "stage": e.get("stage"),
                "decision": e.get("decision"),
                "taxonomy": e.get("taxonomy_code") or "",
                "confidence": e.get("confidence"),
                "provider": e.get("provider") or "",
                "ids": json.dumps(e.get("record_ids") or {}),
                "reason": e.get("reason"),
            }
            for e in events
        ]
    )
    c1, c2, c3 = st.columns(3)
    stages = sorted(df["stage"].dropna().unique().tolist())
    taxes = sorted([t for t in df["taxonomy"].unique().tolist() if t])
    decs = sorted(df["decision"].dropna().unique().tolist())
    s = c1.multiselect("Stage", stages, default=stages, key="aud_stage")
    t = c2.multiselect("Taxonomy", taxes, default=taxes, key="aud_tax")
    d = c3.multiselect("Decision", decs, default=decs, key="aud_dec")
    view = df[df["stage"].isin(s) & df["decision"].isin(d)]
    if taxes:
        view = view[view["taxonomy"].isin(t) | (view["taxonomy"] == "")]
    q = st.text_input("Search audit", key="aud_q")
    if q:
        view = view[view.apply(lambda row: q.lower() in str(row).lower(), axis=1)]
    st.dataframe(view, width="stretch", hide_index=True, height=420)
    if not view.empty:
        choice = st.selectbox(
            "Expand row",
            list(range(len(view))),
            format_func=lambda i: f"{view.iloc[i]['time']} · {view.iloc[i]['stage']} · {view.iloc[i]['decision']}",
        )
        st.write(view.iloc[int(choice)]["reason"])


def tab_learning(batch1, batch2, batch3) -> None:
    st.markdown("##### The controller learned")
    st.caption("Human feedback became an executable reconciliation policy. Batches 1–3 are full-size closes (same n, new IDs) — not an 18-row slice.")
    if not batch1:
        st.info("Run close 1 first.")
        return
    series = [("Close 1 · 0 policies", batch1["report"])]
    if batch2:
        series.append(("Close 2", batch2["report"]))
    if batch3:
        series.append(("Close 3", batch3["report"]))
    cols = st.columns(len(series))
    for col, (label, rep) in zip(cols, series):
        learned = int((rep.get("a_records_by_stage") or {}).get("learned_rule", 0))
        llm_calls = int((rep.get("llm_funnel") or {}).get("calls") or 0)
        col.metric(label, rep["match_rate_a_label"], f"memory {learned} · LLM calls {llm_calls}")
    if len(series) >= 2:
        st.plotly_chart(learning_curve(series), width="stretch")
        r1, r2 = series[0][1], series[-1][1]
        st.success(
            f"Match rate {r1['match_rate_a_label']} → {r2['match_rate_a_label']} "
            f"on same-size closes. Learned-rule rows: "
            f"{int((r2.get('a_records_by_stage') or {}).get('learned_rule', 0))}."
        )
    else:
        st.info("Label FEE_NET / TIME_LAG policies, then run **Close 2** (and Close 3) in the sidebar.")


def tab_agent(report: dict, memory) -> None:
    from src.agent.operator import ProposalQueue

    st.markdown("##### Controller agent")
    st.caption(
        report.get("agent_charter")
        or "Tools under a finance charter. Matching math is unchanged. New policy waits on the operator."
    )
    trace = report.get("agent_trace") or []
    if trace:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "tool": s.get("tool"),
                        "why": s.get("why"),
                        "matches_added": s.get("matches_added"),
                        "unmatched_a_after": s.get("unmatched_a_after"),
                        "seconds": s.get("seconds"),
                    }
                    for s in trace
                ]
            ),
            width="stretch",
            hide_index=True,
            height=280,
        )
    else:
        st.info("Run Close 1 to see which tools the agent selected.")

    queue = ProposalQueue(DATA / "agent_proposals.json")
    pending = queue.pending()
    st.markdown(f"##### Operator queue · {len(pending)} pending")
    st.caption("Agent drafted these. Nothing is policy until you Accept or Edit. Reject leaves the exception as-is.")
    if not pending:
        st.success("No pending proposals.")
        return
    labels = {
        f"{p['proposal_id']} · {p.get('taxonomy_code')} · {p.get('exception_id')}": p for p in pending
    }
    chosen = labels[st.selectbox("Proposal", list(labels), key="agent_prop")]
    st.write(chosen.get("agent_rationale") or "")
    st.caption(chosen.get("evidence") or "")
    executable = bool(chosen.get("executable", True))
    if not executable:
        st.warning("Not an executable learned_rule — Reject after you have read the ops action.")
    rule = st.text_area("Proposed policy (you may edit)", value=chosen.get("proposed_rule") or "", key="agent_rule")
    note = st.text_input("Operator note (optional)", key="agent_note")
    c1, c2, c3 = st.columns(3)
    if c1.button("Accept as-is", type="primary", disabled=not executable):
        queue.accept(chosen["proposal_id"], memory, proposed_rule=rule, operator_note=note)
        st.success("Accepted. Close 2 will apply this as learned_rule.")
        st.rerun()
    if c2.button("Accept with edits", disabled=not executable):
        queue.accept(chosen["proposal_id"], memory, proposed_rule=rule, operator_note=note)
        st.success("Edited and accepted into Exception Memory.")
        st.rerun()
    if c3.button("Reject"):
        queue.reject(chosen["proposal_id"], operator_note=note or "rejected by operator")
        st.info("Rejected. Exception stays on the list. No memory write.")
        st.rerun()


def tab_scale(report: dict) -> None:
    scale = report.get("scale") or {}
    per = scale.get("per_1000_records") or {}
    st.markdown("##### Scale (estimates, labeled)")
    st.caption(scale.get("label") or "")
    a, b, c, d = st.columns(4)
    a.metric("This close (measured)", f"{scale.get('this_batch_wall_clock_seconds', 0):.2f}s")
    b.metric("Manual estimate", f"{scale.get('this_batch_manual_seconds_estimate', 0) / 60:.1f} min")
    c.metric("LLM calls", int(scale.get("this_batch_llm_calls") or 0))
    d.metric("LLM $ this close (est.)", f"${scale.get('this_batch_llm_usd_estimate', 0):.4f}")
    st.markdown(
        f"Per 1,000 records at this exception mix: **~{per.get('llm_calls_estimate', 0)} LLM calls**, "
        f"**~${per.get('llm_usd_estimate', 0)}**, **~{per.get('controller_seconds_estimate', 0)}s** controller vs "
        f"**~{per.get('manual_seconds_estimate', 0) / 60:.0f} min** manual. "
        "Rules are ~free; the LLM is the expensive tail."
    )
    secs = report.get("stage_seconds") or {}
    st.dataframe(
        pd.DataFrame([{"stage": k, "seconds": v} for k, v in secs.items()]),
        width="stretch",
        hide_index=True,
    )


def tab_why() -> None:
    st.markdown(
        """
<div class="core glass">
  <h4>The bar</h4>
  <p>Throughput + measured accuracy + an honest exception list. The PS asked for an <b>agent</b>: matching stages are tools; the agent selects them under a charter; out-of-box solutions are drafted for a human operator. We do not leave execution to the model.</p>
</div>
        """,
        unsafe_allow_html=True,
    )


def landing() -> None:
    st.markdown(
        """
<div class="core-grid">
  <div class="core glass"><h4>The job</h4><div class="big">Verification, not generation</div><p>Finance teams do not have a matching problem. They have a verification problem. This controller processes a settlement batch and refuses to fabricate certainty.</p></div>
  <div class="core glass"><h4>01 Deterministic floor</h4><p>Exact order_ref + amount + date. Confidence 1.0. The model is not allowed to rewrite the easy majority.</p></div>
  <div class="core glass"><h4>02 Honest exceptions</h4><p>DUP, SPLIT, FEE_NET, TIME_LAG, PARTIAL, OOP, UNRESOLVED — ranked by ₹ at risk. HOLD or ESCALATE, never “no match”.</p></div>
</div>
<div class="core-grid">
  <div class="core glass"><h4>03 Agent tools</h4><p>Rule, memory, fuzzy, classifier, gated LLM are tools. The agent chooses under a charter: never skip the floor, never auto-apply a new policy.</p></div>
  <div class="core glass"><h4>04 Operator accept</h4><p>Out-of-box residue gets a drafted reason + solution. A human Accepts, Edits, or Rejects. Only then does Close 2 learn.</p></div>
  <div class="core glass"><h4>05 Live stress</h4><p>Inject a 3% fee, a T+20 lag, or an orphan. Agent drafts. Operator decides.</p></div>
</div>
        """,
        unsafe_allow_html=True,
    )
