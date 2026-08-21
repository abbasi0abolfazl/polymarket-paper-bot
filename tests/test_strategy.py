import tempfile
import time
import unittest
from pathlib import Path

from polymarket_paper_bot.engine import PaperTradingEngine
from polymarket_paper_bot.evidence import (
    MarketEvidence,
    evidence_report,
    opening_observation,
    write_evidence,
)
from polymarket_paper_bot.journal import SnapshotJournal
from polymarket_paper_bot.live import PolymarketPublicClient, PublicApiError
from polymarket_paper_bot.models import BotConfig, MarketSnapshot, OrderBookLevel
from polymarket_paper_bot.replay import append_snapshot, load_snapshots
from polymarket_paper_bot.research import ResearchSession, walk_forward_report
from polymarket_paper_bot.reference import (
    BtcTwapObservation,
    ReferenceFeedError,
    append_observation,
    load_observations,
    parse_twap_event,
    synchronization_report,
)
from polymarket_paper_bot.strategy import (
    average_fill_price,
    calculate_trade_edge,
    fractional_kelly,
    inventory_penalty,
    paired_arbitrage_edge,
    polymarket_fee,
    quote_fill,
    revise_probability,
)


class StrategyMathTests(unittest.TestCase):
    def test_probability_update_matches_article_example(self):
        updated = revise_probability(0.42, 1.62)
        self.assertAlmostEqual(updated, 0.5394, places=3)

    def test_correlation_discount_reduces_signal_strength(self):
        full = revise_probability(0.42, 1.62, 1.0)
        discounted = revise_probability(0.42, 1.62, 0.5)
        self.assertGreater(full, discounted)
        self.assertGreater(discounted, 0.42)

    def test_average_fill_uses_order_book_depth(self):
        price, filled = average_fill_price(
            [OrderBookLevel(0.46, 80), OrderBookLevel(0.48, 200)], 100
        )
        self.assertEqual(filled, 100)
        self.assertAlmostEqual(price, 0.464)

    def test_quote_fill_calculates_fee_at_each_depth_level(self):
        quote = quote_fill(
            [OrderBookLevel(0.46, 80), OrderBookLevel(0.48, 200)],
            100,
            fee_rate=0.07,
        )
        expected_fee = round(
            80 * 0.07 * 0.46 * 0.54 + 20 * 0.07 * 0.48 * 0.52,
            5,
        )
        self.assertAlmostEqual(quote.average_price, 0.464)
        self.assertEqual(quote.fee, expected_fee)

    def test_official_crypto_fee_example_at_fifty_cents(self):
        self.assertEqual(polymarket_fee(100, 0.50, 0.07), 1.75)

    def test_makers_pay_no_fee(self):
        self.assertEqual(polymarket_fee(100, 0.50, 0.07, "maker"), 0.0)

    def test_trade_edge_matches_article_example(self):
        edge = calculate_trade_edge(0.55, 0.49, 0.012, 0.008)
        self.assertAlmostEqual(edge, 0.04)

    def test_fractional_kelly_matches_article_example(self):
        allocation = fractional_kelly(0.60, 0.49, 0.20)
        self.assertAlmostEqual(allocation, 0.0431, places=3)

    def test_paired_arbitrage_edge(self):
        edge = paired_arbitrage_edge(0.47, 0.49, 0.03493, 0.01)
        self.assertAlmostEqual(edge, -0.00493)

    def test_inventory_penalty_scales_with_position(self):
        self.assertGreater(inventory_penalty(100, 0.08, 0.03, 200), 0)
        self.assertLess(inventory_penalty(-100, 0.08, 0.03, 200), 0)


class EngineTests(unittest.TestCase):
    @staticmethod
    def snapshot(up_price=0.45, down_price=0.48, **overrides):
        values = dict(
            market_id="pair",
            timestamp=1,
            seconds_remaining=60,
            prior_up_probability=0.5,
            signal_multiplier=1.0,
            short_term_volatility=0.02,
            up_asks=(OrderBookLevel(up_price, 100),),
            down_asks=(OrderBookLevel(down_price, 100),),
        )
        values.update(overrides)
        return MarketSnapshot(**values)

    def test_pair_trade_is_preferred_after_real_fees(self):
        engine = PaperTradingEngine(BotConfig(order_size=50))
        decisions = engine.evaluate(self.snapshot())
        self.assertEqual([decision.side for decision in decisions], ["UP", "DOWN"])
        self.assertEqual(decisions[0].quantity, 50)
        self.assertGreater(decisions[0].fee, 0)

    def test_old_pair_example_is_rejected_after_real_fees(self):
        engine = PaperTradingEngine(BotConfig(order_size=50))
        decisions = engine.evaluate(self.snapshot(up_price=0.47, down_price=0.49))
        self.assertEqual(decisions, [])

    def test_stale_snapshot_is_rejected(self):
        engine = PaperTradingEngine(BotConfig(max_data_age_seconds=1))
        decisions = engine.evaluate(self.snapshot(data_age_seconds=1.1))
        self.assertEqual(decisions, [])

    def test_skewed_books_are_rejected(self):
        engine = PaperTradingEngine(BotConfig(max_book_skew_seconds=1))
        decisions = engine.evaluate(self.snapshot(book_skew_seconds=1.1))
        self.assertEqual(decisions, [])

    def test_settlement_updates_cash_fees_and_realized_pnl(self):
        engine = PaperTradingEngine(BotConfig(order_size=10))
        engine.process(self.snapshot(market_id="settle"))
        pnl = engine.settle("settle", "UP")
        self.assertAlmostEqual(pnl, 0.35203, places=5)
        self.assertAlmostEqual(engine.account.cash, 10000.35203, places=5)
        self.assertAlmostEqual(engine.account.total_fees, 0.34797, places=5)


