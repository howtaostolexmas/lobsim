// C++ order book engine. Mirrors src/lobsim/order_book.py structure for
// structure, so the two can be read side by side.
#pragma once

#include <cstdint>
#include <list>
#include <map>
#include <optional>
#include <unordered_map>
#include <utility>
#include <vector>

namespace lob {

enum class Side : uint8_t { Buy, Sell };

// Same order as the Python reason strings; lobsim/fast.py maps code -> string.
enum class Reason : uint8_t { NonPositiveQty, NonPositivePrice, DuplicateId, UnknownId };

struct Order {
    int64_t id;
    Side side;
    int64_t price;
    int64_t qty;
};

// All resting orders at one price, oldest first, plus their total qty.
// std::list because erasing from the middle (cancel) is O(1) and, unlike
// std::vector, erasing never moves other elements, so stored iterators stay valid.
struct Level {
    std::list<Order> orders;
    int64_t total = 0;
};

// One engine event. Which fields matter depends on `kind`:
//   'A' accepted:  id
//   'R' rejected:  id, reason
//   'T' trade:     maker, id (= taker), price, qty, aggressor, maker_rem, taker_rem
//   'C' cancelled: id, qty (= remaining)
struct Event {
    char kind;
    int64_t id = 0;
    int64_t maker = 0;
    int64_t price = 0;
    int64_t qty = 0;
    int64_t maker_rem = 0;
    int64_t taker_rem = 0;
    Side aggressor = Side::Buy;
    Reason reason = Reason::UnknownId;
};

class OrderBook {
public:
    // Commands append their events to `out`. Passing the vector in lets a
    // caller reuse one buffer instead of allocating a new vector per call.
    void add_limit(int64_t id, Side side, int64_t price, int64_t qty, std::vector<Event>& out);
    void add_market(int64_t id, Side side, int64_t qty, std::vector<Event>& out);
    void cancel(int64_t id, std::vector<Event>& out);

    std::optional<int64_t> best_bid() const;
    std::optional<int64_t> best_ask() const;
    std::vector<std::pair<int64_t, int64_t>> depth(Side side, size_t n) const;
    std::optional<int64_t> resting_qty(int64_t id) const;

private:
    // Where a resting order lives, so cancel can jump straight to it: O(1).
    struct Locator {
        Side side;
        int64_t price;
        std::list<Order>::iterator it;
    };

    // std::map keeps prices sorted ascending. Best ask = begin(); best bid =
    // last element. Same idea as Python: no negated prices, no custom comparator.
    std::map<int64_t, Level> bids_;
    std::map<int64_t, Level> asks_;
    std::unordered_map<int64_t, Locator> index_;

    std::map<int64_t, Level>& side_levels(Side side) { return side == Side::Buy ? bids_ : asks_; }
    std::optional<Reason> validate(int64_t id, int64_t qty, std::optional<int64_t> price) const;
    int64_t match(int64_t taker, Side side, int64_t qty, std::optional<int64_t> limit_price,
                  std::vector<Event>& out);
    void rest(const Order& order);
};

}  // namespace lob
