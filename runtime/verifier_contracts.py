"""Formal verifier contracts (v5.5.3 A5).

Pure contract proofs for the alpha-signal evidence chain.  Every check
takes PARSED artifacts (dicts / lists / DataFrames) — no I/O, no DB —
so web/app.py's task-result verifiers and the hermetic tests exercise
the SAME proofs.  Each check returns (ok: bool, details: list[str])
with details[0] a one-line result; every check is fail-closed (missing
input -> not ok, never a silent pass).

The v5.5.1 artifact-based verifiers only asserted existence ("file
exists", "field present").  A5 upgrades them to CONTRACT proofs:
  - Package   : file SHA self-check + PIT lineage completeness +
                no-defect classification + weight sum / cash residual
  - Precommit : every portfolio row has one deterministic order
  - Reconcile : every order ends in exactly one fill/reject (no dangling)
  - Sell      : signal_date / package SHA / execution_date exact binding
  - NAV       : all candidates, cash >= 0, nav identity, fill conservation
  - E4        : epoch declared, dates >= start, no prestart mixing
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

# Allowed risk-overlay multipliers (pre-registered YAML values; the
# threshold drift guard in the builder covers the numeric contract).
ALLOWED_MULTIPLIERS = (1.0, 0.70, 0.50)
# Weights after the overlay must not exceed this many decimals of slack.
_WEIGHT_EPS = 1e-9

TERMINAL_EVENT_KINDS = ("BUY_FILLED", "BUY_REJECTED",
                        "SELL_FILLED", "SELL_REJECTED")
_SIDE_OF_EVENT = {"BUY_FILLED": "BUY", "BUY_REJECTED": "BUY",
                  "SELL_FILLED": "SELL", "SELL_REJECTED": "SELL"}


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha256_file(p: Path) -> str:
    return _sha256_bytes(p.read_bytes())


def _is_sha256(v: Any) -> bool:
    return isinstance(v, str) and bool(re.fullmatch(r"[0-9a-f]{64}", v))


# ── Package ────────────────────────────────────────────────────────────


def check_package_contract(
    manifest: dict,
    pkg_dir: Path,
    portfolios_df: pd.DataFrame,
    lineage_records: list[dict],
    required_families: tuple[str, ...],
) -> tuple[bool, list[str]]:
    """Sealed-package contract: status, classification, file SHAs,
    lineage completeness, weight sum + cash residual = 1 per candidate.

    ``lineage_records`` is input_manifest["lineage"] (or the records the
    manifest's source_snapshot_shas derive from); ``required_families``
    is the builder's REQUIRED_LINEAGE_FAMILIES.
    """
    problems: list[str] = []
    # ── status / classification ──
    if manifest.get("package_status") != "SEALED":
        problems.append(f"package_status={manifest.get('package_status')!r} "
                        "!= SEALED")
    reason = manifest.get("revision_reason") or ""
    if "KNOWN_DEFECT" in reason or "PRESTART" in reason.upper():
        problems.append("classification carries KNOWN_DEFECT/PRESTART "
                        f"({reason[:80]!r}) — not eligible evidence")
    if not manifest.get("candidate_ids"):
        problems.append("candidate_ids empty")

    # ── file SHA self-check (manifest vs on-disk bytes) ──
    for key, fname in (("universe_sha", "universe.parquet"),
                       ("factor_values_sha", "factor_values.parquet"),
                       ("scores_sha", "scores.parquet"),
                       ("target_portfolio_sha", "target_portfolios.parquet")):
        path = pkg_dir / fname
        if not path.exists():
            problems.append(f"{fname} missing")
            continue
        expected = manifest.get(key)
        actual = _sha256_file(path)
        if not _is_sha256(expected):
            problems.append(f"{fname}: manifest sha missing/invalid "
                            f"({expected!r})")
        elif actual != expected:
            problems.append(f"{fname}: manifest {expected} != actual {actual}")
    # root-of-trust inventory: package_sha256.json must bind EVERY payload
    # (including the manifest itself) to its real bytes.
    inv_path = pkg_dir / "package_sha256.json"
    if not inv_path.exists():
        problems.append("package_sha256.json missing — no root of trust")
    else:
        try:
            inventory = json.loads(inv_path.read_text(encoding="utf-8"))
        except ValueError:
            problems.append("package_sha256.json corrupt")
            inventory = {}
        # v5.5.3 (2026-08-06): the builder's inventory is NESTED —
        # {"schema_version", "package_dir", "files": {fname: sha},
        #  "package_sha256"}.  The 2026-08-05 first production seal was
        # wrongly failed: top-level keys were treated as file names and
        # the real payload SHAs were never checked.  Read the "files"
        # mapping; fall back to the legacy flat form.
        payloads = inventory.get("files")
        if not isinstance(payloads, dict):
            payloads = inventory
        for fname, expected in payloads.items():
            if fname in ("package_sha256", "schema_version", "package_dir"):
                continue
            path = pkg_dir / fname
            if not path.exists():
                problems.append(f"{fname}: in inventory but missing on disk")
            elif _sha256_file(path) != expected:
                problems.append(f"{fname}: inventory {expected} != "
                                f"actual {_sha256_file(path)}")
    if not _is_sha256(manifest.get("pit_contract_sha")):
        problems.append("pit_contract_sha missing/invalid — lineage not "
                        "bound to the PIT semantic contract")

    # ── PIT lineage completeness (every required family real) ──
    by_family = {rec.get("family"): rec for rec in lineage_records}
    for fam in required_families:
        rec = by_family.get(fam)
        if rec is None:
            problems.append(f"lineage family {fam} missing")
            continue
        if not _is_sha256(rec.get("content_sha256")):
            problems.append(f"lineage {fam}: content_sha256 "
                            f"{rec.get('content_sha256')!r} not a real SHA")
        if not isinstance(rec.get("row_count"), int) or rec["row_count"] < 1:
            problems.append(f"lineage {fam}: row_count "
                            f"{rec.get('row_count')!r} < 1")
        if rec.get("available_at_source") not in (
                "db_timestamp_column", "business_date_convention"):
            problems.append(f"lineage {fam}: available_at_source "
                            f"{rec.get('available_at_source')!r} not honest")

    # ── weight sum + cash residual = 1 per candidate ──
    if not portfolios_df.empty:
        for cid, grp in portfolios_df.groupby("candidate_id"):
            if grp.duplicated(subset=["symbol"]).any():
                problems.append(f"candidate {cid}: duplicate symbol rows")
                continue
            w = pd.to_numeric(grp["target_weight"], errors="coerce")
            w0 = pd.to_numeric(grp["weight_before_overlay"], errors="coerce")
            if w.isna().any() or w0.isna().any():
                problems.append(f"candidate {cid}: NaN weight present")
                continue
            total = float(w.sum())
            residual = 1.0 - total
            if total <= 0 or residual < -_WEIGHT_EPS or residual > 1.0:
                problems.append(f"candidate {cid}: weight sum {total:.6f} "
                                f"-> cash residual {residual:.6f} outside "
                                "[0, 1]")
            # equal weight before overlay, scaled by one allowed multiplier
            base = w0.iloc[0]
            if not (w0 - base).abs().max() <= 1e-9:
                problems.append(f"candidate {cid}: weight_before_overlay "
                                "not equal weight")
            mult = float(w.iloc[0] / base) if base > 0 else float("nan")
            if not any(abs(mult - m) <= 1e-9 for m in ALLOWED_MULTIPLIERS):
                problems.append(f"candidate {cid}: implied multiplier "
                                f"{mult!r} not in {ALLOWED_MULTIPLIERS}")

    if problems:
        return False, ([f"result=FAIL; task=package_contract; "
                        f"reasons={problems[0]}"] + problems)
    details = []
    for cid, grp in portfolios_df.groupby("candidate_id"):
        total = float(pd.to_numeric(grp["target_weight"]).sum())
        details.append(f"candidate={cid}; weight_sum={total:.4f}; "
                       f"cash_residual={1.0 - total:.4f}")
    return True, ([f"result=PASS; task=package_contract; "
                   f"families={len(required_families)}"] + details)


# ── Precommit ──────────────────────────────────────────────────────────


def check_precommit_contract(
    orders: list[dict],
    portfolio_rows: list[tuple[str, str]],
    held_keys: set[tuple[str, str]],
    expected_package_sha: str,
    expected_signal_date: str,
    expected_execution_date: str,
) -> tuple[bool, list[str]]:
    """Deterministic-order contract.

    Every portfolio row must map to exactly one BUY order or an
    already-held position (re-buy never duplicates).  Every BUY order
    must trace back to a portfolio row (no orphan orders).  All orders
    carry order_id + the package's exact SHA / signal / execution dates.
    Zero BUY orders is legitimate ONLY if the portfolio is empty or every
    row is already held — anything else fails (silent drops are defects).
    """
    problems: list[str] = []
    if expected_package_sha and not _is_sha256(expected_package_sha):
        problems.append(f"expected package sha invalid {expected_package_sha!r}")

    buys = [o for o in orders if o.get("side") == "BUY"]
    for o in orders:
        if "order_id" not in o:
            problems.append(f"order without order_id: {o.get('symbol')}")
        if expected_package_sha and o.get("package_sha") != expected_package_sha:
            problems.append(f"order {o.get('order_id')}: package_sha "
                            f"{o.get('package_sha')!r} != expected "
                            f"{expected_package_sha!r}")
        if o.get("signal_date") != expected_signal_date:
            problems.append(f"order {o.get('order_id')}: signal_date "
                            f"{o.get('signal_date')!r} != "
                            f"{expected_signal_date!r}")
        if o.get("execution_date") != expected_execution_date:
            problems.append(f"order {o.get('order_id')}: execution_date "
                            f"{o.get('execution_date')!r} != "
                            f"{expected_execution_date!r}")

    order_keys = {(o.get("challenger_id"), o.get("symbol"))
                  for o in buys}
    seen: set[tuple[str, str]] = set()
    for o in buys:
        key = (o.get("challenger_id"), o.get("symbol"))
        if key in seen:
            problems.append(f"duplicate BUY {key} on "
                            f"{expected_execution_date}")
        seen.add(key)

    # every BUY order traces to a portfolio row
    portfolio_keys = set(portfolio_rows)
    orphan = order_keys - portfolio_keys
    if orphan:
        problems.append(f"BUY orders without portfolio row: "
                        f"{sorted(orphan)[:3]}")

    # every portfolio row has an order or is already held
    missing = [k for k in portfolio_rows
               if k not in order_keys and k not in held_keys]
    if missing:
        problems.append(f"portfolio rows without order or held position: "
                        f"{missing[:3]}")

    # empty BUY day legitimacy
    if not buys and portfolio_rows:
        unheld = [k for k in portfolio_rows if k not in held_keys]
        if unheld:
            problems.append(f"zero BUY orders but {len(unheld)} portfolio "
                            "rows are not held — silent drop")

    if problems:
        return False, ([f"result=FAIL; task=precommit_contract; "
                        f"reasons={problems[0]}"] + problems)
    return True, [f"result=PASS; task=precommit_contract; "
                  f"buys={len(buys)}; portfolio_rows={len(portfolio_rows)}; "
                  f"held_skipped={sum(1 for k in portfolio_rows if k in held_keys)}"]


# ── Reconcile ──────────────────────────────────────────────────────────


def check_reconcile_contract(
    orders: list[dict],
    events: list[dict],
) -> tuple[bool, list[str]]:
    """No-dangling contract.

    Every PRECOMMITTED order must end in exactly one terminal event
    (BUY/SELL FILLED or REJECTED) keyed by order_id; terminal events must
    reference real orders; no order gets two terminals.  A day with zero
    orders must have zero fills (nothing to reconcile).
    """
    problems: list[str] = []
    pending = [o for o in orders
               if o.get("state") in ("ORDER_PRECOMMITTED", "SELL_PRECOMMITTED")]
    order_by_id = {o.get("order_id"): o for o in orders if o.get("order_id")}
    terminal_by_order: dict[str, list[dict]] = {}
    orphan_fills = 0
    for ev in events:
        kind = ev.get("event_type") if ev else None
        if kind not in TERMINAL_EVENT_KINDS:
            continue
        oid = ev.get("order_id")
        if oid not in order_by_id:
            orphan_fills += 1
            continue
        # side family must match the order's side
        if _SIDE_OF_EVENT[kind] != order_by_id[oid].get("side"):
            problems.append(f"event {kind} on order {oid} mismatches "
                            f"side {order_by_id[oid].get('side')}")
        terminal_by_order.setdefault(oid, []).append(kind)

    for o in pending:
        oid = o.get("order_id")
        terms = terminal_by_order.get(oid, [])
        if not terms:
            problems.append(f"order {oid} ({o.get('side')} "
                            f"{o.get('symbol')}) dangling — no fill/reject")
        elif len(terms) > 1:
            problems.append(f"order {oid} got {len(terms)} terminal events "
                            f"{terms} — at most one")
    if orphan_fills:
        problems.append(f"{orphan_fills} fill/reject events reference no order")
    if not orders and terminal_by_order:
        problems.append("fills exist but the day has zero orders")

    if problems:
        return False, ([f"result=FAIL; task=reconcile_contract; "
                        f"reasons={problems[0]}"] + problems)
    return True, [f"result=PASS; task=reconcile_contract; "
                  f"pending={len(pending)}; terminal_events={len(terminal_by_order)}"]


# ── Sell ───────────────────────────────────────────────────────────────


def check_sell_contract(
    sell_orders: list[dict],
    expected_signal_date: str,
    expected_package_sha: str,
) -> tuple[bool, list[str]]:
    """Exact-binding contract for SELL orders.

    SELL decisions are made on the T-day close against the T-day SEALED
    package and execute on T+1.  Every SELL order must bind the EXACT
    signal_date and package SHA of that decision day, must NOT execute on
    the signal day itself, and all sells of one decision day share one
    execution date (no cross-day mixing in one ledger file).
    """
    problems: list[str] = []
    if not sell_orders:
        return True, [f"result=PASS; task=sell_contract; sells=0"]
    exec_dates = {o.get("execution_date") for o in sell_orders}
    for o in sell_orders:
        # precommitted at decision time; terminal states are legal when
        # the verifier re-runs after T+1 reconcile — the BINDING fields
        # (signal_date / package_sha / execution_date) are what matter.
        if o.get("state") not in ("SELL_PRECOMMITTED", "SELL_FILLED",
                                  "SELL_REJECTED"):
            problems.append(f"sell {o.get('order_id')}: state "
                            f"{o.get('state')!r} not a ledger state")
        if o.get("signal_date") != expected_signal_date:
            problems.append(f"sell {o.get('order_id')}: signal_date "
                            f"{o.get('signal_date')!r} != "
                            f"{expected_signal_date!r} (cross-day mix)")
        if o.get("package_sha") != expected_package_sha:
            problems.append(f"sell {o.get('order_id')}: package_sha "
                            f"{o.get('package_sha')!r} != "
                            f"{expected_package_sha!r}")
        if not o.get("execution_date") or \
                o["execution_date"] == o.get("signal_date"):
            problems.append(f"sell {o.get('order_id')}: executes on the "
                            "signal day itself — must be T+1")
        if "order_id" not in o:
            problems.append("sell order without order_id")
    if len(exec_dates) > 1:
        problems.append(f"sells span execution dates {sorted(exec_dates)} "
                        "— one decision day must not mix ledger days")

    if problems:
        return False, ([f"result=FAIL; task=sell_contract; "
                        f"reasons={problems[0]}"] + problems)
    return True, [f"result=PASS; task=sell_contract; "
                  f"sells={len(sell_orders)}; execution_date="
                  f"{sorted(exec_dates)[0]}"]


# ── NAV ────────────────────────────────────────────────────────────────


def check_nav_contract(
    snapshots: list[dict],
    expected_candidate_ids: list[str],
    fills: list[dict],
    cost_rate_map: dict[str, float],
    prev_cash_by_candidate: dict[str, float] | None = None,
    slippage_bps_map: dict[str, float] | None = None,
) -> tuple[bool, list[str]]:
    """NAV contract: all candidates, cash >= 0, nav identity, conservation.

    For each candidate with a snapshot:
      - present in expected_candidate_ids (all candidates accounted)
      - cash >= 0 (never a silent negative-cash mark)
      - nav == cash + positions_mv (mark identity)
      - position_count >= 0
    When the previous day's cash is provided, the day's fills must
    reconcile EXACTLY:
        cash_now == prev_cash - BUY notional - BUY costs
                           + SELL proceeds - SELL costs
    (costs = notional * (cost_rate + slippage_bps / 1e4) per fill;
    cost_rate and slippage_bps from the FROZEN contracts — NOT the
    event's slippage_bps field, which records the realized fill-price
    deviation vs the precommit price and may be negative.  The virtual
    accounts debit the frozen contract rate at fill time; using the
    event deviation here would double-count price moves that are already
    inside fill_price.)
    """
    problems: list[str] = []
    snap_by_cand = {s.get("candidate_id"): s for s in snapshots}
    seen_candidates = set(snap_by_cand)
    for cid in expected_candidate_ids:
        if cid not in seen_candidates:
            problems.append(f"candidate {cid} missing from NAV snapshots")

    for cid, snap in snap_by_cand.items():
        cash = snap.get("cash")
        mv = snap.get("positions_mv")
        nav = snap.get("nav")
        if cash is None or mv is None or nav is None:
            problems.append(f"candidate {cid}: snapshot missing cash/mv/nav")
            continue
        if cash < 0:
            problems.append(f"candidate {cid}: cash {cash:.2f} < 0")
        if mv < 0:
            problems.append(f"candidate {cid}: positions_mv {mv:.2f} < 0")
        if abs(nav - (cash + mv)) > 1e-6 * max(1.0, abs(cash + mv)):
            problems.append(f"candidate {cid}: nav {nav:.2f} != cash+mv "
                            f"{cash + mv:.2f}")
        if not isinstance(snap.get("position_count"), int) or \
                snap["position_count"] < 0:
            problems.append(f"candidate {cid}: position_count "
                            f"{snap.get('position_count')!r} invalid")

    # fill conservation per candidate when previous cash is known
    if prev_cash_by_candidate:
        for cid, prev_cash in prev_cash_by_candidate.items():
            if cid not in snap_by_cand:
                continue
            cost_rate = cost_rate_map.get(cid, 0.0)
            slip = float((slippage_bps_map or {}).get(cid, 0.0)) / 1e4
            delta = 0.0
            for f in fills:
                if f.get("challenger_id") != cid:
                    continue
                kind = f.get("event_type")
                shares = f.get("shares") or 0
                price = f.get("fill_price") or 0.0
                notional = float(shares) * float(price)
                if kind == "BUY_FILLED":
                    delta -= notional * (1.0 + cost_rate + slip)
                elif kind == "SELL_FILLED":
                    delta += notional * (1.0 - cost_rate - slip)
            expected_cash = float(prev_cash) + delta
            if abs(expected_cash - float(snap_by_cand[cid]["cash"])) > \
                    1e-6 * max(1.0, abs(expected_cash)):
                problems.append(
                    f"candidate {cid}: fill conservation broken — "
                    f"prev_cash {prev_cash:.2f} + fill delta {delta:.2f} = "
                    f"{expected_cash:.2f} != cash {snap_by_cand[cid]['cash']:.2f}")

    if problems:
        return False, ([f"result=FAIL; task=nav_contract; "
                        f"reasons={problems[0]}"] + problems)
    return True, [f"result=PASS; task=nav_contract; "
                  f"candidates={len(snap_by_cand)}; cash_min="
                  f"{min((s['cash'] for s in snap_by_cand.values()), default=0.0):.2f}"]


# ── E4 ─────────────────────────────────────────────────────────────────


def check_e4_contract(
    registry_start: str | None,
    registry_epoch_id: str | None,
    valid_dates: list[str],
    counted_round_trips: int,
) -> tuple[bool, list[str]]:
    """E4 counting contract.

    - the true-blind epoch must be DECLARED (registry start non-null) —
      a NOT_STARTED registry makes any E4 count invalid
    - every counted date must be >= the declared start (prestart dates —
      KNOWN_DEFECT_PRESTART_PACKAGE days — never count)
    - round trips >= 0
    """
    problems: list[str] = []
    if not registry_start:
        problems.append("true_blind_start is null — epoch NOT_STARTED, "
                        "no E4 evidence exists yet")
    if registry_epoch_id and not registry_epoch_id.startswith("v5.5.3"):
        problems.append(f"epoch_id {registry_epoch_id!r} not a v5.5.3 "
                        "declaration")
    prestart = [d for d in valid_dates
                if registry_start and d < registry_start]
    if prestart:
        problems.append(f"{len(prestart)} counted dates predate the "
                        f"declared start {registry_start}: {prestart[:3]}")
    if not isinstance(counted_round_trips, int) or counted_round_trips < 0:
        problems.append(f"round trips {counted_round_trips!r} invalid")

    if problems:
        return False, ([f"result=FAIL; task=e4_contract; "
                        f"reasons={problems[0]}"] + problems)
    return True, [f"result=PASS; task=e4_contract; "
                  f"start={registry_start}; valid_dates={len(valid_dates)}; "
                  f"round_trips={counted_round_trips}"]
