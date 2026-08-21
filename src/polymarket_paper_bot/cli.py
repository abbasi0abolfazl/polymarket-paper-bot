from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from .evidence import (
    MarketEvidence,
    evidence_report,
    load_evidence,
    opening_observation,
    parse_timestamp,
    write_evidence,
)
from .engine import PaperTradingEngine
from .journal import SnapshotJournal
from .live import PolymarketPublicClient, PublicApiError, PublicMarket
from .models import BotConfig, MarketSnapshot, OrderBookLevel
from .replay import append_snapshot, load_snapshots
from .reference import (
    ReferenceFeedError,
    capture_twap,
    load_observations,
    synchronization_report,
)
from .research import ResearchSession, load_sessions, walk_forward_report, write_session


def load_config(path: str | None) -> BotConfig:
    if path is None:
        return BotConfig()
    with Path(path).open(encoding="utf-8") as stream:
        payload = json.load(stream)
    if "execution_cost_rate" in payload:
        raise ValueError(
            "execution_cost_rate is obsolete. Use default_taker_fee_rate; fees now "
            "follow the official nonlinear formula."
        )
    return BotConfig(**payload)


def demo_snapshots() -> list[MarketSnapshot]:
    return [
        MarketSnapshot(
            market_id="btc-updown-demo",
            timestamp=1,
            seconds_remaining=190,
            prior_up_probability=0.42,
            signal_multiplier=1.62,
            correlation_discount=0.85,
            short_term_volatility=0.025,
            up_asks=(OrderBookLevel(0.46, 80), OrderBookLevel(0.48, 200)),
            down_asks=(OrderBookLevel(0.57, 120), OrderBookLevel(0.59, 200)),
        ),
        MarketSnapshot(
            market_id="btc-updown-pair-demo",
            timestamp=2,
            seconds_remaining=60,
            prior_up_probability=0.50,
            signal_multiplier=1.0,
            short_term_volatility=0.018,
            up_asks=(OrderBookLevel(0.45, 150),),
            down_asks=(OrderBookLevel(0.48, 150),),
        ),
    ]


def render_trade(decision) -> str:
    return (
        f"{decision.market_id}: PAPER BUY {decision.quantity:.2f} {decision.side} "
        f"@ {decision.average_price:.3f} | fee=${decision.fee:.5f} "
        f"fair={decision.fair_value:.3f} edge={decision.usable_edge:.3%} | "
        f"{decision.reason}"
    )


def render_account(engine: PaperTradingEngine) -> None:
    open_risk = sum(
        position.capital_at_risk for position in engine.account.positions.values()
    )
    print(
        f"Cash=${engine.account.cash:,.2f} | open cost=${open_risk:,.2f} | "
        f"fees=${engine.account.total_fees:,.5f} | trades={engine.account.trade_count} | "
        f"realized P&L=${engine.account.realized_pnl:,.2f}"
    )
    for market_id, position in engine.account.positions.items():
        print(
            f"{market_id}: UP={position.up_quantity:.2f}, DOWN={position.down_quantity:.2f}, "
            f"matched={position.matched_quantity:.2f}, net={position.directional_exposure:.2f}, "
            f"fees=${position.fees_paid:.5f}"
        )


def run_snapshots(
    engine: PaperTradingEngine,
    snapshots: list[MarketSnapshot],
    show_no_trade: bool = True,
) -> int:
    trade_count = 0
    for snapshot in snapshots:
        decisions = engine.process(snapshot)
        if decisions:
            for decision in decisions:
                print(render_trade(decision))
            trade_count += len(decisions)
        elif show_no_trade:
            print(f"{snapshot.market_id}: no paper trade")
    render_account(engine)
    return trade_count


def describe_market(market: PublicMarket) -> str:
    end_time = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(market.end_timestamp))
    return (
        f"{market.question}\n"
        f"  market={market.market_id} end={end_time} fee_rate={market.taker_fee_rate}\n"
        f"  UP={market.up_token_id}\n"
        f"  DOWN={market.down_token_id}"
    )


