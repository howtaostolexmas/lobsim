#include <pybind11/pybind11.h>

namespace py = pybind11;

// Stub: the real engine arrives in the next commit.
struct CoreOrderBook {};

PYBIND11_MODULE(_core, m) {
    m.doc() = "C++ order book engine for lobsim";
    py::class_<CoreOrderBook>(m, "CoreOrderBook").def(py::init<>());
}
