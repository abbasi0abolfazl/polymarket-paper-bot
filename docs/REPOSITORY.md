# Repository map

| Path | Purpose |
| --- | --- |
| `src/polymarket_paper_bot/engine.py` | Paper decisions, risk checks, positions, and settlement. |
| `src/polymarket_paper_bot/live.py` | Read-only public market discovery and order-book snapshots. |
| `src/polymarket_paper_bot/reference.py` | Public BTC/USD TWAP capture and synchronization checks. |
| `src/polymarket_paper_bot/evidence.py` | Immutable market-window and resolution evidence. |
| `src/polymarket_paper_bot/research.py` | Immutable outcome records and chronological walk-forward baseline. |
| `src/polymarket_paper_bot/journal.py` | SQLite capture journal and integrity audit. |
| `src/polymarket_paper_bot/cli.py` | Safe command-line interface. |
| `data/example_*` | Versioned synthetic examples used for local verification. |
| `data/evidence/`, `data/research/` | Local research records, ignored by Git. |
| `tests/` | Unit tests. |

See [the operating guide](OPERATIONS.md), [the roadmap](ROADMAP.md), and [regional-access policy](REGIONAL_ACCESS.md).
