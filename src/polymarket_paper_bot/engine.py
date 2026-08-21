from __future__ import annotations

from .models import (
    BotConfig,
    MarketSnapshot,
    PaperAccount,
    Position,
    Side,
    TradeDecision,
)
from .strategy import (
    calculate_trade_edge,
    fractional_kelly,
    inventory_penalty,
    paired_arbitrage_edge,
    quote_fill,
    revise_probability,
)


class PaperTradingEngine:
    """Decision and accounting engine. It cannot place live orders."""

    def __init__(self, config: BotConfig | None = None) -> None:
        self.config = config or BotConfig()
        self.account = PaperAccount(cash=self.config.starting_cash)

    def evaluate(self, snapshot: MarketSnapshot) -> list[TradeDecision]:
        position = self.account.positions.get(snapshot.market_id, Position())
        if self.account.realized_pnl <= -self.config.daily_loss_limit:
            return []
        if position.capital_at_risk >= self.config.max_capital_per_market:
            return []
        if snapshot.data_age_seconds > self.config.max_data_age_seconds:
            return []
        if snapshot.book_skew_seconds > self.config.max_book_skew_seconds:
            return []

        fair_up = revise_probability(
            snapshot.prior_up_probability,
            snapshot.signal_multiplier,
            snapshot.correlation_discount,
        )
        fair_down = 1 - fair_up
        up_quote = quote_fill(
            snapshot.up_asks,
            self.config.order_size,
            snapshot.taker_fee_rate,
        )
        down_quote = quote_fill(
            snapshot.down_asks,
            self.config.order_size,
            snapshot.taker_fee_rate,
        )
        paired_quantity = min(up_quote.quantity, down_quote.quantity, self.config.order_size)
        pair_up_quote = quote_fill(
            snapshot.up_asks,
            paired_quantity,
            snapshot.taker_fee_rate,
        )
        pair_down_quote = quote_fill(
            snapshot.down_asks,
            paired_quantity,
            snapshot.taker_fee_rate,
        )
        paired_costs = pair_up_quote.fee_per_share + pair_down_quote.fee_per_share
        pair_edge = paired_arbitrage_edge(
            pair_up_quote.average_price,
            pair_down_quote.average_price,
            paired_costs,
            self.config.safety_margin,
        )

        if pair_edge >= self.config.min_edge:
            combined_unit_cost = (
                pair_up_quote.total_cost_per_share + pair_down_quote.total_cost_per_share
            )
            quantity = self._cap_quantity(
                position,
                paired_quantity,
                combined_unit_cost,
                paired=True,
            )
            if quantity > 0:
                final_up_quote = quote_fill(
                    snapshot.up_asks, quantity, snapshot.taker_fee_rate
                )
                final_down_quote = quote_fill(
                    snapshot.down_asks, quantity, snapshot.taker_fee_rate
                )
                return [
                    TradeDecision(
                        snapshot.market_id,
                        "UP",
                        quantity,
                        final_up_quote.average_price,
                        fair_up,
                        pair_edge,
                        "paired arbitrage: combined executable cost is below payout",
                        final_up_quote.fee,
                    ),
                    TradeDecision(
                        snapshot.market_id,
                        "DOWN",
                        quantity,
                        final_down_quote.average_price,
                        fair_down,
                        pair_edge,
                        "paired arbitrage: combined executable cost is below payout",
                        final_down_quote.fee,
                    ),
                ]

        decisions: list[TradeDecision] = []
        for side, fair_value, preliminary_quote, asks in (
            ("UP", fair_up, up_quote, snapshot.up_asks),
            ("DOWN", fair_down, down_quote, snapshot.down_asks),
        ):
            adjusted_fair = self._inventory_adjusted_fair(
                fair_value, side, position, snapshot
            )
            costs = preliminary_quote.fee_per_share
            usable_edge = calculate_trade_edge(
                adjusted_fair,
                preliminary_quote.average_price,
                costs,
                self.config.safety_margin,
            )
            if usable_edge < self.config.min_edge:
                continue

            kelly_entry = min(preliminary_quote.total_cost_per_share, 1 - 1e-9)
            allocation = fractional_kelly(
                adjusted_fair, kelly_entry, self.config.kelly_fraction
            )
            cash_budget = min(
                self.account.cash * allocation,
                self.config.max_capital_per_market - position.capital_at_risk,
            )
            desired = min(
                preliminary_quote.quantity,
                cash_budget / preliminary_quote.total_cost_per_share,
            )
            quantity = self._cap_quantity(
                position,
                desired,
                preliminary_quote.total_cost_per_share,
                side=side,
            )
            if quantity > 0:
                final_quote = quote_fill(asks, quantity, snapshot.taker_fee_rate)
                final_edge = calculate_trade_edge(
                    adjusted_fair,
                    final_quote.average_price,
                    final_quote.fee_per_share,
                    self.config.safety_margin,
                )
                if final_edge < self.config.min_edge:
                    continue
                decisions.append(
                    TradeDecision(
                        snapshot.market_id,
                        side,
                        quantity,
                        final_quote.average_price,
                        adjusted_fair,
                        final_edge,
                        "directional fair value exceeds executable price after costs",
                        final_quote.fee,
                    )
                )

        return sorted(decisions, key=lambda decision: decision.usable_edge, reverse=True)[:1]

    def execute(self, decisions: list[TradeDecision]) -> list[TradeDecision]:
        executed: list[TradeDecision] = []
        for decision in decisions:
            debit = decision.total_cost
            if debit > self.account.cash + 1e-12:
                continue
            position = self.account.positions.setdefault(decision.market_id, Position())
            if position.capital_at_risk + debit > self.config.max_capital_per_market + 1e-12:
                continue
            self.account.cash -= debit
            position.add(
                decision.side,
                decision.quantity,
                decision.average_price,
                decision.fee,
            )
            self.account.total_fees += decision.fee
            self.account.trade_count += 1
            executed.append(decision)
        return executed

    def settle(self, market_id: str, winner: Side) -> float:
        position = self.account.positions.pop(market_id, Position())
        payout = position.up_quantity if winner == "UP" else position.down_quantity
        pnl = payout - position.capital_at_risk
        self.account.cash += payout
        self.account.realized_pnl += pnl
        return pnl

    def process(self, snapshot: MarketSnapshot) -> list[TradeDecision]:
        return self.execute(self.evaluate(snapshot))

    def _inventory_adjusted_fair(
        self,
        fair_value: float,
        side: Side,
        position: Position,
        snapshot: MarketSnapshot,
    ) -> float:
        signed_imbalance = position.directional_exposure
        if side == "DOWN":
            signed_imbalance *= -1
        penalty = inventory_penalty(
            signed_imbalance,
            self.config.inventory_lambda,
            snapshot.short_term_volatility,
            snapshot.seconds_remaining,
        )
        return min(max(fair_value - penalty, 1e-6), 1 - 1e-6)

    def _cap_quantity(
        self,
        position: Position,
        desired: float,
        price: float,
        *,
        side: Side | None = None,
        paired: bool = False,
    ) -> float:
        if desired <= 0 or price <= 0:
            return 0.0
        capital_room = self.config.max_capital_per_market - position.capital_at_risk
        cash_room = self.account.cash
        quantity = min(desired, capital_room / price, cash_room / price)
        if not paired and side is not None:
            current = position.directional_exposure
            if side == "UP":
                exposure_room = self.config.max_unhedged_shares - current
            else:
                exposure_room = self.config.max_unhedged_shares + current
            quantity = min(quantity, max(exposure_room, 0.0))
        return max(quantity, 0.0)
