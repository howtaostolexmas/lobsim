#include "order_book.hpp"

#include <algorithm>
#include <iterator>

namespace lob {

namespace {

// Can an order on `side` with `limit_price` trade at `opposite_best`?
bool crosses(Side side, std::optional<int64_t> limit_price, int64_t opposite_best) {
    if (!limit_price) return true;  // market order: no price limit
    return side == Side::Buy ? *limit_price >= opposite_best : *limit_price <= opposite_best;
}

Event accepted(int64_t id) { return Event{'A', id}; }

Event rejected(int64_t id, Reason reason) {
    Event e{'R', id};
    e.reason = reason;
    return e;
}

Event cancelled(int64_t id, int64_t remaining) {
    Event e{'C', id};
    e.qty = remaining;
    return e;
}

}  // namespace

// ---- commands ---------------------------------------------------------------

void OrderBook::add_limit(int64_t id, Side side, int64_t price, int64_t qty,
                          std::vector<Event>& out) {
    if (auto reason = validate(id, qty, price)) {
        out.push_back(rejected(id, *reason));
        return;
    }
    out.push_back(accepted(id));
    int64_t remaining = match(id, side, qty, price, out);
    if (remaining > 0) rest(Order{id, side, price, remaining});
}

void OrderBook::add_market(int64_t id, Side side, int64_t qty, std::vector<Event>& out) {
    if (auto reason = validate(id, qty, std::nullopt)) {
        out.push_back(rejected(id, *reason));
        return;
    }
    out.push_back(accepted(id));
    int64_t remaining = match(id, side, qty, std::nullopt, out);
    if (remaining > 0) out.push_back(cancelled(id, remaining));  // market orders never rest
}

void OrderBook::cancel(int64_t id, std::vector<Event>& out) {
    auto found = index_.find(id);
    if (found == index_.end()) {
        out.push_back(rejected(id, Reason::UnknownId));
        return;
    }
    Locator loc = found->second;
    index_.erase(found);

    auto& levels = side_levels(loc.side);
    auto level_it = levels.find(loc.price);
    Level& level = level_it->second;
    int64_t remaining = loc.it->qty;
    level.total -= remaining;
    level.orders.erase(loc.it);  // O(1): we already hold the list iterator
    if (level.orders.empty()) levels.erase(level_it);
    out.push_back(cancelled(id, remaining));
}

// ---- queries ----------------------------------------------------------------

std::optional<int64_t> OrderBook::best_bid() const {
    if (bids_.empty()) return std::nullopt;
    return bids_.rbegin()->first;
}

std::optional<int64_t> OrderBook::best_ask() const {
    if (asks_.empty()) return std::nullopt;
    return asks_.begin()->first;
}

std::vector<std::pair<int64_t, int64_t>> OrderBook::depth(Side side, size_t n) const {
    std::vector<std::pair<int64_t, int64_t>> result;
    auto add = [&](const auto& entry) {
        if (result.size() < n) result.emplace_back(entry.first, entry.second.total);
    };
    if (side == Side::Buy) {
        for (auto it = bids_.rbegin(); it != bids_.rend() && result.size() < n; ++it) add(*it);
    } else {
        for (auto it = asks_.begin(); it != asks_.end() && result.size() < n; ++it) add(*it);
    }
    return result;
}

std::optional<int64_t> OrderBook::resting_qty(int64_t id) const {
    auto found = index_.find(id);
    if (found == index_.end()) return std::nullopt;
    return found->second.it->qty;
}

// ---- internals --------------------------------------------------------------

std::optional<Reason> OrderBook::validate(int64_t id, int64_t qty,
                                          std::optional<int64_t> price) const {
    if (qty <= 0) return Reason::NonPositiveQty;
    if (price && *price <= 0) return Reason::NonPositivePrice;
    if (index_.count(id)) return Reason::DuplicateId;
    return std::nullopt;
}

// Same algorithm as OrderBook._match in Python: walk the opposite side from
// the best price, oldest order first (price-time priority).
int64_t OrderBook::match(int64_t taker, Side side, int64_t qty,
                         std::optional<int64_t> limit_price, std::vector<Event>& out) {
    auto& opposite = side == Side::Buy ? asks_ : bids_;
    int64_t remaining = qty;
    while (remaining > 0 && !opposite.empty()) {
        // Best ask is the lowest price (begin); best bid is the highest (last).
        auto level_it = side == Side::Buy ? opposite.begin() : std::prev(opposite.end());
        int64_t best = level_it->first;
        if (!crosses(side, limit_price, best)) break;

        Level& level = level_it->second;
        Order& maker = level.orders.front();
        int64_t fill = std::min(remaining, maker.qty);
        maker.qty -= fill;
        level.total -= fill;
        remaining -= fill;

        Event trade{'T', taker};
        trade.maker = maker.id;
        trade.price = best;
        trade.qty = fill;
        trade.aggressor = side;
        trade.maker_rem = maker.qty;
        trade.taker_rem = remaining;
        out.push_back(trade);

        if (maker.qty == 0) {
            index_.erase(maker.id);    // use maker before the pop destroys it
            level.orders.pop_front();
            // Erase the level last: after this, level_it and level are invalid,
            // and the next loop iteration looks the best level up again.
            if (level.orders.empty()) opposite.erase(level_it);
        }
    }
    return remaining;
}

void OrderBook::rest(const Order& order) {
    Level& level = side_levels(order.side)[order.price];  // creates an empty level if new
    level.orders.push_back(order);
    level.total += order.qty;
    index_[order.id] = Locator{order.side, order.price, std::prev(level.orders.end())};
}

}  // namespace lob
