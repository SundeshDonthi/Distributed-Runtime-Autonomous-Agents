"""Builds Phase 9's one C++ extension: `runtime.routing._native`, the
compiled training/inference kernels behind `runtime.routing.neural`'s
`backend="cpp"` option. Everything else in this project is pure Python; this
is a build step only for that one module, and is entirely optional at
runtime (`neural.py` falls back to its pure-Python implementation if the
extension was never built -- Section 15A.1's "optional at runtime" pattern,
same as Redis/MCP/distributed workers elsewhere in this project).

Build it in place with:
    python setup.py build_ext --inplace
"""
from pybind11.setup_helpers import Pybind11Extension, build_ext
from setuptools import setup

ext_modules = [
    Pybind11Extension(
        "runtime.routing._native",
        ["native/router_native.cpp"],
        cxx_std=17,
    ),
]

setup(
    name="distributed-runtime-native",
    version="0.1.0",
    ext_modules=ext_modules,
    cmdclass={"build_ext": build_ext},
)
