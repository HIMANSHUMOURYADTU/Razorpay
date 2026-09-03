"""
Synthetic data for the Finance Controller agent.

Hidden ground truth is ONLY for our scoring. The matcher must never read it.

Batches 1, 2, and 3 are full-size closes (same trap mix, new IDs) so match-rate
deltas are fair comparisons — not an 18-row slice next to an 88-row close.

Eval packs: easy / adversarial / shifted under data/eval/.

Run from repo root:
    python data/generate_synthetic_data.py
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

SEED = 42
OUTPUT_DIR = Path(__file__).resolve().parent

# Official columns the matcher is allowed to see.
SOURCE_A_COLUMNS = [
    "txn_id",
    "order_ref",
    "amount",
    "settlement_date",
    "description",
    "currency",
]
SOURCE_B_COLUMNS = [
    "ledger_id",
    "order_ref",
    "amount",
    "posting_date",
    "description",
    "currency",
]

# Razorpay's common domestic fee: 2% + 18% GST = 2.36%.
FEE_2PCT = Decimal("0.02")
FEE_2_36PCT = Decimal("0.0236")

MERCHANTS = [
    "Urban Grocery",
    "Leaf & Bean Cafe",
    "Orbit Mobility",
    "MediQuick Pharmacy",
    "BrightPath Tuition",
    "SilkRoute Exports",
    "PixelForge Studio",
    "Harbour Linens",
    "Kite Kids Wear",
    "Summit Fitness",
    "Amber Books",
    "Cedar Homewares",
    "Volt Auto Parts",
    "Maple Dental",
    "Indigo Travel",
]

# Vendors reserved for FEE_NET so Exception Memory can learn a vendor rule.
FEE_VENDOR_2PCT = "CloudStack SaaS"
FEE_VENDOR_236 = "Nimbus Hosting"


def money(value: Decimal | float | int | str) -> str:
    """Format INR amounts to two decimal places (paise)."""
    return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


def dec(value: Decimal | float | int | str) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


@dataclass
class SourceARow:
    txn_id: str
    order_ref: str
    amount: str
    settlement_date: str
    description: str
    currency: str = "INR"


@dataclass
class SourceBRow:
    ledger_id: str
    order_ref: str
    amount: str
    posting_date: str
    description: str
    currency: str = "INR"


@dataclass
class GroundTruthRow:
    batch: int
    gt_group: str
    taxonomy: str
    source_a_ids: str
    source_b_ids: str
    expected_behavior: str
    injection_notes: str


@dataclass
class Batch:
    name: str
    batch_id: int
    source_a: list[SourceARow] = field(default_factory=list)
    source_b: list[SourceBRow] = field(default_factory=list)
    ground_truth: list[GroundTruthRow] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)

    def add_gt(self, row: GroundTruthRow) -> None:
        self.ground_truth.append(row)
        self.counters[row.taxonomy] = self.counters.get(row.taxonomy, 0) + 1


class IdFactory:
    """Readable demo IDs: pay_A00001 / order_00001 / led_B00001."""

    def __init__(self, prefix: str = "") -> None:
        self.txn_n = 0
        self.led_n = 0
        self.ord_n = 0
        self.group_n = 0
        self.prefix = prefix

    def txn(self) -> str:
        self.txn_n += 1
        return f"pay_{self.prefix}A{self.txn_n:05d}"

    def ledger(self) -> str:
        self.led_n += 1
        return f"led_{self.prefix}B{self.led_n:05d}"

    def order(self) -> str:
        self.ord_n += 1
        return f"order_{self.prefix}{self.ord_n:05d}"

    def group(self) -> str:
        self.group_n += 1
        return f"GT-{self.prefix}{self.group_n:04d}"


def settlement_desc(merchant: str, order_ref: str, extra: str = "") -> str:
    suffix = f" | {extra}" if extra else ""
    return f"Razorpay settlement | {merchant} | {order_ref}{suffix}"


def ledger_desc(merchant: str, order_ref: str, extra: str = "") -> str:
    suffix = f" - {extra}" if extra else ""
    return f"Ledger posting - {merchant} | {order_ref}{suffix}"


def random_amount(rng: random.Random, lo: int = 250, hi: int = 48000) -> Decimal:
    rupees = rng.randint(lo, hi)
    paise = rng.choice([0, 0, 0, 25, 50, 75, 99])  # mostly round, some paise
    return dec(f"{rupees}.{paise:02d}")


def random_posting_date(rng: random.Random, start: date, end: date) -> date:
    span = (end - start).days
    return start + timedelta(days=rng.randint(0, span))


def add_clean_pair(
    batch: Batch,
    ids: IdFactory,
    rng: random.Random,
    merchant: str,
    amount: Decimal,
    posting: date,
    lag_days: int,
) -> None:
    order_ref = ids.order()
    txn_id = ids.txn()
    ledger_id = ids.ledger()
    group = ids.group()
    settlement = posting + timedelta(days=lag_days)

    batch.source_a.append(
        SourceARow(
            txn_id=txn_id,
            order_ref=order_ref,
            amount=money(amount),
            settlement_date=settlement.isoformat(),
            description=settlement_desc(merchant, order_ref),
        )
    )
    batch.source_b.append(
        SourceBRow(
            ledger_id=ledger_id,
            order_ref=order_ref,
            amount=money(amount),
            posting_date=posting.isoformat(),
            description=ledger_desc(merchant, order_ref),
        )
    )
    batch.add_gt(
        GroundTruthRow(
            batch=batch.batch_id,
            gt_group=group,
            taxonomy="CLEAN",
            source_a_ids=txn_id,
            source_b_ids=ledger_id,
            expected_behavior="Stage 1 exact match on order_ref + amount + date window.",
            injection_notes=f"identical amount {money(amount)}; settlement lag {lag_days}d",
        )
    )


def inject_clean_matches(
    batch: Batch,
    ids: IdFactory,
    rng: random.Random,
    n: int,
    period_start: date,
    period_end: date,
) -> None:
    for _ in range(n):
        merchant = rng.choice(MERCHANTS)
        amount = random_amount(rng)
        posting = random_posting_date(rng, period_start, period_end)
        lag = rng.choice([0, 0, 1, 1, 2])  # well inside a 2-3 day window
        add_clean_pair(batch, ids, rng, merchant, amount, posting, lag)


def inject_duplicates(
    batch: Batch,
    ids: IdFactory,
    rng: random.Random,
    n: int,
    period_start: date,
    period_end: date,
) -> None:
    """n orders each get TWO identical settlement rows and ONE ledger row."""
    for _ in range(n):
        merchant = rng.choice(MERCHANTS)
        amount = random_amount(rng, 800, 12000)
        posting = random_posting_date(rng, period_start, period_end)
        settlement = posting + timedelta(days=1)
        order_ref = ids.order()
        txn_1, txn_2 = ids.txn(), ids.txn()
        ledger_id = ids.ledger()
        group = ids.group()

        for txn_id in (txn_1, txn_2):
            batch.source_a.append(
                SourceARow(
                    txn_id=txn_id,
                    order_ref=order_ref,
                    amount=money(amount),
                    settlement_date=settlement.isoformat(),
                    description=settlement_desc(merchant, order_ref, "duplicate capture"),
                )
            )
        batch.source_b.append(
            SourceBRow(
                ledger_id=ledger_id,
                order_ref=order_ref,
                amount=money(amount),
                posting_date=posting.isoformat(),
                description=ledger_desc(merchant, order_ref),
            )
        )
        batch.add_gt(
            GroundTruthRow(
                batch=batch.batch_id,
                gt_group=group,
                taxonomy="DUP",
                source_a_ids=f"{txn_1},{txn_2}",
                source_b_ids=ledger_id,
                expected_behavior=(
                    "One A row can match B; the second A row is a duplicate settlement "
                    "and must be flagged DUP, not force-matched."
                ),
                injection_notes=(
                    f"two identical settlements of {money(amount)} for {order_ref}; "
                    "ledger has a single row"
                ),
            )
        )


def inject_splits(
    batch: Batch,
    ids: IdFactory,
    rng: random.Random,
    n: int,
    period_start: date,
    period_end: date,
) -> None:
    """n orders: two settlement rows that SUM to one ledger row."""
    for i in range(n):
        merchant = rng.choice(MERCHANTS)
        total = random_amount(rng, 2000, 20000)
        # Vary the split ratio so we are not always 50/50.
        ratio = [Decimal("0.40"), Decimal("0.50"), Decimal("0.65")][i % 3]
        part_1 = dec(total * ratio)
        part_2 = dec(total - part_1)
        posting = random_posting_date(rng, period_start, period_end)
        settlement = posting + timedelta(days=1)
        order_ref = ids.order()
        txn_1, txn_2 = ids.txn(), ids.txn()
        ledger_id = ids.ledger()
        group = ids.group()

        batch.source_a.append(
            SourceARow(
                txn_id=txn_1,
                order_ref=order_ref,
                amount=money(part_1),
                settlement_date=settlement.isoformat(),
                description=settlement_desc(merchant, order_ref, f"split 1/2 {money(part_1)}"),
            )
        )
        batch.source_a.append(
            SourceARow(
                txn_id=txn_2,
                order_ref=order_ref,
                amount=money(part_2),
                settlement_date=(settlement + timedelta(days=1)).isoformat(),
                description=settlement_desc(merchant, order_ref, f"split 2/2 {money(part_2)}"),
            )
        )
        batch.source_b.append(
            SourceBRow(
                ledger_id=ledger_id,
                order_ref=order_ref,
                amount=money(total),
                posting_date=posting.isoformat(),
                description=ledger_desc(merchant, order_ref, f"full order {money(total)}"),
            )
        )
        batch.add_gt(
            GroundTruthRow(
                batch=batch.batch_id,
                gt_group=group,
                taxonomy="SPLIT",
                source_a_ids=f"{txn_1},{txn_2}",
                source_b_ids=ledger_id,
                expected_behavior="Sum-match the two A rows to the single B row; do not 1:1 match.",
                injection_notes=(
                    f"{money(part_1)} + {money(part_2)} = {money(total)} on {order_ref}"
                ),
            )
        )


def inject_rounding(
    batch: Batch,
    ids: IdFactory,
    rng: random.Random,
    n: int,
    period_start: date,
    period_end: date,
) -> None:
    """Paise-level drift between settlement and ledger amounts."""
    drifts = [Decimal("0.01"), Decimal("-0.01"), Decimal("0.02"), Decimal("-0.03"), Decimal("0.05"), Decimal("-0.04")]
    for i in range(n):
        merchant = rng.choice(MERCHANTS)
        gross = random_amount(rng, 400, 15000)
        drift = drifts[i % len(drifts)]
        net_a = dec(gross + drift)
        posting = random_posting_date(rng, period_start, period_end)
        settlement = posting + timedelta(days=rng.choice([0, 1]))
        order_ref = ids.order()
        txn_id = ids.txn()
        ledger_id = ids.ledger()
        group = ids.group()

        batch.source_a.append(
            SourceARow(
                txn_id=txn_id,
                order_ref=order_ref,
                amount=money(net_a),
                settlement_date=settlement.isoformat(),
                description=settlement_desc(merchant, order_ref),
            )
        )
        batch.source_b.append(
            SourceBRow(
                ledger_id=ledger_id,
                order_ref=order_ref,
                amount=money(gross),
                posting_date=posting.isoformat(),
                description=ledger_desc(merchant, order_ref),
            )
        )
        batch.add_gt(
            GroundTruthRow(
                batch=batch.batch_id,
                gt_group=group,
                taxonomy="FX_ROUND",
                source_a_ids=txn_id,
                source_b_ids=ledger_id,
                expected_behavior="Fuzzy/tolerant match within paise band; log the amount delta.",
                injection_notes=(
                    f"A={money(net_a)} B={money(gross)} delta={money(drift)} (paise-level)"
                ),
            )
        )


def inject_fee_net(
    batch: Batch,
    ids: IdFactory,
    rng: random.Random,
    specs: list[tuple[str, Decimal]],
    period_start: date,
    period_end: date,
) -> None:
    """A is net of platform fee; B is gross. Vendor names are stable for learning."""
    for merchant, fee_rate in specs:
        gross = random_amount(rng, 1500, 25000)
        fee = dec(gross * fee_rate)
        net_a = dec(gross - fee)
        posting = random_posting_date(rng, period_start, period_end)
        settlement = posting + timedelta(days=1)
        order_ref = ids.order()
        txn_id = ids.txn()
        ledger_id = ids.ledger()
        group = ids.group()
        pct = (fee_rate * 100).quantize(Decimal("0.01"))

        batch.source_a.append(
            SourceARow(
                txn_id=txn_id,
                order_ref=order_ref,
                amount=money(net_a),
                settlement_date=settlement.isoformat(),
                description=settlement_desc(merchant, order_ref, f"net of {pct}% fee"),
            )
        )
        batch.source_b.append(
            SourceBRow(
                ledger_id=ledger_id,
                order_ref=order_ref,
                amount=money(gross),
                posting_date=posting.isoformat(),
                description=ledger_desc(merchant, order_ref, "gross"),
            )
        )
        batch.add_gt(
            GroundTruthRow(
                batch=batch.batch_id,
                gt_group=group,
                taxonomy="FEE_NET",
                source_a_ids=txn_id,
                source_b_ids=ledger_id,
                expected_behavior=(
                    f"Reconstruct gross from A using {pct}% fee and match. "
                    f"Learnable rule: '{merchant} settlements are net of {pct}% fee'."
                ),
                injection_notes=(
                    f"vendor={merchant}; fee={pct}%; A_net={money(net_a)} "
                    f"B_gross={money(gross)} fee_amt={money(fee)}"
                ),
            )
        )


def inject_time_lag(
    batch: Batch,
    ids: IdFactory,
    rng: random.Random,
    n: int,
    period_start: date,
    period_end: date,
    lag_days: int = 9,
) -> None:
    """Same amount + order_ref, but settlement is just outside a normal 2-3d window."""
    for i in range(n):
        merchant = rng.choice(MERCHANTS)
        amount = random_amount(rng, 600, 9000)
        posting = random_posting_date(rng, period_start, period_end - timedelta(days=lag_days + 1))
        lag = lag_days + (i % 2)  # 9 or 10 days
        settlement = posting + timedelta(days=lag)
        order_ref = ids.order()
        txn_id = ids.txn()
        ledger_id = ids.ledger()
        group = ids.group()

        batch.source_a.append(
            SourceARow(
                txn_id=txn_id,
                order_ref=order_ref,
                amount=money(amount),
                settlement_date=settlement.isoformat(),
                description=settlement_desc(merchant, order_ref, f"settled T+{lag}"),
            )
        )
        batch.source_b.append(
            SourceBRow(
                ledger_id=ledger_id,
                order_ref=order_ref,
                amount=money(amount),
                posting_date=posting.isoformat(),
                description=ledger_desc(merchant, order_ref),
            )
        )
        batch.add_gt(
            GroundTruthRow(
                batch=batch.batch_id,
                gt_group=group,
                taxonomy="TIME_LAG",
                source_a_ids=txn_id,
                source_b_ids=ledger_id,
                expected_behavior="Do not Stage-1 match; classify TIME_LAG and widen window once.",
                injection_notes=f"identical amount {money(amount)}; settlement lag {lag}d (outside 2-3d window)",
            )
        )


def inject_partial_refunds(
    batch: Batch,
    ids: IdFactory,
    rng: random.Random,
    n: int,
    period_start: date,
    period_end: date,
) -> None:
    """B holds the original gross; A holds net-of-refund."""
    for _ in range(n):
        merchant = rng.choice(MERCHANTS)
        original = random_amount(rng, 1200, 18000)
        refund_ratio = rng.choice([Decimal("0.20"), Decimal("0.25"), Decimal("0.40")])
        refund = dec(original * refund_ratio)
        net_a = dec(original - refund)
        posting = random_posting_date(rng, period_start, period_end)
        settlement = posting + timedelta(days=2)
        order_ref = ids.order()
        txn_id = ids.txn()
        ledger_id = ids.ledger()
        group = ids.group()

        batch.source_a.append(
            SourceARow(
                txn_id=txn_id,
                order_ref=order_ref,
                amount=money(net_a),
                settlement_date=settlement.isoformat(),
                description=settlement_desc(merchant, order_ref, f"net of refund {money(refund)}"),
            )
        )
        batch.source_b.append(
            SourceBRow(
                ledger_id=ledger_id,
                order_ref=order_ref,
                amount=money(original),
                posting_date=posting.isoformat(),
                description=ledger_desc(merchant, order_ref, f"original {money(original)}"),
            )
        )
        batch.add_gt(
            GroundTruthRow(
                batch=batch.batch_id,
                gt_group=group,
                taxonomy="PARTIAL",
                source_a_ids=txn_id,
                source_b_ids=ledger_id,
                expected_behavior="Match against adjusted (net-of-refund) amount; do not treat as full match.",
                injection_notes=(
                    f"original={money(original)} refund={money(refund)} "
                    f"A_net={money(net_a)} ({int(refund_ratio * 100)}% refunded)"
                ),
            )
        )


def inject_oop(
    batch: Batch,
    ids: IdFactory,
    rng: random.Random,
    n: int,
) -> None:
    """Posted in July (prior period), settled in August — out-of-period posting."""
    for i in range(n):
        merchant = rng.choice(MERCHANTS)
        amount = random_amount(rng, 900, 8000)
        posting = date(2026, 7, 28 + i)  # 28 or 29 July
        settlement = date(2026, 8, 2 + i)  # early August
        order_ref = ids.order()
        txn_id = ids.txn()
        ledger_id = ids.ledger()
        group = ids.group()

        batch.source_a.append(
            SourceARow(
                txn_id=txn_id,
                order_ref=order_ref,
                amount=money(amount),
                settlement_date=settlement.isoformat(),
                description=settlement_desc(merchant, order_ref, "Aug cycle"),
            )
        )
        batch.source_b.append(
            SourceBRow(
                ledger_id=ledger_id,
                order_ref=order_ref,
                amount=money(amount),
                posting_date=posting.isoformat(),
                description=ledger_desc(merchant, order_ref, "July books"),
            )
        )
        batch.add_gt(
            GroundTruthRow(
                batch=batch.batch_id,
                gt_group=group,
                taxonomy="OOP",
                source_a_ids=txn_id,
                source_b_ids=ledger_id,
                expected_behavior="Flag out-of-period: ledger in July, settlement in August cycle.",
                injection_notes=(
                    f"identical amount {money(amount)}; posting={posting.isoformat()} "
                    f"settlement={settlement.isoformat()} (adjacent months)"
                ),
            )
        )


def inject_unresolved(
    batch: Batch,
    ids: IdFactory,
    rng: random.Random,
    period_start: date,
    period_end: date,
) -> None:
    """One orphan settlement (no ledger) and one orphan ledger (no settlement)."""
    # Orphan A — settled on Razorpay, never posted internally.
    merchant_a = rng.choice(MERCHANTS)
    amount_a = random_amount(rng, 300, 4000)
    order_a = ids.order()
    txn_id = ids.txn()
    group_a = ids.group()
    posting_like = random_posting_date(rng, period_start, period_end)

    batch.source_a.append(
        SourceARow(
            txn_id=txn_id,
            order_ref=order_a,
            amount=money(amount_a),
            settlement_date=(posting_like + timedelta(days=1)).isoformat(),
            description=settlement_desc(merchant_a, order_a, "NO LEDGER COUNTERPART"),
        )
    )
    batch.add_gt(
        GroundTruthRow(
            batch=batch.batch_id,
            gt_group=group_a,
            taxonomy="UNRESOLVED",
            source_a_ids=txn_id,
            source_b_ids="",
            expected_behavior="Stay UNRESOLVED. No plausible candidate. Do not force-match.",
            injection_notes=f"orphan settlement {txn_id} {money(amount_a)} — no source_b row exists",
        )
    )

    # Orphan B — internal journal / other-rail posting, no Razorpay settlement.
    merchant_b = rng.choice(MERCHANTS)
    amount_b = random_amount(rng, 300, 4000)
    order_b = ids.order()
    ledger_id = ids.ledger()
    group_b = ids.group()
    posting_b = random_posting_date(rng, period_start, period_end)

    batch.source_b.append(
        SourceBRow(
            ledger_id=ledger_id,
            order_ref=order_b,
            amount=money(amount_b),
            posting_date=posting_b.isoformat(),
            description=ledger_desc(merchant_b, order_b, "manual journal / other rail"),
        )
    )
    batch.add_gt(
        GroundTruthRow(
            batch=batch.batch_id,
            gt_group=group_b,
            taxonomy="UNRESOLVED",
            source_a_ids="",
            source_b_ids=ledger_id,
            expected_behavior="Stay UNRESOLVED. No plausible candidate. Do not force-match.",
            injection_notes=f"orphan ledger {ledger_id} {money(amount_b)} — no source_a row exists",
        )
    )


def shuffle_rows(batch: Batch, rng: random.Random) -> None:
    """Don't leave injected cases clumped at the bottom of the CSV."""
    rng.shuffle(batch.source_a)
    rng.shuffle(batch.source_b)