class PublicDataTests(unittest.TestCase):
    def setUp(self):
        now_ms = str(int(time.time() * 1000))

        def opener(url):
            if url.endswith("/api/geoblock"):
                return {"blocked": True, "country": "IR", "region": ""}
            if "/events/keyset?" in url:
                return {
                    "events": [
                        {
                            "slug": "bitcoin-up-or-down-demo",
                            "title": "Bitcoin Up or Down",
                            "markets": [
                                {
                                    "id": "123",
                                    "conditionId": "0xabc",
                                    "slug": "bitcoin-up-or-down-demo-market",
                                    "question": "Bitcoin Up or Down - Demo",
                                    "active": True,
                                    "closed": False,
                                    "endDate": "2099-01-01T00:00:00Z",
                                    "outcomes": '["Up", "Down"]',
                                    "clobTokenIds": '["up-token", "down-token"]',
                                    "feesEnabled": True,
                                    "takerBaseFee": "0.07",
                                }
                            ],
                        }
                    ]
                }
            if "token_id=up-token" in url:
                return {
                    "timestamp": now_ms,
                    "bids": [{"price": "0.44", "size": "100"}],
                    "asks": [{"price": "0.45", "size": "100"}],
                }
            if "token_id=down-token" in url:
                return {
                    "timestamp": now_ms,
                    "bids": [{"price": "0.47", "size": "100"}],
                    "asks": [{"price": "0.48", "size": "100"}],
                }
            raise AssertionError(f"Unexpected URL: {url}")

        self.client = PolymarketPublicClient(opener=opener)

    def test_geoblock_response_omits_ip(self):
        access = self.client.check_access()
        self.assertTrue(access.blocked)
        self.assertEqual(access.country, "IR")
        self.assertFalse(hasattr(access, "ip"))

    def test_geoblock_http_403_fails_closed(self):
        def opener(_):
            raise PublicApiError("Public endpoint returned HTTP 403")

        access = PolymarketPublicClient(opener=opener).check_access()
        self.assertTrue(access.blocked)
        self.assertEqual(access.country, "unknown")

    def test_market_discovery_maps_up_and_down_tokens(self):
        markets = self.client.discover_active_markets()
        self.assertEqual(len(markets), 1)
        self.assertEqual(markets[0].up_token_id, "up-token")
        self.assertEqual(markets[0].down_token_id, "down-token")
        self.assertEqual(markets[0].taker_fee_rate, 0.07)

    def test_live_snapshot_uses_market_implied_probability(self):
        market = self.client.discover_active_markets()[0]
        snapshot = self.client.snapshot(market)
        expected = 0.445 / (0.445 + 0.475)
        self.assertAlmostEqual(snapshot.prior_up_probability, expected)
        self.assertEqual(snapshot.signal_multiplier, 1.0)
        self.assertEqual(snapshot.source, "polymarket-public-api")
        self.assertLess(snapshot.book_skew_seconds, 0.001)

    def test_snapshot_capture_round_trip(self):
        market = self.client.discover_active_markets()[0]
        snapshot = self.client.snapshot(market)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "capture.jsonl"
            append_snapshot(path, snapshot)
            loaded = load_snapshots(path)
        self.assertEqual(loaded, [snapshot])