def select_market(client: PolymarketPublicClient, args) -> PublicMarket:
    markets = client.discover_active_markets(
        query=args.query,
        event_slug=args.event_slug,
        limit=getattr(args, "limit", 100),
    )
    if not markets:
        raise PublicApiError(
            "No active matching UP/DOWN market was found. Supply --event-slug for a specific event."
        )
    return markets[0]


def print_access(client: PolymarketPublicClient) -> None:
    status = client.check_access()
    region = f"-{status.region}" if status.region else ""
    state = "BLOCKED" if status.blocked else "available"
    print(f"Trading access: {state} ({status.country}{region}).")
    if status.blocked:
        print("Research mode only. This project does not bypass geographic restrictions.")


def run_live(client: PolymarketPublicClient, config: BotConfig, args, paper: bool) -> None:
    if args.samples < 1:
        raise ValueError("samples must be at least 1")
    if args.interval < 0:
        raise ValueError("interval cannot be negative")
    print_access(client)
    market = select_market(client, args)
    print(describe_market(market))
    engine = PaperTradingEngine(config)
    journal = SnapshotJournal(args.database) if args.database else None
    try:
        for index in range(args.samples):
            snapshot = client.snapshot(market)
            if args.output:
                append_snapshot(args.output, snapshot)
            is_new = journal.record(snapshot) if journal else True
            best_up = min(snapshot.up_asks, key=lambda level: level.price).price
            best_down = min(snapshot.down_asks, key=lambda level: level.price).price
            state = "recorded" if is_new else "duplicate ignored"
            print(
                f"sample={index + 1} UP ask={best_up:.3f} DOWN ask={best_down:.3f} "
                f"age={snapshot.data_age_seconds:.3f}s skew={snapshot.book_skew_seconds:.3f}s | {state}"
            )
            if paper:
                decisions = engine.process(snapshot)
                if decisions:
                    for decision in decisions:
                        print(render_trade(decision))
                else:
                    print("  no paper trade")
            if index + 1 < args.samples:
                time.sleep(args.interval)
    finally:
        if journal:
            journal.close()
    if paper:
        render_account(engine)
    if args.output:
        print(f"Recorded snapshots: {Path(args.output).resolve()}")


async def run_coordinated_capture(
    client: PolymarketPublicClient, config: BotConfig, args
) -> None:
    """Start independent public market and BTC feeds together; never trades."""
    if not args.event_slug:
        raise ValueError("capture-session requires --event-slug to avoid recording a wrong market")
    if Path(args.output).resolve() == Path(args.btc_output).resolve():
        raise ValueError("Market and BTC output paths must be different")
    await asyncio.gather(
        asyncio.to_thread(run_live, client, config, args, False),
        capture_twap(
            output=args.btc_output,
            samples=args.btc_samples,
            window_seconds=args.btc_window,
        ),
    )
    print("Coordinated public capture completed. Create market evidence before research.")


