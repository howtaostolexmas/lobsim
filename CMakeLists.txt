cmake_minimum_required(VERSION 3.15...3.31)
project(${SKBUILD_PROJECT_NAME} LANGUAGES CXX)

set(PYBIND11_FINDPYTHON ON)
find_package(pybind11 CONFIG REQUIRED)

pybind11_add_module(_core src/cpp/bindings.cpp src/cpp/order_book.cpp)
target_compile_features(_core PRIVATE cxx_std_17)

install(TARGETS _core DESTINATION lobsim)
