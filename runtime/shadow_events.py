"""Forward Shadow execution event ledger (v5.5.2).

Append-only JSONL of every execution action, one file per execution date:

  exports/forward_shadow_evidence/execution/events/<date>.jsonl

Events are the DURABLE TRUTH of the shadow: the state machine
(runtime/shadow_execution_state.py) is reconstructed from events on
every run (``replay``), so reruns are deterministic and no executed
state can be lost or silently rewritten.  orders.json remains as a
human-readable projection of the same truth.

Event types map 1:1 to production actions (NOT to every state-machine
transition — replay derives the full chain from the action):

  ORDER_PRECOMMITTED  precommit(): a BUY order enters at SIGNAL_CREATED,
                      is sealed into TARGET_PORTFOLIO_SEALED and ends at
                      ORDER_PRECOMMITTED with the T-day close reference.
  BUY_FILLED          reconcile(): fill at open -> BUY_FILLED + HOLDING.
  BUY_REJECTED        reconcile(): NO_OPEN / gate block / missing prev
                      close -> BUY_REJECTED.
  SELL_PRECOMMITTED   sell_precommit(): HOLDING position -> SELL_PRECOMMITTED
                      with the T-day close reference.
  SELL_FILLED         reconcile(): sell fill at open -> SELL_FILLED and,
                      per the v5.4.1 round-trip rule, closes the position
                      (ROUND_TRIP_COMPLETED) in the same replay step.
  NAV_SNAPSHOT        nav(): daily mark-to-market (no order_id).

Idempotency: an event's identity is (order_id, event_type); appending
the same identity twice is a no-op (IDEMPOTENT), never a second write.
An event file that is rewritten (truncated/reordered) is detected by
replay — a sequence the state machine rejects raises EVENT_LOG_CORRUPT.

Fail-closed: every replay transition must succeed; any missing order,
illegal transition, or duplicate BUY raises instead of silently
continuing.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Iterator, Optional

from runtime.shadow_execution_state import (
    SELL_FILLED,
    ShadowOrder,
    ShadowStateMachine,
)

# ── Event types ──────────────────────────────────────────────────────

ORDER_PRECOMMITTED = "ORDER_PRECOMMITTED"
BUY_FILLED = "BUY_FILLED"
BUY_REJECTED = "BUY_REJECTED"
SELL_PRECOMMITTED = "SELL_PRECOMMITTED"
SELL_FILLED = "SELL_FILLED"
SELL_REJECTED = "SELL_REJECTED"
NAV_SNAPSHOT = "NAV_SNAPSHOT"

ALL_EVENT_TYPES = frozenset({
    ORDER_PRECOMMITTED, BUY_FILLED, BUY_REJECTED,
    SELL_PRECOMMITTED, SELL_FILLED, SELL_REJECTED, NAV_SNAPSHOT,
})

# Events that carry an order identity (NAV_SNAPSHOT does not).
ORDERED_EVENTS = frozenset({
    ORDER_PRECOMMITTED, BUY_FILLED, BUY_REJECTED,
    SELL_PRECOMMITTED, SELL_FILLED, SELL_REJECTED,
})


def event_log_path(execution_zone: Path, execution_date: str) -> Path:
    """The append-only event file for one execution date."""
    return execution_zone / "events" / f"{execution_date}.jsonl"


def compute_event_id(event: dict) -> str:
    """Deterministic identity — excludes event_id/event_time so a rerun
    of the same action produces the same id (idempotency contract)."""
    payload = {k: v for k, v in event.items()
               if k not in ("event_id", "event_time")}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def identity_of(event: dict) -> tuple:
    """Idempotency key for one event."""
    if event.get("event_type") in ORDERED_EVENTS:
        return (event["event_type"], event.get("order_id"))
    return (event["event_type"], event.get("challenger_id"),
            event.get("execution_date"))


def append_event(log_path: Path, event: dict, seen: set | None = None,
                 verify: bool = True) -> tuple[str, bool]:
    """Append one event; returns (event_id, appended).

    ``seen`` carries the identities already present in the log (built by
    :func:`existing_identities`) — a repeat identity is an idempotent
    no-op.  With ``verify`` the parent dir is created and the line is
    written atomically (tmp + rename) so a crash never leaves a torn
    line.  Raises ValueError for an event with an unknown type or a
    missing order_id on an ordered event type.
    """
    etype = event.get("event_type")
    if etype not in ALL_EVENT_TYPES:
        raise ValueError(f"unknown event_type {etype!r}")
    if etype in ORDERED_EVENTS and not event.get("order_id"):
        raise ValueError(f"{etype} requires order_id")
    ident = identity_of(event)
    if seen is not None and ident in seen:
        return (event.get("event_id")
                or compute_event_id(event), False)
    event_id = compute_event_id(event)
    event["event_id"] = event_id
    event.setdefault("event_time",
                     __import__("datetime").datetime.now().isoformat(
                         timespec="seconds"))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
    tmp = log_path.with_suffix(".jsonl.tmp")
    if log_path.exists():
        tmp.write_bytes(log_path.read_bytes() + line.encode())
    else:
        tmp.write_text(line, encoding="utf-8")
    tmp.replace(log_path)
    return event_id, True


def iter_events(log_path: Path) -> Iterator[dict]:
    """Yield every event in order; a blank/partial tail line is ignored
    (crash-torn write), anything else malformed raises."""
    if not log_path.exists():
        return
    for lineno, raw in enumerate(log_path.read_text(encoding="utf-8")
                                 .splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"event log corrupt at {log_path}:{lineno} — {exc}") from exc


def existing_identities(log_path: Path) -> set:
    """Idempotency keys already recorded in the log."""
    return {identity_of(ev) for ev in iter_events(log_path)}


# ── Replay: events -> state machine ──────────────────────────────────


def _find_order(machine: ShadowStateMachine, order_id: str) -> ShadowOrder:
    for o in machine.orders:
        if o.order_id == order_id:
            return o
    raise RuntimeError(
        f"event log corrupt: order {order_id} referenced before its "
        "ORDER_PRECOMMITTED event")


def _find_position(machine: ShadowStateMachine, challenger_id: str,
                   symbol: str):
    key = (challenger_id, symbol)
    pos = machine.positions.get(key)
    if pos is None:
        raise RuntimeError(
            f"event log corrupt: position {key} referenced with no "
            "open BUY_FILLED")
    return pos


def replay(log_path: Path,
           machine: ShadowStateMachine | None = None) -> ShadowStateMachine:
    """Rebuild the state machine from the event log (fail-closed).

    ``machine`` carries a pre-existing machine so multi-date logs can be
    replayed into ONE accumulating truth (a SELL on a later execution
    date references a BUY on an earlier one — the files are per
    execution date, the chain is across files).

    Every transition must be legal; an illegal sequence, a missing
    order, or a duplicate BUY raises EVENT_LOG_CORRUPT instead of
    silently diverging from the recorded truth.
    """
    machine = machine or ShadowStateMachine()
    for ev in iter_events(log_path):
        etype = ev["event_type"]
        try:
            if etype == ORDER_PRECOMMITTED:
                order = ShadowOrder(
                    signal_date=ev["signal_date"],
                    execution_date=ev["execution_date"],
                    side=ev["side"], symbol=ev["symbol"],
                    challenger_id=ev["challenger_id"],
                    target_weight=ev["target_weight"],
                    target_shares=ev["target_shares"],
                    lot_adjusted_shares=ev.get("lot_adjusted_shares"),
                    precommit_price=ev.get("precommit_price"),
                    order_id=ev["order_id"],
                    package_sha=ev.get("source_package_sha"))
                machine.add_order(order)
                # Transition() returns a NEW record — chain the returned
                # objects, never the stale original (the ledger replaces
                # records in-place, so the old reference is gone).
                order = machine.seal_target_portfolio(order)
                machine.precommit(order, float(ev["precommit_price"]))
            elif etype == BUY_FILLED:
                order = _find_order(machine, ev["order_id"])
                machine.fill_buy(order, float(ev["fill_price"]),
                                 float(ev.get("slippage_bps", 0.0)))
            elif etype == BUY_REJECTED:
                order = _find_order(machine, ev["order_id"])
                machine.reject_buy(order, ev.get("reason") or "rejected")
            elif etype == SELL_PRECOMMITTED:
                pos = _find_position(machine, ev["challenger_id"],
                                     ev["symbol"])
                machine.precommit_sell(
                    pos, float(ev["precommit_price"]),
                    execution_date=ev["execution_date"],
                    order_id=ev["order_id"],
                    shares=int(ev["target_shares"]) if ev.get("target_shares")
                    else None)
            elif etype == SELL_FILLED:
                pos = _find_position(machine, ev["challenger_id"],
                                     ev["symbol"])
                machine.fill_sell(pos, float(ev["fill_price"]),
                                  float(ev.get("slippage_bps", 0.0)),
                                  shares=int(ev["shares"])
                                  if ev.get("shares") else None)
                # v5.5.3: a PARTIAL sell leaves remaining_shares > 0 and
                # the position HOLDING — only a full exit completes the
                # round trip.
                if pos.remaining_shares == 0:
                    machine.complete_round_trip(pos)
            elif etype == SELL_REJECTED:
                pos = _find_position(machine, ev["challenger_id"],
                                     ev["symbol"])
                machine.reject_sell(pos, ev.get("reason") or "rejected")
            elif etype == NAV_SNAPSHOT:
                continue  # account valuation — no machine transition
            else:
                raise RuntimeError(f"unknown event type {etype!r}")
        except (KeyError, ValueError, RuntimeError) as exc:
            # A missing required field is corrupt too — fail-closed.
            raise RuntimeError(
                f"EVENT_LOG_CORRUPT at {log_path.name} "
                f"({ev.get('event_type')} {ev.get('order_id', '')}): "
                f"{exc}") from exc
    return machine


def replay_all(execution_zone: Path) -> ShadowStateMachine:
    """Replay every dated event file (sorted) — the full accumulated
    execution truth across all execution dates.  ONE shared machine, so
    cross-day chains (buy on day E, sell on a later day) reconstruct."""
    events_dir = execution_zone / "events"
    machine = ShadowStateMachine()
    if not events_dir.exists():
        return machine
    for path in sorted(events_dir.glob("*.jsonl")):
        machine = replay(path, machine)
    return machine


def exported_orders(machine: ShadowStateMachine) -> list[dict]:
    """Project the machine's orders to the orders.json schema.

    Keeps the pre-v5.5.2 file format (orders.json) in sync with the
    event truth — the file is a projection, never the source of truth.
    """
    out = []
    for o in machine.orders:
        rec = {
            "signal_date": o.signal_date,
            "execution_date": o.execution_date,
            "challenger_id": o.challenger_id,
            "symbol": o.symbol,
            "side": o.side,
            "target_weight": o.target_weight,
            "target_shares": o.target_shares,
            "lot_adjusted_shares": o.lot_adjusted_shares,
            "precommit_price": o.precommit_price,
            "fill_price": o.fill_price,
            "fill_status": o.fill_status,
            "slippage_bps": o.slippage_bps,
            "rejection_reason": o.rejection_reason,
            "state": o.state,
            "package_sha": o.package_sha,
            "order_id": o.order_id,
        }
        if o.state == SELL_FILLED:
            # SELL orders live on positions; project them so orders.json
            # stays complete for existing consumers.
            pass
        out.append(rec)
    # SELL orders are projected from positions' sell_order.
    for (cand, sym), pos in machine.positions.items():
        sell = pos.sell_order
        if sell is None:
            continue
        out.append({
            "signal_date": sell.signal_date,
            "execution_date": sell.execution_date,
            "challenger_id": sell.challenger_id,
            "symbol": sell.symbol,
            "side": "SELL",
            "target_weight": sell.target_weight,
            "target_shares": sell.target_shares,
            "lot_adjusted_shares": sell.lot_adjusted_shares,
            "precommit_price": sell.precommit_price,
            "fill_price": sell.fill_price,
            "fill_status": sell.fill_status,
            "slippage_bps": sell.slippage_bps,
            "rejection_reason": sell.rejection_reason,
            "state": sell.state,
            "package_sha": None,
            "order_id": sell.order_id,
        })
    return out


def iter_all_events(execution_zone: Path) -> Iterable[dict]:
    """Every event across all dates (sorted) — for account rebuilding."""
    events_dir = execution_zone / "events"
    if not events_dir.exists():
        return
    for path in sorted(events_dir.glob("*.jsonl")):
        yield from iter_events(path)