def add_live_arguments(parser: argparse.ArgumentParser, capture_default: str | None) -> None:
    parser.add_argument("--query", default="Bitcoin Up or Down")
    parser.add_argument("--event-slug", help="Use a specific Polymarket event slug")
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--output", default=capture_default, help="Append snapshots to JSONL")
    parser.add_argument(
        "--database",
        help="Optional SQLite journal for duplicate-resistant capture and later auditing",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only real-data and replayable paper trading for binary markets."
    )
    parser.add_argument("--config", help="Path to a JSON config file")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("demo", help="Run deterministic fee-aware examples")

    replay_parser = subparsers.add_parser("replay", help="Replay JSONL market snapshots")
    replay_parser.add_argument("file", help="Path to a JSONL snapshot file")

    report_parser = subparsers.add_parser("report", help="Summarize a JSONL paper replay")
    report_parser.add_argument("file", help="Path to a JSONL snapshot file")

    audit_parser = subparsers.add_parser(
        "audit", help="Audit snapshot-journal integrity and chronological ordering"
    )
    audit_parser.add_argument("database", help="Path to a SQLite snapshot journal")

    subparsers.add_parser(
        "show-config", help="Print the effective risk and freshness configuration"
    )
    subparsers.add_parser(
        "check-access", help="Check geographic trading availability without showing the IP"
    )

    discover_parser = subparsers.add_parser(
        "discover", help="List active public UP/DOWN markets"
    )
    discover_parser.add_argument("--query", default="Bitcoin Up or Down")
    discover_parser.add_argument("--event-slug")
    discover_parser.add_argument("--limit", type=int, default=100)

    capture_parser = subparsers.add_parser(
        "capture", help="Record real public order books without making decisions"
    )
    add_live_arguments(capture_parser, "data/live-capture.jsonl")

    live_parser = subparsers.add_parser(
        "live-paper", help="Use real public order books for non-trading paper decisions"
    )
    add_live_arguments(live_parser, None)

    session_capture_parser = subparsers.add_parser(
        "capture-session",
        help="Capture one named market and BTC TWAP concurrently; no trading",
    )
    add_live_arguments(session_capture_parser, "data/session-market-snapshots.jsonl")
    session_capture_parser.add_argument("--btc-samples", type=int, default=20)
    session_capture_parser.add_argument("--btc-window", type=int, choices=(30, 60), default=30)
    session_capture_parser.add_argument("--btc-output", default="data/session-btc-twap.jsonl")

    btc_parser = subparsers.add_parser(
        "capture-btc", help="Record public BTC/USD Chainlink TWAP updates"
    )
    btc_parser.add_argument("--samples", type=int, default=20)
    btc_parser.add_argument("--window", type=int, choices=(30, 60), default=30)
    btc_parser.add_argument("--symbol", default="btc/usd")
    btc_parser.add_argument("--output", default="data/btc-twap.jsonl")

    sync_parser = subparsers.add_parser(
        "sync-report", help="Check market-snapshot timing against captured BTC TWAP data"
    )
    sync_parser.add_argument("snapshots", help="Path to a JSONL market snapshot file")
    sync_parser.add_argument("btc_observations", help="Path to a JSONL BTC TWAP file")
    sync_parser.add_argument("--max-age", type=float, default=5.0)

    evidence_parser = subparsers.add_parser(
        "record-evidence",
        help="Create an immutable opening-price and resolution-rule record",
    )
    evidence_parser.add_argument("--market-id", required=True)
    evidence_parser.add_argument("--event-slug", required=True)
    evidence_parser.add_argument("--market-slug", required=True)
    evidence_parser.add_argument("--question", required=True)
    evidence_parser.add_argument("--window-start", required=True, help="Unix or ISO-8601 timestamp")
    evidence_parser.add_argument("--window-end", required=True, help="Unix or ISO-8601 timestamp")
    evidence_parser.add_argument("--resolution-url", required=True)
    evidence_parser.add_argument("--resolution-text", required=True)
    evidence_parser.add_argument("--btc-observations", required=True)
    evidence_parser.add_argument("--max-opening-delay", type=float, default=60.0)
    evidence_parser.add_argument("--output", required=True)

    evidence_check_parser = subparsers.add_parser(
        "evidence-check", help="Verify captured snapshots against one evidence record"
    )
    evidence_check_parser.add_argument("snapshots")
    evidence_check_parser.add_argument("evidence")

    session_parser = subparsers.add_parser(
        "record-session", help="Create an immutable, manually verified research outcome"
    )
    session_parser.add_argument("--evidence", required=True)
    session_parser.add_argument("--btc-observations", required=True)
    session_parser.add_argument("--opening-up-price", type=float, required=True)
    session_parser.add_argument("--outcome", choices=("UP", "DOWN"), required=True)
    session_parser.add_argument("--outcome-source-url", required=True)
    session_parser.add_argument("--outcome-note", required=True)
    session_parser.add_argument("--max-closing-delay", type=float, default=60.0)
    session_parser.add_argument("--output", required=True)

    research_parser = subparsers.add_parser(
        "walk-forward", help="Evaluate a BTC-direction baseline on later unseen sessions"
    )
    research_parser.add_argument("sessions", nargs="+", help="Immutable research-session JSON files")
    research_parser.add_argument("--min-train", type=int, default=5)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        if args.command == "show-config":
            print(json.dumps(asdict(config), indent=2))
            return
        if args.command == "demo":
            run_snapshots(PaperTradingEngine(config), demo_snapshots())
            return
        if args.command in ("replay", "report"):
            snapshots = load_snapshots(args.file)
            print(
                f"Loaded {len(snapshots)} snapshots from "
                f"{len({snapshot.market_id for snapshot in snapshots})} markets."
            )
            trades = run_snapshots(
                PaperTradingEngine(config),
                snapshots,
                show_no_trade=args.command == "replay",
            )
            if args.command == "report":
                print(f"Paper decisions executed: {trades}")
            return
        if args.command == "audit":
            with SnapshotJournal(args.database) as journal:
                print(json.dumps(journal.audit(), indent=2))
            return
        if args.command == "capture-btc":
            captured = asyncio.run(
                capture_twap(
                    output=args.output,
                    samples=args.samples,
                    window_seconds=args.window,
                    symbol=args.symbol,
                )
            )
            print(f"Captured {captured} BTC TWAP observations: {Path(args.output).resolve()}")
            return
        if args.command == "sync-report":
            report = synchronization_report(
                load_snapshots(args.snapshots),
                load_observations(args.btc_observations),
                args.max_age,
            )
            print(json.dumps(report, indent=2))
            return
        if args.command == "record-evidence":
            window_start = parse_timestamp(args.window_start)
            opening = opening_observation(
                load_observations(args.btc_observations),
                window_start,
                args.max_opening_delay,
            )
            evidence = MarketEvidence(
                market_id=args.market_id,
                event_slug=args.event_slug,
                market_slug=args.market_slug,
                question=args.question,
                window_start=window_start,
                window_end=parse_timestamp(args.window_end),
                opening_btc_price=opening.value,
                opening_btc_observed_at=opening.observed_at,
                btc_source=opening.source,
                resolution_url=args.resolution_url,
                resolution_text=args.resolution_text,
                recorded_at=time.time(),
            )
            write_evidence(args.output, evidence)
            print(f"Immutable market evidence recorded: {Path(args.output).resolve()}")
            return
        if args.command == "evidence-check":
            print(json.dumps(evidence_report(load_snapshots(args.snapshots), load_evidence(args.evidence)), indent=2))
            return
        if args.command == "record-session":
            evidence = load_evidence(args.evidence)
            closing = opening_observation(
                load_observations(args.btc_observations),
                evidence.window_end,
                args.max_closing_delay,
            )
            session = ResearchSession(
                market_id=evidence.market_id,
                resolved_at=evidence.window_end,
                opening_btc_price=evidence.opening_btc_price,
                closing_btc_price=closing.value,
                opening_up_price=args.opening_up_price,
                outcome=args.outcome,
                outcome_source_url=args.outcome_source_url,
                outcome_note=args.outcome_note,
                evidence_path=str(Path(args.evidence).resolve()),
            )
            write_session(args.output, session)
            print(f"Immutable research session recorded: {Path(args.output).resolve()}")
            return
        if args.command == "walk-forward":
            print(json.dumps(walk_forward_report(load_sessions(args.sessions), args.min_train), indent=2))
            return

        client = PolymarketPublicClient(
            default_taker_fee_rate=config.default_taker_fee_rate
        )
        if args.command == "check-access":
            print_access(client)
        elif args.command == "discover":
            print_access(client)
            markets = client.discover_active_markets(
                query=args.query, event_slug=args.event_slug, limit=args.limit
            )
            if not markets:
                print("No matching active markets found.")
            for market in markets:
                print(describe_market(market))
        elif args.command == "capture":
            run_live(client, config, args, paper=False)
        elif args.command == "live-paper":
            run_live(client, config, args, paper=True)
        elif args.command == "capture-session":
            asyncio.run(run_coordinated_capture(client, config, args))
    except (
        OSError,
        ValueError,
        PublicApiError,
        ReferenceFeedError,
        json.JSONDecodeError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
