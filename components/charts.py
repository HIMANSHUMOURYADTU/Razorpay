"""Shared dashboard helpers. Numbers always come from a pipeline report."""

from __future__ import annotations

import html
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"

STAGE_ORDER = ("rule", "learned_rule", "fuzzy", "classifier", "llm_assisted")
STAGE_LABEL = {
    "rule": "Rule",
    "learned_rule": "Memory",
    "fuzzy": "Fuzzy",
    "classifier": "Classifier",
    "llm_assisted": "LLM",
}
STAGE_HINT = {
    "rule": "Exact ref · ±₹0.01 · ≤3d · conf 1.00",
    "learned_rule": "Human-validated policy",
    "fuzzy": "Tolerance bands · floor 0.70",
    "classifier": "SPLIT sum / taxonomy",
    "llm_assisted": "Gated JSON · floor 0.75",
}
TAX_COLOR = {
    "DUP": "#F5C16C",
    "SPLIT": "#C4B5FD",
    "FX_ROUND": "#67E8F9",
    "FEE_NET": "#3395FF",
    "TIME_LAG": "#FB923C",
    "PARTIAL": "#F9A8D4",
    "OOP": "#A5B4FC",
    "UNRESOLVED": "#FB7185",
}

PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#C9D7EA", family="IBM Plex Sans, sans-serif", size=12),
    margin=dict(l=8, r=8, t=28, b=8),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
)


def inr(value: float | int) -> str:
    return f"₹{value:,.0f}"


def tax_badge(code: str) -> str:
    color = TAX_COLOR.get(code, "#94A3B8")
    safe = html.escape(code)
    return f'<span class="tax" style="background:{color}22;color:{color};border:1px solid {color}55">{safe}</span>'


def sev_badge(level: str) -> str:
    safe = html.escape((level or "medium").upper())
    return f'<span class="sev sev-{html.escape(level or "medium")}">{safe}</span>'


def load_theme() -> str:
    return (REPO / "styles" / "theme.css").read_text(encoding="utf-8")


def sankey_figure(report: dict) -> go.Figure:
    by_stage = report.get("a_records_by_stage") or {}
    human = int(report.get("exception_count_a") or 0)
    nodes = ["Settlements"]
    colors = ["#2E96FF"]
    sources, targets, values = [], [], []
    for stage in STAGE_ORDER:
        n = int(by_stage.get(stage, 0))
        if n <= 0:
            continue
        nodes.append(STAGE_LABEL[stage])
        colors.append("#10B981" if stage != "llm_assisted" else "#67E8F9")
        sources.append(0)
        targets.append(len(nodes) - 1)
        values.append(n)
    if human:
        nodes.append("Human review")
        colors.append("#FB7185")
        sources.append(0)
        targets.append(len(nodes) - 1)
        values.append(human)
    fig = go.Figure(
        data=[
            go.Sankey(
                arrangement="snap",
                node=dict(
                    label=nodes,
                    color=colors,
                    pad=18,
                    thickness=16,
                    line=dict(color="rgba(255,255,255,0.15)", width=0.5),
                ),
                link=dict(
                    source=sources,
                    target=targets,
                    value=values,
                    color="rgba(46,150,255,0.22)",
                ),
            )
        ]
    )
    fig.update_layout(**PLOTLY_LAYOUT, height=280, title="What happened to every settlement")
    return fig


def learning_curve(batches: list[tuple[str, dict]]) -> go.Figure:
    """Match rate + stage attribution across closes. n is in the hover."""
    fig = go.Figure()
    x = [label for label, _ in batches]
    rates = [r["match_rate_a"] * 100 for _, r in batches]
    ns = [f"{r['matched_a']}/{r['source_a_count']}" for _, r in batches]
    fig.add_trace(
        go.Scatter(
            x=x,
            y=rates,
            mode="lines+markers",
            name="Match rate %",
            line=dict(color="#2E96FF", width=3),
            marker=dict(size=10),
            customdata=ns,
            hovertemplate="%{x}: %{y:.1f}% (%{customdata})<extra></extra>",
        )
    )
    for stage, color in (
        ("rule", "#4B6280"),
        ("learned_rule", "#10B981"),
        ("fuzzy", "#67E8F9"),
        ("classifier", "#C4B5FD"),
        ("llm_assisted", "#F5C16C"),
    ):
        fig.add_trace(
            go.Bar(
                x=x,
                y=[int((r.get("a_records_by_stage") or {}).get(stage, 0)) for _, r in batches],
                name=STAGE_LABEL[stage],
                marker_color=color,
                yaxis="y2",
                opacity=0.85,
            )
        )
    fig.update_layout(
        **PLOTLY_LAYOUT,
        height=340,
        barmode="stack",
        yaxis=dict(title="Match rate %", gridcolor="#1A2740", range=[0, 100]),
        yaxis2=dict(title="A-rows by stage", overlaying="y", side="right", gridcolor="#1A2740"),
        title="Controller learning — same-n closes (not a 18-row slice)",
    )
    return fig


def exposure_bars(report: dict) -> go.Figure:
    mix = report.get("exposure_by_taxonomy") or {}
    if not mix:
        fig = go.Figure()
        fig.update_layout(**PLOTLY_LAYOUT, height=200, title="No A-side exposure")
        return fig
    codes = list(mix.keys())
    amounts = [mix[c]["amount_inr"] for c in codes]
    fig = go.Figure(
        go.Bar(
            x=codes,
            y=amounts,
            marker_color=[TAX_COLOR.get(c, "#94A3B8") for c in codes],
            hovertemplate="%{x}: ₹%{y:,.0f}<extra></extra>",
        )
    )
    fig.update_layout(**PLOTLY_LAYOUT, height=280, title="₹ at risk by taxonomy")
    fig.update_xaxes(tickangle=-18)
    fig.update_yaxes(gridcolor="#1A2740")
    return fig


def matches_frame(result) -> pd.DataFrame:
    rows = []
    for m in result.matches:
        rows.append(
            {
                "stage": m.stage,
                "conf": m.confidence,
                "order_ref": m.order_ref,
                "txn": ",".join(m.txn_ids),
                "ledger": ",".join(m.ledger_ids),
                "taxonomy": m.taxonomy_code or "",
                "provider": m.provider or "",
                "reason": m.reason,
            }
        )
    return pd.DataFrame(rows)
