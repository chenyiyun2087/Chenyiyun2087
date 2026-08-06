"""Pure task-to-command mapping for the Web scheduler."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Mapping


@dataclass(frozen=True)
class TaskCommandContext:
    tasks: Mapping[str, Mapping[str, object]]
    normalize_datestr: Callable[[object], str | None]
    trusted_strategy: str
    trusted_risk_profile: str
    trusted_config: Mapping[str, object]
    daily_audit_task: str


def build_task_script_parts(
    task_name: str,
    run_options: Mapping[str, object] | None,
    context: TaskCommandContext,
) -> list[str]:
    task_config = context.tasks[task_name]
    script = str(task_config["script"])
    options = dict(run_options or {})
    datestr = context.normalize_datestr(options.get("datestr"))
    historical_safe = bool(options.get("historical_safe"))
    historical_reissue = bool(options.get("historical_reissue"))
    today = datetime.now().strftime("%Y%m%d")

    if task_name == "sina_picture":
        return [script, "config_1", datestr or today, "--capture-only"]
    if task_name == "sina_analyse":
        return [script, "config_1", datestr or today, "--analyze-only"]
    if task_name == "adc_bs_detect":
        return [script, "--date", datestr or today]
    if task_name == "bs_ocr_adc_compare":
        target = datestr or today
        return [script, "--start", target, "--end", target]
    if task_name == "sina_score":
        return [script, *(["--date", datestr] if datestr else []), "--force"]
    if task_name == "sina_bs_consensus":
        return [script, *(["--date", datestr] if datestr else [])]
    if task_name == "rolling_strategy_scorer":
        # The scorer is a digest source. It always persists scores/weights but
        # never sends its former standalone card; the integrated strategy
        # review publishes the single routine daily notification.
        args: list[str] = ["--no-push"]
        if datestr:
            args[0:0] = ["--calc-date", _iso_date(datestr)]
        return [script, *args]
    if task_name == "pit_forward_shadow_collection":
        return [script, "--as-of", _iso_date(datestr or today)]
    if task_name == "trusted_strategy_candidates":
        target = datestr or today
        release_id = (
            f"{context.trusted_strategy}_{target}_"
            f"{context.trusted_config['config_sha']}"
        )
        args = [
            "--risk-profile",
            context.trusted_risk_profile,
            "--strategy",
            context.trusted_strategy,
            "--release-id",
            release_id,
            "--top-n",
            str(context.trusted_config["top_n"]),
            "--max-total-positions",
            str(context.trusted_config["max_total_positions"]),
            "--write-db",
            "--no-emit-orders" if historical_safe else "--emit-orders",
            "--write-signal-snapshot",
        ]
        # Candidate/order details are included in the integrated review. The
        # source task remains silent on success and still receives scheduler
        # failure/block/retry notifications.
        if historical_reissue:
            args.append("--historical-reissue")
        if datestr:
            args.extend(["--date", datestr])
        return [script, *args]
    if task_name == "trusted_strategy_shadow_monitor":
        target = datestr or today
        release_id = (
            f"{context.trusted_strategy}_{target}_"
            f"{context.trusted_config['config_sha']}"
        )
        args = [
            "--strategy-id",
            context.trusted_strategy,
            "--release-id",
            release_id,
            "--write-db",
            "--allow-empty",
        ]
        # Shadow metrics are persisted and summarized by the integrated review;
        # do not emit a second routine card.
        if datestr:
            args.extend(["--execution-date", datestr])
        if historical_reissue:
            args.append("--historical-reissue")
        return [script, *args]
    if task_name == "trusted_strategy_backtest":
        return [script, *(["--date", datestr] if datestr else [])]
    if task_name == "trusted_strategy_performance_review":
        args = ["--review-window-days", "63", "--allow-substitute-diagnostic"]
        if not historical_safe or historical_reissue:
            args.append("--notify-feishu")
        if datestr:
            args.extend(["--date", datestr])
        if historical_reissue:
            args.append("--historical-reissue")
        return [script, *args]
    if task_name == "sina_bs_image_weekly_cleanup":
        return [script, "--execute", *(["--date", datestr] if datestr else [])]
    if task_name == "sina_m8":
        return [script, "--lookback-dates", "60"]
    if task_name == "sina_snapshot":
        return [script, "snapshot", *(["--date", _iso_date(datestr)] if datestr else [])]
    if task_name == "sina_m7_sell":
        return [script, *(["--date", _iso_date(datestr)] if datestr else [])]
    if task_name == "bs_signal_monthly_cycle":
        args = ["--date", _iso_date(datestr)] if datestr else []
        if bool(options.get("force")):
            args.append("--force")
        return [script, *args]
    if task_name == "candle_diag_scan":
        args = ["--skip-existing"]
        if datestr:
            args.extend(["--date", _iso_date(datestr)])
        return [script, *args]
    if task_name in ("alpha_challenger_shadow_record",
                     "alpha_challenger_shadow_reconcile",
                     "daily_vls_scores"):
        # v5.4.1 fix: the pipeline args (e.g. --mode reconcile) were
        # previously DROPPED here, so the reconcile task silently ran in
        # record mode.  Honor the pipeline-declared args explicitly.
        args = list(task_config.get("args") or [])
        if datestr:
            args.extend(["--date", _iso_date(datestr)])
        return [script, *args]
    if task_name == context.daily_audit_task:
        # The integrated audit accepts this flag but sends only when the final
        # cross-task reconciliation contains an anomaly.
        args = ["--notify-feishu"]
        if datestr:
            args.extend(["--date", datestr])
        if historical_safe:
            args.append("--historical-safe")
        if historical_reissue:
            args.append("--historical-reissue")
        return [script, *args]
    # Generic fallback: honor any pipeline-declared args (fail-safe so a
    # future task with args can never silently drop its mode).
    pipeline_args = list(task_config.get("args") or [])
    if pipeline_args:
        return [script, *pipeline_args]
    return [script]


def _iso_date(datestr: str) -> str:
    return f"{datestr[:4]}-{datestr[4:6]}-{datestr[6:]}"
