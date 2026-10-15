"""Run a finite, read-only SDK-stream and REST recovery verification."""

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alpaca_ross_bot.config import PaperConfig
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.local_credentials import load_paper_credentials
from alpaca_ross_bot.order_reconciliation import OrderReconciler, ReconciliationPolicy
from alpaca_ross_bot.paper_order_http import PaperOrderHttp
from alpaca_ross_bot.paper_order_recovery import collect_rest_batch_async
from alpaca_ross_bot.paper_trade_stream import PaperTradeUpdateStream


async def verify(args: argparse.Namespace) -> dict[str, object]:
    root = args.runtime_root.resolve()
    config = PaperConfig.from_toml(args.config)
    credentials = config.resolve_credentials(
        load_paper_credentials(args.credentials_file, config)
    )
    journal = Journal.open_private(root / "journal.sqlite3", root)
    stream: PaperTradeUpdateStream | None = None
    task: asyncio.Task[None] | None = None
    try:
        api = PaperOrderHttp(
            config,
            credentials,
            timeout_seconds=args.timeout_seconds,
            max_request_bytes=65_536,
            max_response_bytes=1_048_576,
        )
        stream_api = PaperOrderHttp(
            config,
            credentials,
            timeout_seconds=args.timeout_seconds,
            max_request_bytes=65_536,
            max_response_bytes=1_048_576,
        )
        reference = api.verify_account(journal)
        lifecycle = OrderReconciler(journal, reference)
        policy = ReconciliationPolicy(
            timedelta(seconds=args.max_age_seconds),
            timedelta(seconds=args.max_capture_seconds),
            args.max_pages,
        )
        stream = PaperTradeUpdateStream(
            config,
            credentials,
            reference,
            verify_account=lambda: stream_api.verify_account(journal),
            on_frame=lifecycle.ingest_frame,
            on_health=lifecycle.stream_health,
            timeout_seconds=args.timeout_seconds,
            max_frame_bytes=1_048_576,
        )
        task = asyncio.create_task(
            stream.run_for(
                args.stream_seconds, reconnect_delay_seconds=0.25, max_reconnects=1
            )
        )
        deadline = asyncio.get_running_loop().time() + args.stream_seconds
        while lifecycle.view()["stream_status"] != "SUBSCRIBED":
            if task.done():
                await task
                raise ValueError("trade stream subscription was not verified")
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("trade stream subscription deadline elapsed")
            await asyncio.sleep(0.01)
        batch = await collect_rest_batch_async(
            api, root, reference, policy, clock=lambda: datetime.now(UTC)
        )
        if args.initialize_baseline and lifecycle.view()["baseline"] is None:
            baseline_capture = lifecycle.apply_rest_batch(batch, policy)
            if (
                not batch.complete
                or not batch.stable
                or batch.positions
                or batch.open_orders
                or journal.read_order_intent_ids()
            ):
                raise ValueError(
                    "baseline initialization requires complete empty state"
                )
            lifecycle.register_position_baseline(baseline_capture.watermark, policy)
            batch = await collect_rest_batch_async(
                api, root, reference, policy, clock=lambda: datetime.now(UTC)
            )
        # Drain queued stream callbacks before comparing the capture watermark.
        await asyncio.sleep(0.05)
        report = lifecycle.apply_rest_batch(batch, policy)
        stream.stop()
        await task
        return {
            "observed_at": datetime.now(UTC).isoformat(),
            "paper_account_verified": True,
            "sdk_subscription_verified": True,
            "capture_status": report.status,
            "difference_codes": sorted({item["code"] for item in report.differences}),
            "rebuild_verified": lifecycle.verify_rebuild(),
            "shutdown_requires_recovery": lifecycle.view()["stream_gap"],
            "snapshots_atomic": False,
            "trading_authorized": False,
            "broker_writes_performed": False,
        }
    finally:
        if stream is not None:
            stream.stop()
        if task is not None and not task.done():
            await task
        journal.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connect", action="store_true")
    parser.add_argument("--initialize-baseline", action="store_true")
    parser.add_argument(
        "--config", type=Path, default=Path("config/paper.example.toml")
    )
    parser.add_argument("--credentials-file", type=Path, required=True)
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=Path.home() / ".local/state/alpaca-bot/paper-engineering",
    )
    parser.add_argument("--timeout-seconds", type=float, default=5)
    parser.add_argument("--stream-seconds", type=float, default=30)
    parser.add_argument("--max-age-seconds", type=float, default=60)
    parser.add_argument("--max-capture-seconds", type=float, default=30)
    parser.add_argument("--max-pages", type=int, default=20)
    args = parser.parse_args()
    if not args.connect:
        print(json.dumps({"status": "NOT_RUN", "trading_authorized": False}))
        return
    try:
        report = asyncio.run(verify(args))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "error_class": type(exc).__name__,
                    "trading_authorized": False,
                }
            )
        )
        parser.exit(
            1, "read-only lifecycle verification failed; private data not logged\n"
        )
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