class JournalTests(unittest.TestCase):
    @staticmethod
    def snapshot(timestamp=100):
        return MarketSnapshot(
            market_id="journal-market",
            timestamp=timestamp,
            seconds_remaining=60,
            prior_up_probability=0.5,
            signal_multiplier=1.0,
            short_term_volatility=0.01,
            up_asks=(OrderBookLevel(0.45, 10),),
            down_asks=(OrderBookLevel(0.48, 10),),
            source="test",
        )

    def test_journal_deduplicates_and_audits(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            database = Path(directory) / "journal.sqlite"
            with SnapshotJournal(database) as journal:
                self.assertTrue(journal.record(self.snapshot(100)))
                self.assertFalse(journal.record(self.snapshot(100)))
                self.assertTrue(journal.record(self.snapshot(101)))
                report = journal.audit()
        self.assertEqual(report["snapshot_count"], 2)
        self.assertEqual(report["unique_count"], 2)
        self.assertEqual(report["duplicate_count"], 0)
        self.assertEqual(report["ordering_violations"], 0)


class ReferenceFeedTests(unittest.TestCase):
    @staticmethod
    def snapshot(timestamp=1_786_120_000):
        return MarketSnapshot(
            market_id="reference-market",
            timestamp=timestamp,
            seconds_remaining=60,
            prior_up_probability=0.5,
            signal_multiplier=1.0,
            short_term_volatility=0.01,
            up_asks=(OrderBookLevel(0.45, 10),),
            down_asks=(OrderBookLevel(0.48, 10),),
            source="test",
        )

    def test_parses_documented_twap_payload_without_price_rounding(self):
        observation = parse_twap_event(
            {
                "type": "update",
                "payload": {
                    "symbol": "btc/usd",
                    "value": "65000.12345678",
                    "timestamp": 1_786_120_000_000,
                    "windowSeconds": 30,
                },
            }
        )
        self.assertEqual(observation.value, "65000.12345678")
        self.assertEqual(observation.observed_at, 1_786_120_000)
        self.assertEqual(observation.window_seconds, 30)

    def test_rejects_wrong_symbol_and_invalid_price(self):
        event = {
            "type": "update",
            "payload": {
                "symbol": "eth/usd",
                "value": "65000",
                "timestamp": 1_786_120_000,
                "windowSeconds": 30,
            },
        }
        with self.assertRaises(ReferenceFeedError):
            parse_twap_event(event)
        event["payload"]["symbol"] = "btc/usd"
        event["payload"]["value"] = "NaN"
        with self.assertRaises(ReferenceFeedError):
            parse_twap_event(event)

    def test_observation_round_trip_and_sync_report(self):
        observation = BtcTwapObservation(
            symbol="btc/usd",
            value="65000.5",
            observed_at=1_786_120_003,
            received_at=1_786_120_004,
            window_seconds=30,
            source="test",
        )
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "btc.jsonl"
            append_observation(path, observation)
            loaded = load_observations(path)
        report = synchronization_report([self.snapshot()], loaded, max_age_seconds=5)
        self.assertEqual(loaded, [observation])
        self.assertEqual(report["fresh_snapshots"], 1)
        self.assertEqual(report["stale_or_missing_snapshots"], 0)
        self.assertEqual(report["worst_match_age_seconds"], 3)


class EvidenceTests(unittest.TestCase):
    def test_opening_price_uses_first_observation_after_start(self):
        observations = [
            BtcTwapObservation("btc/usd", "64999", 99, 99, 30),
            BtcTwapObservation("btc/usd", "65000", 103, 103, 30),
            BtcTwapObservation("btc/usd", "65001", 105, 105, 30),
        ]
        opening = opening_observation(observations, 100, max_delay=5)
        self.assertEqual(opening.value, "65000")

    def test_evidence_is_immutable_and_reports_window_coverage(self):
        evidence = MarketEvidence(
            market_id="evidence-market", event_slug="event", market_slug="market",
            question="BTC Up or Down", window_start=100, window_end=200,
            opening_btc_price="65000", opening_btc_observed_at=100,
            btc_source="test", resolution_url="https://example.test/rules",
            resolution_text="Resolve UP if the ending value exceeds the opening value.",
            recorded_at=101,
        )
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "evidence.json"
            write_evidence(path, evidence)
            with self.assertRaises(FileExistsError):
                write_evidence(path, evidence)
        snapshot = EngineTests.snapshot(market_id="evidence-market", timestamp=150)
        report = evidence_report([snapshot], evidence)
        self.assertEqual(report["snapshots_inside_declared_window"], 1)
        self.assertEqual(report["snapshots_outside_declared_window"], 0)


class ResearchTests(unittest.TestCase):
    @staticmethod
    def session(index: int, change: str, outcome: str) -> ResearchSession:
        return ResearchSession(
            market_id=f"research-{index}", resolved_at=100 + index,
            opening_btc_price="100", closing_btc_price=change,
            opening_up_price=0.5, outcome=outcome,
            outcome_source_url="https://example.test/outcome", outcome_note="verified",
            evidence_path=f"evidence-{index}.json",
        )

    def test_walk_forward_never_uses_future_outcomes_for_fit(self):
        sessions = [
            self.session(1, "101", "UP"), self.session(2, "99", "DOWN"),
            self.session(3, "102", "UP"), self.session(4, "98", "DOWN"),
        ]
        report = walk_forward_report(sessions, min_train=2)
        self.assertEqual(report["out_of_sample_sessions"], 2)
        self.assertEqual(report["predictions"], 2)
        self.assertEqual(report["directional_accuracy"], 1.0)


if __name__ == "__main__":
    unittest.main()