def write_csv(path: Path, columns: list[str], rows: list) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: getattr(row, col) for col in columns})


def write_ground_truth(path: Path, rows: list[GroundTruthRow]) -> None:
    columns = [
        "batch",
        "gt_group",
        "taxonomy",
        "source_a_ids",
        "source_b_ids",
        "expected_behavior",
        "injection_notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: getattr(row, col) for col in columns})


def inject_full_close(
    batch: Batch,
    ids: IdFactory,
    rng: random.Random,
    period_start: date,
    period_end: date,
) -> None:
    """Same trap mix as the demo close — used so batch 2/3 are fair n=88 reruns, not 18-row slices."""
    inject_clean_matches(batch, ids, rng, n=55, period_start=period_start, period_end=period_end)
    inject_duplicates(batch, ids, rng, n=4, period_start=period_start, period_end=period_end)
    inject_splits(batch, ids, rng, n=3, period_start=period_start, period_end=period_end)
    inject_rounding(batch, ids, rng, n=6, period_start=period_start, period_end=period_end)
    inject_fee_net(
        batch,
        ids,
        rng,
        specs=[
            (FEE_VENDOR_2PCT, FEE_2PCT),
            (FEE_VENDOR_2PCT, FEE_2PCT),
            (FEE_VENDOR_236, FEE_2_36PCT),
            (FEE_VENDOR_236, FEE_2_36PCT),
        ],
        period_start=period_start,
        period_end=period_end,
    )
    inject_time_lag(batch, ids, rng, n=3, period_start=period_start, period_end=period_end, lag_days=9)
    inject_partial_refunds(batch, ids, rng, n=3, period_start=period_start, period_end=period_end)
    inject_oop(batch, ids, rng, n=2)
    inject_unresolved(batch, ids, rng, period_start=period_start, period_end=period_end)


def build_batch_1(rng: random.Random) -> Batch:
    batch = Batch(name="batch1", batch_id=1)
    ids = IdFactory(prefix="")
    inject_full_close(batch, ids, rng, date(2026, 8, 1), date(2026, 8, 28))
    shuffle_rows(batch, rng)
    return batch


def build_batch_2(rng: random.Random) -> Batch:
    """Full-size twin of batch 1 on new IDs — fair compounding comparison (same n, same mix)."""
    batch = Batch(name="batch2", batch_id=2)
    ids = IdFactory(prefix="2")
    inject_full_close(batch, ids, rng, date(2026, 9, 1), date(2026, 9, 28))
    shuffle_rows(batch, rng)
    return batch


def build_batch_3(rng: random.Random) -> Batch:
    """Third full close. More memory labels should push learned_rule toward saturation."""
    batch = Batch(name="batch3", batch_id=3)
    ids = IdFactory(prefix="3")
    inject_full_close(batch, ids, rng, date(2026, 10, 1), date(2026, 10, 28))
    shuffle_rows(batch, rng)
    return batch


def build_eval_easy(rng: random.Random) -> Batch:
    batch = Batch(name="eval_easy", batch_id=10)
    ids = IdFactory(prefix="E")
    inject_clean_matches(batch, ids, rng, n=40, period_start=date(2026, 8, 1), period_end=date(2026, 8, 20))
    shuffle_rows(batch, rng)
    return batch


def build_eval_adversarial(rng: random.Random) -> Batch:
    """Denser traps, fewer clean rows — stress the taxonomy, not the happy path."""
    batch = Batch(name="eval_adversarial", batch_id=11)
    ids = IdFactory(prefix="V")
    start, end = date(2026, 8, 1), date(2026, 8, 28)
    inject_clean_matches(batch, ids, rng, n=20, period_start=start, period_end=end)
    inject_duplicates(batch, ids, rng, n=6, period_start=start, period_end=end)
    inject_splits(batch, ids, rng, n=4, period_start=start, period_end=end)
    inject_rounding(batch, ids, rng, n=8, period_start=start, period_end=end)
    inject_fee_net(
        batch,
        ids,
        rng,
        specs=[
            (FEE_VENDOR_2PCT, FEE_2PCT),
            (FEE_VENDOR_2PCT, FEE_2PCT),
            (FEE_VENDOR_236, FEE_2_36PCT),
            (FEE_VENDOR_236, FEE_2_36PCT),
            (FEE_VENDOR_2PCT, FEE_2PCT),
        ],
        period_start=start,
        period_end=end,
    )
    inject_time_lag(batch, ids, rng, n=4, period_start=start, period_end=end, lag_days=9)
    inject_partial_refunds(batch, ids, rng, n=4, period_start=start, period_end=end)
    inject_oop(batch, ids, rng, n=3)
    inject_unresolved(batch, ids, rng, period_start=start, period_end=end)
    shuffle_rows(batch, rng)
    return batch


SHIFT_FEE_A = "Helios Cloud"
SHIFT_FEE_B = "Quark Pay"


def build_eval_shifted(rng: random.Random) -> Batch:
    """Same fee-net / lag shapes, unseen vendor strings — distribution shift."""
    batch = Batch(name="eval_shifted", batch_id=12)
    ids = IdFactory(prefix="S")
    start, end = date(2026, 11, 1), date(2026, 11, 20)
    inject_clean_matches(batch, ids, rng, n=30, period_start=start, period_end=end)
    inject_fee_net(
        batch,
        ids,
        rng,
        specs=[
            (SHIFT_FEE_A, FEE_2PCT),
            (SHIFT_FEE_A, FEE_2PCT),
            (SHIFT_FEE_B, FEE_2_36PCT),
            (SHIFT_FEE_B, FEE_2_36PCT),
        ],
        period_start=start,
        period_end=end,
    )
    inject_time_lag(batch, ids, rng, n=3, period_start=start, period_end=end, lag_days=9)
    inject_rounding(batch, ids, rng, n=4, period_start=start, period_end=end)
    shuffle_rows(batch, rng)
    return batch


def print_summary(batch: Batch) -> None:
    print(f"\n=== {batch.name.upper()} ===")
    print(f"  source_a rows : {len(batch.source_a)}")
    print(f"  source_b rows : {len(batch.source_b)}")
    print(f"  gt groups     : {len(batch.ground_truth)}")
    print("  taxonomy counts (groups, not rows):")
    for code in [
        "CLEAN",
        "DUP",
        "SPLIT",
        "FX_ROUND",
        "FEE_NET",
        "TIME_LAG",
        "PARTIAL",
        "OOP",
        "UNRESOLVED",
    ]:
        n = batch.counters.get(code, 0)
        if n:
            print(f"    {code:<12} {n}")


def write_injection_summary(path: Path, batches: list[Batch]) -> None:
    """Human-readable review file listing every injected edge case."""
    lines = [
        "FINANCE CONTROLLER — SYNTHETIC DATA INJECTION SUMMARY",
        "Hidden labels live in data/ground_truth.csv. Matcher must ignore that file.",
        "Batches 1–3 are full-size closes (same mix, new IDs). Eval sets are separate.",
        "",
    ]
    for batch in batches:
        lines.append(f"{'=' * 72}")
        lines.append(f"{batch.name.upper()}  |  A={len(batch.source_a)} rows  B={len(batch.source_b)} rows")
        lines.append(f"{'=' * 72}")
        for row in batch.ground_truth:
            if row.taxonomy == "CLEAN":
                continue
            lines.append(f"[{row.taxonomy}] {row.gt_group}")
            lines.append(f"  A: {row.source_a_ids or '(none)'}")
            lines.append(f"  B: {row.source_b_ids or '(none)'}")
            lines.append(f"  notes : {row.injection_notes}")
            lines.append(f"  expect: {row.expected_behavior}")
            lines.append("")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    rng = random.Random(SEED)
    batch1 = build_batch_1(rng)
    batch2 = build_batch_2(rng)
    batch3 = build_batch_3(rng)
    easy = build_eval_easy(rng)
    adversarial = build_eval_adversarial(rng)
    shifted = build_eval_shifted(rng)
    demo = [batch1, batch2, batch3]
    evals = [easy, adversarial, shifted]

    write_csv(OUTPUT_DIR / "source_a.csv", SOURCE_A_COLUMNS, batch1.source_a)
    write_csv(OUTPUT_DIR / "source_b.csv", SOURCE_B_COLUMNS, batch1.source_b)
    write_csv(OUTPUT_DIR / "batch2_source_a.csv", SOURCE_A_COLUMNS, batch2.source_a)
    write_csv(OUTPUT_DIR / "batch2_source_b.csv", SOURCE_B_COLUMNS, batch2.source_b)
    write_csv(OUTPUT_DIR / "batch3_source_a.csv", SOURCE_A_COLUMNS, batch3.source_a)
    write_csv(OUTPUT_DIR / "batch3_source_b.csv", SOURCE_B_COLUMNS, batch3.source_b)
    eval_dir = OUTPUT_DIR / "eval"
    eval_dir.mkdir(exist_ok=True)
    for batch, stem in (
        (easy, "easy"),
        (adversarial, "adversarial"),
        (shifted, "shifted"),
    ):
        write_csv(eval_dir / f"{stem}_a.csv", SOURCE_A_COLUMNS, batch.source_a)
        write_csv(eval_dir / f"{stem}_b.csv", SOURCE_B_COLUMNS, batch.source_b)
    write_ground_truth(
        OUTPUT_DIR / "ground_truth.csv",
        [row for batch in demo + evals for row in batch.ground_truth],
    )
    write_injection_summary(OUTPUT_DIR / "injection_summary.txt", demo + evals)

    for batch in demo + evals:
        print_summary(batch)
    print("\nWrote demo batches 1–3 (full-size) and data/eval/{easy,adversarial,shifted}_*.csv")
    print("Matcher must not read ground_truth.csv or injection_summary.txt.")


if __name__ == "__main__":
    main()
