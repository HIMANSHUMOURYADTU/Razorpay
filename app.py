"""Razorpay Finance Controller — control room."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from components import views
from reports.report_generator import build_report
from src.exception_memory import ExceptionMemory
from src.pipeline import run_pipeline

REPO = Path(__file__).resolve().parent
DATA = REPO / "data"
load_dotenv(REPO / ".env")

st.set_page_config(
    page_title="Finance Controller",
    page_icon="⬡",
    layout="wide",
    initial_sidebar_state="expanded",
)

views.inject_theme()


def _memory() -> ExceptionMemory:
    return ExceptionMemory(DATA / "exception_memory.json")


def _provider_ready() -> tuple[bool, str]:
    name = (os.getenv("LLM_PROVIDER") or "gemini").strip().lower()
    keys = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "ollama": None}
    env_name = keys.get(name)
    if name == "ollama":
        return True, name
    if env_name and os.getenv(env_name, "").strip():
        return True, name
    return False, name


def _batch_from_path(source_a: Path) -> int:
    name = source_a.name
    if "batch3" in name:
        return 3
    if "batch2" in name:
        return 2
    if "easy" in name:
        return 10
    if "adversarial" in name:
        return 11
    if "shifted" in name:
        return 12
    return 1


def _run(source_a: Path, source_b: Path, *, enable_llm: bool) -> dict:
    result = run_pipeline(
        source_a, source_b, audit_path=REPO / "audit_log.jsonl", memory=_memory(), enable_llm=enable_llm
    )
    batch = _batch_from_path(source_a)
    return {"result": result, "report": build_report(result, batch=batch), "source_a": str(source_a)}


def _append_stress(selected: list[dict]) -> tuple[Path, Path]:
    work = DATA / "_stress"
    work.mkdir(exist_ok=True)
    a = pd.read_csv(DATA / "source_a.csv")
    b = pd.read_csv(DATA / "source_b.csv")
    for sc in selected:
        a = pd.concat([a, pd.DataFrame([sc["source_a"]])], ignore_index=True)
        if sc.get("source_b"):
            b = pd.concat([b, pd.DataFrame([sc["source_b"]])], ignore_index=True)
    path_a, path_b = work / "source_a.csv", work / "source_b.csv"
    a.to_csv(path_a, index=False)
    b.to_csv(path_b, index=False)
    return path_a, path_b


ready, provider = _provider_ready()

for key in ("batch1", "batch2", "batch3", "eval_pack"):
    if key not in st.session_state:
        st.session_state[key] = None

active = st.session_state.batch1
batch_label = None
if st.session_state.batch3:
    batch_label = "CLOSE 03 · FULL"
elif st.session_state.batch2:
    batch_label = "CLOSE 02 · FULL"
elif active:
    batch_label = "CLOSE 01 · FULL"

views.header(ready=ready, provider=provider, batch_label=batch_label)

with st.sidebar:
    st.markdown("#### Run controller")
    st.caption("Matching policy never changes when you swap LLM_PROVIDER.")
    enable_llm = st.toggle("Confidence-gated LLM assist", value=ready)
    if enable_llm and ready:
        st.success(f"{provider} · floor 0.75 · JSON only")
    elif enable_llm:
        st.warning(f"{provider} key missing — run will skip LLM with a stated reason.")
    st.divider()
    st.markdown("##### Demo closes (same n, new IDs)")
    run1 = st.button("▶ Close 1", type="primary", width="stretch")
    run2 = st.button("Close 2", width="stretch", disabled=st.session_state.batch1 is None)
    run3 = st.button("Close 3", width="stretch", disabled=st.session_state.batch2 is None)
    st.caption("Label CloudStack 2% + 14-day lag after close 1. Add Nimbus 2.36% before close 3.")
    st.divider()
    st.markdown("##### Live stress")
    scenarios = json.loads((DATA / "stress_scenarios.json").read_text(encoding="utf-8"))["scenarios"]
    titles = {s["title"]: s for s in scenarios}
    picked = st.multiselect("Inject rows", list(titles), default=[list(titles)[0]])
    run_stress = st.button("Stress this close", width="stretch", disabled=not picked)
    for sc in scenarios:
        st.caption(f"{sc['title']}: {sc['expect']}")
    st.divider()
    st.markdown("##### Eval packs")
    eval_choice = st.selectbox("Dataset", ["easy", "adversarial", "shifted"])
    run_eval = st.button("Run eval pack", width="stretch")
    st.divider()
    st.markdown("##### Your files")
    uploaded_a = st.file_uploader("Settlement export (A)", type="csv")
    uploaded_b = st.file_uploader("Internal ledger (B)", type="csv")
    run_uploaded = st.button("Reconcile upload", width="stretch")

if run1:
    with st.spinner("Ingesting → reconciling → classifying → gated AI → exceptions…"):
        st.session_state.batch1 = _run(DATA / "source_a.csv", DATA / "source_b.csv", enable_llm=enable_llm)
        st.session_state.batch2 = None
        st.session_state.batch3 = None
        st.rerun()

if run2 and st.session_state.batch1:
    with st.spinner("Close 2 — learned policies fire before fuzzy/LLM…"):
        st.session_state.batch2 = _run(
            DATA / "batch2_source_a.csv", DATA / "batch2_source_b.csv", enable_llm=enable_llm
        )
        st.rerun()

if run3 and st.session_state.batch2:
    with st.spinner("Close 3 — more memory, less residue…"):
        st.session_state.batch3 = _run(
            DATA / "batch3_source_a.csv", DATA / "batch3_source_b.csv", enable_llm=enable_llm
        )
        st.rerun()

if run_stress:
    selected = [titles[t] for t in picked]
    path_a, path_b = _append_stress(selected)
    with st.spinner("Stressing the controller on batch 1 + injected rows…"):
        st.session_state.batch1 = _run(path_a, path_b, enable_llm=enable_llm)
        st.rerun()

if run_eval:
    with st.spinner(f"Running {eval_choice} pack…"):
        st.session_state.eval_pack = _run(
            DATA / "eval" / f"{eval_choice}_a.csv",
            DATA / "eval" / f"{eval_choice}_b.csv",
            enable_llm=enable_llm,
        )
        st.rerun()

if run_uploaded:
    if not uploaded_a or not uploaded_b:
        st.error("Upload both CSVs first.")
    else:
        tmp = DATA / "_upload"
        tmp.mkdir(exist_ok=True)
        path_a, path_b = tmp / "source_a.csv", tmp / "source_b.csv"
        path_a.write_bytes(uploaded_a.getvalue())
        path_b.write_bytes(uploaded_b.getvalue())
        with st.spinner("Reconciling uploaded files…"):
            st.session_state.batch1 = _run(path_a, path_b, enable_llm=enable_llm)
            st.session_state.batch2 = None
            st.session_state.batch3 = None
            st.rerun()

batch = st.session_state.batch1
views.system_health(batch["report"] if batch else None, ready, provider)

if not batch:
    views.landing()
    st.stop()

report, result = batch["report"], batch["result"]
views.kpis(report)
views.pipeline_strip(report)
views.controller_core(report)

if report.get("llm_skipped_reason"):
    st.info(f"LLM assist skipped — {report['llm_skipped_reason']}")
elif report.get("llm_provider"):
    via = report.get("llm_matches_by_provider") or {}
    st.caption(f"LLM this run: **{report['llm_provider']}** · accepted matches: {via or 'none'}")

ev = st.session_state.eval_pack
if ev:
    er = ev["report"]
    st.caption(
        f"Last eval pack: {er.get('match_rate_a_label')} · precision {er.get('precision_label')} · "
        f"exceptions {er.get('exception_count_a')}"
    )

tabs = st.tabs(
    [
        "Command",
        "Exceptions",
        "Agent",
        "Matches",
        "Memory",
        "Audit",
        "Learning",
        "Scale",
        "Why it wins",
    ]
)
with tabs[0]:
    views.tab_overview(report, result)
with tabs[1]:
    views.tab_exceptions(result, report, _memory())
with tabs[2]:
    views.tab_agent(report, _memory())
with tabs[3]:
    views.tab_matches(result)
with tabs[4]:
    views.tab_memory(_memory())
with tabs[5]:
    views.tab_audit()
with tabs[6]:
    views.tab_learning(st.session_state.batch1, st.session_state.batch2, st.session_state.batch3)
with tabs[7]:
    views.tab_scale(report)
    if st.session_state.eval_pack and st.session_state.eval_pack is not batch:
        ev = st.session_state.eval_pack["report"]
        st.markdown("Last eval pack: " + ev.get("match_rate_a_label", ""))
    ev = DATA / "eval"
    if (ev / "easy_a.csv").exists():
        st.caption("Eval files: data/eval/easy · adversarial · shifted (run from the sidebar).")
with tabs[8]:
    views.tab_why()
