# Polymarket Paper Bot

A read-only, standard-library-only research project for short binary markets. It implements the pricing and risk concepts discussed in [Daniro’s article](https://x.com/Dan1ro0/status/2085766554407317704), but it cannot connect a wallet, accept a private key, sign anything, or submit an order.

This is a data-collection and paper-trading tool—not evidence of a profitable strategy.

## What is now implemented

- Depth-aware executable-price estimates and partial fills
- Official Polymarket-style taker fees: `shares × fee rate × price × (1 − price)`, rounded to five decimals
- Separate maker/taker treatment: maker fees are zero
- Paired UP/DOWN and directional paper decisions, fee-inclusive accounting, settlement, and hard risk limits
- Data-age and order-book-skew guards that reject unsafe inputs
- Public, unauthenticated market discovery and order-book reads
- `capture` and `live-paper` commands using real public data when it is available
- Optional public BTC/USD Chainlink TWAP capture with explicit heartbeat and reconnect handling
- A timing report that verifies whether each market snapshot has a sufficiently fresh BTC reference point
- JSONL recording plus a duplicate-resistant SQLite snapshot journal and `audit` command
- Replay and report commands for reproducible offline analysis

The old 47¢ UP + 49¢ DOWN example is now correctly rejected after real crypto taker fees and the safety margin.

## Install and verify

Python 3.11+ is the only requirement.

```bash
cd /home/abolfazl/Documents/Codex/2026-08-19/https-x-com-dan1ro0-status-2085766554407317704/outputs/polymarket-paper-bot
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
```

To capture the optional public BTC reference feed, install the small extra dependency:

```bash
python -m pip install -e '.[live]'
```

You may also run directly without installation:

```bash
PYTHONPATH=src python3 -m polymarket_paper_bot.cli demo
```

## Safe commands

All commands are read-only or local-paper operations.

```bash
# Check public access status. It does not print or store your IP address.
PYTHONPATH=src python3 -m polymarket_paper_bot.cli check-access

# Run the deterministic fee-aware demo.
PYTHONPATH=src python3 -m polymarket_paper_bot.cli demo

# Replay or summarize stored snapshots.
PYTHONPATH=src python3 -m polymarket_paper_bot.cli replay data/example_snapshots.jsonl
PYTHONPATH=src python3 -m polymarket_paper_bot.cli report data/example_snapshots.jsonl

# Discover public BTC Up/Down markets when public access is available.
PYTHONPATH=src python3 -m polymarket_paper_bot.cli discover

# Record 30 public order-book samples. This makes no paper decisions.
PYTHONPATH=src python3 -m polymarket_paper_bot.cli capture \
  --samples 30 --interval 2 \
  --output data/live-capture.jsonl \
  --database data/live-capture.sqlite

# Make paper-only decisions from the same public feed and preserve the inputs.
PYTHONPATH=src python3 -m polymarket_paper_bot.cli live-paper \
  --samples 30 --interval 2 \
  --output data/live-paper.jsonl \
  --database data/live-paper.sqlite

# Capture one specific market's public books and public BTC TWAP at the same time.
# Use an event slug from `discover`; this command has no order or wallet capability.
PYTHONPATH=src python3 -m polymarket_paper_bot.cli capture-session \
  --event-slug "REAL_EVENT_SLUG" \
  --samples 30 --interval 2 --output data/session-market-snapshots.jsonl \
  --btc-samples 20 --btc-window 30 --btc-output data/session-btc-twap.jsonl

# Check duplicate and chronology integrity of a SQLite capture journal.
PYTHONPATH=src python3 -m polymarket_paper_bot.cli audit data/live-paper.sqlite

# Capture future public BTC/USD Chainlink 30-second TWAP updates. No account is used.
PYTHONPATH=src python3 -m polymarket_paper_bot.cli capture-btc \
  --samples 20 --window 30 --output data/btc-twap.jsonl

# Check that captured market snapshots and BTC reference values are close enough in time.
PYTHONPATH=src python3 -m polymarket_paper_bot.cli sync-report \
  data/example_snapshots.jsonl data/example_btc_twap.jsonl --max-age 5

# Verify one immutable market-evidence record against the stored snapshots.
PYTHONPATH=src python3 -m polymarket_paper_bot.cli evidence-check \
  data/example_snapshots.jsonl data/example_market_evidence.json
```

If a public endpoint returns `403`, the program fails closed and never attempts a workaround. It is expected that public data or trading is unavailable in some jurisdictions.

## How live-paper decides

`live-paper` is intentionally conservative until a validated BTC signal model exists:

1. It reads public UP and DOWN books.
2. It computes a market-implied probability from the two midpoints.
3. It assumes no directional informational edge (`signal_multiplier = 1`).
4. It evaluates only pricing structures that still pass depth, fee, freshness, skew, and risk checks.
5. It records inputs so results can be replayed locally.

It does not claim that raw market prices provide predictive edge. The BTC reference-feed collection and timing checks from Phase 3 are now included, using Polymarket's documented public RTDS Chainlink TWAP topics. The feed begins at the next update and is not a historical backfill, so start `capture`/`live-paper` and `capture-btc` together for a research session.

Before analysis, create one immutable evidence file per market. The command derives the opening BTC price from your locally captured TWAP observations, but requires you to copy the exact market window and resolution wording from its official market page. It refuses to overwrite a record, so later results retain the original assumptions.

```bash
PYTHONPATH=src python3 -m polymarket_paper_bot.cli record-evidence \
  --market-id "POLYMARKET_CONDITION_ID" \
  --event-slug "EVENT_SLUG" --market-slug "MARKET_SLUG" \
  --question "Bitcoin Up or Down ..." \
  --window-start "2026-08-21T12:00:00Z" \
  --window-end "2026-08-21T12:05:00Z" \
  --resolution-url "https://polymarket.com/event/..." \
  --resolution-text "Paste the exact market resolution rule here" \
  --btc-observations data/btc-twap.jsonl \
  --output data/evidence/MARKET_SLUG.json
```

The next remaining step is chronological signal research using only sessions whose market evidence, reference data, and order books have all been captured.

## Chronological research

`record-session` creates an immutable outcome label after a market has resolved. It takes the opening price from its evidence record and the first BTC TWAP value after the declared market end. You must verify and enter the actual resolved outcome and its source; the tool does not guess settlement from a BTC price.

```bash
PYTHONPATH=src python3 -m polymarket_paper_bot.cli record-session \
  --evidence data/evidence/REAL_MARKET.json \
  --btc-observations data/btc-twap.jsonl \
  --opening-up-price 0.47 \
  --outcome UP \
  --outcome-source-url "https://polymarket.com/event/..." \
  --outcome-note "Verified against the final market resolution." \
  --output data/research/REAL_MARKET.json
```

Evaluate multiple independently recorded sessions in chronological order:

```bash
PYTHONPATH=src python3 -m polymarket_paper_bot.cli walk-forward \
  data/research_examples/session-*.json --min-train 4
```

For each later session, the baseline chooses its BTC-move threshold using only earlier outcome labels. `directional_accuracy` is calculated only for calls made; always inspect `coverage`, abstentions, sample size, and fee-inclusive paper results before drawing conclusions. Example records are synthetic and must not be treated as performance evidence.

## Data records

JSONL stores portable snapshots for replay. The SQLite journal stores a stable SHA-256 digest of each snapshot and ignores exact duplicates. `audit` reports the number of snapshots, markets, duplicates, time range, and any insertion-order timestamp regressions.

Record only data you are permitted to collect and retain. The project does not collect account credentials, wallet addresses, private keys, or user IP addresses.

## Configuration

Copy the example before adjusting risk controls:

```bash
cp config.example.json config.json
PYTHONPATH=src python3 -m polymarket_paper_bot.cli --config config.json show-config
```

`default_taker_fee_rate` is `0.07` for crypto by default. When public market metadata provides a valid market-specific rate, that rate takes precedence.

## Validation gate before any future execution work

- Correct market-specific fees, resolutions, and settlement rules independently.
- Capture enough real sessions to test latency, partial fills, stale data, and one-leg risk.
- Validate a signal chronologically on unseen data, with results after fees and slippage.
- Run shadow mode continuously and reconcile every paper decision against stored data.
- Verify geographic and legal eligibility. Do not bypass regional restrictions.
- Keep any future execution adapter separate, disabled by default, and out of this codebase until all gates pass.
