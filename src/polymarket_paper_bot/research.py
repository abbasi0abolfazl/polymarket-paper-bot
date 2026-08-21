"""Chronological, paper-only evaluation of a simple BTC direction baseline."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class ResearchSession:
    market_id: str
    resolved_at: float
    opening_btc_price: str
    closing_btc_price: str
    opening_up_price: float
    outcome: str
    outcome_source_url: str
    outcome_note: str
    evidence_path: str

    def __post_init__(self) -> None:
        if self.resolved_at <= 0:
            raise ValueError("resolved_at must be positive")
        if not 0 < self.opening_up_price < 1:
            raise ValueError("opening_up_price must be between 0 and 1")
        if self.outcome not in ("UP", "DOWN"):
            raise ValueError("outcome must be UP or DOWN")
        if not self.outcome_source_url or not self.outcome_note.strip():
            raise ValueError("outcome_source_url and outcome_note are required")
        if Decimal(self.opening_btc_price) <= 0 or Decimal(self.closing_btc_price) <= 0:
            raise ValueError("BTC prices must be positive")

    @property
    def btc_return(self) -> float:
        return float(
            (Decimal(self.closing_btc_price) - Decimal(self.opening_btc_price))
            / Decimal(self.opening_btc_price)
        )


def write_session(path: str | Path, session: ResearchSession) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"Research session already exists and is immutable: {destination}")
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(asdict(session), stream, indent=2, sort_keys=True)
        stream.write("\n")


def load_sessions(paths: list[str | Path]) -> list[ResearchSession]:
    sessions: list[ResearchSession] = []
    for path in paths:
        with Path(path).open(encoding="utf-8") as stream:
            sessions.append(ResearchSession(**json.load(stream)))
    return sorted(sessions, key=lambda item: item.resolved_at)


def walk_forward_report(
    sessions: list[ResearchSession], min_train: int = 5
) -> dict[str, int | float | list[dict[str, int | float | str]]]:
    """Evaluate a BTC-direction baseline strictly against later sessions.

    For each test session, a return threshold is selected from earlier outcomes only.
    A no-signal result counts as an abstention, not a successful prediction.
    """
    if min_train < 1:
        raise ValueError("min_train must be at least 1")
    ordered = sorted(sessions, key=lambda item: item.resolved_at)
    if len(ordered) <= min_train:
        raise ValueError("Need more sessions than min_train for chronological evaluation")
    if len({item.market_id for item in ordered}) != len(ordered):
        raise ValueError("Each research session must use a unique market_id")

    rows: list[dict[str, int | float | str]] = []
    for index in range(min_train, len(ordered)):
        threshold = _fit_threshold(ordered[:index])
        session = ordered[index]
        predicted = _predict(session.btc_return, threshold)
        rows.append(
            {
                "market_id": session.market_id,
                "resolved_at": session.resolved_at,
                "btc_return": session.btc_return,
                "threshold": threshold,
                "predicted": predicted,
                "actual": session.outcome,
                "correct": int(predicted == session.outcome),
            }
        )
    predictions = [row for row in rows if row["predicted"] != "NO_SIGNAL"]
    correct = sum(int(row["correct"]) for row in predictions)
    return {
        "total_sessions": len(ordered),
        "training_sessions_before_first_test": min_train,
        "out_of_sample_sessions": len(rows),
        "predictions": len(predictions),
        "abstentions": len(rows) - len(predictions),
        "coverage": len(predictions) / len(rows),
        "correct_predictions": correct,
        "directional_accuracy": correct / len(predictions) if predictions else 0.0,
        "rows": rows,
    }


def _fit_threshold(sessions: list[ResearchSession]) -> float:
    candidates = (0.0, 0.0005, 0.001, 0.002, 0.005)
    # Score abstentions as zero, so a threshold cannot win just by avoiding calls.
    return max(
        candidates,
        key=lambda threshold: (
            sum(
                _predict(item.btc_return, threshold) == item.outcome
                for item in sessions
            )
            / len(sessions),
            -threshold,
        ),
    )


def _predict(btc_return: float, threshold: float) -> str:
    if btc_return > threshold:
        return "UP"
    if btc_return < -threshold:
        return "DOWN"
    return "NO_SIGNAL"
