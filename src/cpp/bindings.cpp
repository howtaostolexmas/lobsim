// pybind11 glue: exposes lob::OrderBook to Python as lobsim._core.CoreOrderBook.
// Results cross the boundary as plain tuples; lobsim/fast.py turns them into
// the same event dataclasses the Python engine returns.
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <chrono>
#include <cstdint>
#include <vector>

#include "order_book.hpp"

namespace py = pybind11;
using lob::Event;
using lob::OrderBook;
using lob::Side;

namespace {

Side to_side(bool is_buy) { return is_buy ? Side::Buy : Side::Sell; }

py::list to_python(const std::vector<Event>& events) {
    py::list out;
    for (const Event& e : events) {
        switch (e.kind) {
            case 'A':
                out.append(py::make_tuple("A", e.id));
                break;
            case 'R':
                out.append(py::make_tuple("R", e.id, static_cast<int>(e.reason)));
                break;
            case 'T':
                out.append(py::make_tuple("T", e.maker, e.id, e.price, e.qty,
                                          e.aggressor == Side::Buy, e.maker_rem, e.taker_rem));
                break;
            case 'C':
                out.append(py::make_tuple("C", e.id, e.qty));
                break;
        }
    }
    return out;
}

// One pre-decoded op for run_ops. kind: 0 limit, 1 market, 2 cancel.
struct OpRec {
    int kind;
    int64_t id;
    bool is_buy;
    int64_t price;
    int64_t qty;
};

class CoreOrderBook {
public:
    py::list add_limit(int64_t id, bool is_buy, int64_t price, int64_t qty) {
        buf_.clear();
        book_.add_limit(id, to_side(is_buy), price, qty, buf_);
        return to_python(buf_);
    }

    py::list add_market(int64_t id, bool is_buy, int64_t qty) {
        buf_.clear();
        book_.add_market(id, to_side(is_buy), qty, buf_);
        return to_python(buf_);
    }

    py::list cancel(int64_t id) {
        buf_.clear();
        book_.cancel(id, buf_);
        return to_python(buf_);
    }

    std::optional<int64_t> best_bid() const { return book_.best_bid(); }
    std::optional<int64_t> best_ask() const { return book_.best_ask(); }
    std::vector<std::pair<int64_t, int64_t>> depth(bool is_buy, size_t n) const {
        return book_.depth(to_side(is_buy), n);
    }
    std::optional<int64_t> resting_qty(int64_t id) const { return book_.resting_qty(id); }

    // Benchmark helper: decode all ops first, then time only the engine,
    // entirely inside C++. Returns (event_count, elapsed_ns).
    std::pair<int64_t, int64_t> run_ops(const std::vector<OpRec>& ops) {
        std::vector<Event> events;
        events.reserve(ops.size() * 2);
        auto t0 = std::chrono::steady_clock::now();
        for (const OpRec& op : ops) {
            switch (op.kind) {
                case 0: book_.add_limit(op.id, to_side(op.is_buy), op.price, op.qty, events); break;
                case 1: book_.add_market(op.id, to_side(op.is_buy), op.qty, events); break;
                default: book_.cancel(op.id, events); break;
            }
        }
        auto t1 = std::chrono::steady_clock::now();
        auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(t1 - t0).count();
        return {static_cast<int64_t>(events.size()), static_cast<int64_t>(ns)};
    }

private:
    OrderBook book_;
    std::vector<Event> buf_;  // reused across calls to avoid an allocation per call
};

}  // namespace

namespace pybind11::detail {
// Let Python pass (kind, id, is_buy, price, qty) tuples as OpRec.
template <>
struct type_caster<OpRec> {
    PYBIND11_TYPE_CASTER(OpRec, const_name("OpTuple"));
    bool load(handle src, bool) {
        auto t = reinterpret_borrow<py::tuple>(src);
        value = OpRec{t[0].cast<int>(), t[1].cast<int64_t>(), t[2].cast<bool>(),
                      t[3].cast<int64_t>(), t[4].cast<int64_t>()};
        return true;
    }
};
}  // namespace pybind11::detail

PYBIND11_MODULE(_core, m) {
    m.doc() = "C++ order book engine for lobsim";
    py::class_<CoreOrderBook>(m, "CoreOrderBook")
        .def(py::init<>())
        .def("add_limit", &CoreOrderBook::add_limit)
        .def("add_market", &CoreOrderBook::add_market)
        .def("cancel", &CoreOrderBook::cancel)
        .def("best_bid", &CoreOrderBook::best_bid)
        .def("best_ask", &CoreOrderBook::best_ask)
        .def("depth", &CoreOrderBook::depth)
        .def("resting_qty", &CoreOrderBook::resting_qty)
        .def("run_ops", &CoreOrderBook::run_ops);
}
