"""NaiveOrderBook: a slow, obviously-correct reference engine.

It keeps every resting order in one plain list and finds the best maker by
scanning the whole list each time. No sorted containers, no queues, no
indexes. It exists only so property tests can compare the real engine
against something simple enough to trust by reading.
"""

from lobsim import Accepted, Cancelled, Rejected, Side, Trade


class NaiveOrderBook:
    def __init__(self):
        self.orders = []  # each: {"id", "side", "price", "qty", "seq"}
        self.seq = 0

    def add_limit(self, order_id, side, price, qty):
        reason = self._validate(order_id, qty, price)
        if reason:
            return [Rejected(order_id, reason)]
        trades, remaining = self._match(order_id, side, qty, price)
        if remaining > 0:
            self.seq += 1
            self.orders.append(
                {"id": order_id, "side": side, "price": price, "qty": remaining, "seq": self.seq}
            )
        return [Accepted(order_id), *trades]

    def add_market(self, order_id, side, qty):
        reason = self._validate(order_id, qty, None)
        if reason:
            return [Rejected(order_id, reason)]
        trades, remaining = self._match(order_id, side, qty, None)
        events = [Accepted(order_id), *trades]
        if remaining > 0:
            events.append(Cancelled(order_id, remaining))
        return events

    def cancel(self, order_id):
        for o in self.orders:
            if o["id"] == order_id:
                self.orders.remove(o)
                return [Cancelled(order_id, o["qty"])]
        return [Rejected(order_id, "unknown_id")]

    def best_bid(self):
        bids = [o["price"] for o in self.orders if o["side"] is Side.BUY]
        return max(bids) if bids else None

    def best_ask(self):
        asks = [o["price"] for o in self.orders if o["side"] is Side.SELL]
        return min(asks) if asks else None

    def depth(self, side, n):
        totals = {}
        for o in self.orders:
            if o["side"] is side:
                totals[o["price"]] = totals.get(o["price"], 0) + o["qty"]
        prices = sorted(totals, reverse=(side is Side.BUY))
        return [(p, totals[p]) for p in prices[:n]]

    def resting_qty(self, order_id):
        for o in self.orders:
            if o["id"] == order_id:
                return o["qty"]
        return None

    def _validate(self, order_id, qty, price):
        if qty <= 0:
            return "non_positive_qty"
        if price is not None and price <= 0:
            return "non_positive_price"
        if self.resting_qty(order_id) is not None:
            return "duplicate_id"
        return None

    def _match(self, taker_id, side, qty, limit_price):
        trades = []
        while qty > 0:
            maker = self._best_maker(side)
            if maker is None:
                break
            if limit_price is not None:
                if side is Side.BUY and maker["price"] > limit_price:
                    break
                if side is Side.SELL and maker["price"] < limit_price:
                    break
            fill = min(qty, maker["qty"])
            maker["qty"] -= fill
            qty -= fill
            trades.append(
                Trade(maker["id"], taker_id, maker["price"], fill, side, maker["qty"], qty)
            )
            if maker["qty"] == 0:
                self.orders.remove(maker)
        return trades, qty

    def _best_maker(self, taker_side):
        """Best opposite order: best price first, then earliest arrival."""
        makers = [o for o in self.orders if o["side"] is not taker_side]
        if not makers:
            return None
        if taker_side is Side.BUY:
            return min(makers, key=lambda o: (o["price"], o["seq"]))
        return min(makers, key=lambda o: (-o["price"], o["seq"]))
