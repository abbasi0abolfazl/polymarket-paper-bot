# Operating guide

## Normal offline workflow

```bash
PYTHONPATH=src python3 -m polymarket_paper_bot.cli demo
PYTHONPATH=src python3 -m polymarket_paper_bot.cli replay data/example_snapshots.jsonl
PYTHONPATH=src python3 -m polymarket_paper_bot.cli walk-forward \
  data/research_examples/session-*.json --min-train 4
```

These commands work without any account or live market access.

## Live public-data workflow, if officially available

1. Run `check-access` and respect a blocked result.
2. Use `discover` to find an event only where public access is available.
3. Start `capture-session` before the market begins.
4. Create immutable evidence with the official market window and resolution wording.
5. After the market resolves, add an immutable verified outcome session.
6. Run `walk-forward` only on completed sessions.

## Verification commands

```bash
python -m unittest discover -s tests -v
PYTHONPATH=src python3 -m polymarket_paper_bot.cli audit data/live-paper.sqlite
PYTHONPATH=src python3 -m polymarket_paper_bot.cli sync-report \
  data/example_snapshots.jsonl data/example_btc_twap.jsonl --max-age 5
PYTHONPATH=src python3 -m polymarket_paper_bot.cli evidence-check \
  data/example_snapshots.jsonl data/example_market_evidence.json
```

## Data handling

Captured data is intentionally excluded from Git. Keep it local unless you have a legitimate reason and permission to retain or share it. Never put credentials, keys, wallet details, or personal identifiers in evidence, session, configuration, or capture files.
