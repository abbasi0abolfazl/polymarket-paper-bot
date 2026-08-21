# Roadmap

This project is a read-only research and paper-trading toolkit. It has no execution, wallet, key, signing, or order-submission capability.

## Completed

### Phase 1 — Deterministic paper engine

- Depth-aware fill estimates, partial fills, fee-inclusive accounting, settlement, and risk limits.
- Unit tests for strategy math and position accounting.

### Phase 2 — Public-data capture

- Credential-free public market discovery and order-book snapshots when available.
- JSONL captures, SQLite integrity journal, replay, and audit commands.
- Stale-data and UP/DOWN book-skew guards.

### Phase 3 — BTC reference data and market evidence

- Public BTC/USD Chainlink TWAP capture with a heartbeat and bounded reconnects.
- Freshness report matching market and BTC timestamps.
- Immutable evidence files that retain the market window, opening BTC point, official resolution URL, and copied rule text.

### Phase 4 — Chronological research

- Immutable manually verified outcome labels.
- Walk-forward baseline: each later session is evaluated from parameters fitted only on earlier sessions.
- Coverage and abstentions shown beside directional accuracy.

### Phase 5 — Coordinated session collection

- `capture-session` starts public market-book and BTC-TWAP collection together for one named event.
- It remains collection-only and requires an explicit event slug.

## Next: data-quality gate

Collect a meaningful set of complete, independently verifiable sessions before interpreting results.

1. Capture each session before its declared market window begins.
2. Save an evidence record immediately, including the exact official resolution wording.
3. After resolution, create one manually verified outcome record.
4. Audit timestamp freshness, missing intervals, book depth, and one-leg exposure.
5. Run walk-forward research with a fixed training minimum and report both coverage and fee-inclusive results.

Suggested minimum is 30 complete sessions for an initial engineering review; it is not enough to establish profitability.

## Later: shadow validation

Only after the data-quality gate:

1. Freeze the feature definitions and research configuration.
2. Run paper decisions continuously without changing thresholds mid-session.
3. Reconcile every decision to the saved market book, BTC point, evidence record, and resolved outcome.
4. Compare results with conservative fee, latency, and partial-fill assumptions.

## Execution is explicitly out of scope

This repository must not gain order placement, key handling, wallet connection, or regional-access work. Any future execution discussion requires separate legal, platform, security, and risk review, plus confirmed official eligibility.
