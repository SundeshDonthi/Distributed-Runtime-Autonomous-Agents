// Phase 9: the project's one genuine C++ workload. `runtime/routing/
// neural.py` trains its logistic regression scorer with a hand-rolled batch
// gradient descent loop -- O(epochs * n_examples * n_features) of repeated
// dot products and gradient accumulation, re-run every time a new router
// model is trained (Section 15G.2's model lifecycle). That is a real,
// scaling numeric kernel, unlike a single candidate's inference call (one
// 5-feature dot product -- the project's own Phase 7 benchmark already
// measured that at ~1us either way, i.e. nothing for a compiled language to
// meaningfully win back once a Python<->native call's own overhead is
// counted). Training is where compiled, cache-friendly, interpreter-free
// arithmetic actually pays for itself, and it is the only kernel this file
// implements for that reason -- see `benchmarks/benchmark_cpp_router.py`
// for the measured, not assumed, speedup.
//
// This file is a line-for-line port of `runtime.routing.neural`'s Python
// reference implementation (standardization, batch gradient descent on
// binary cross-entropy, L2-regularized weight updates, the same clipped
// sigmoid) -- not a different algorithm. `tests/test_neural_cpp.py` checks
// both backends produce the same model (within floating-point tolerance) on
// identical input, which is the whole point: moving where the arithmetic
// runs, not what it computes.
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <vector>

namespace py = pybind11;

namespace {

double clip(double z, double bound = 35.0) {
    // Mirrors neural.py's `_clip`: keeps std::exp from overflowing on a
    // large |z| during early training iterations, before weights settle.
    return std::max(-bound, std::min(bound, z));
}

double sigmoid(double z) {
    return 1.0 / (1.0 + std::exp(-clip(z)));
}

}  // namespace

py::dict train_logistic_regression_native(
    const std::vector<std::vector<double>>& vectors,
    const std::vector<double>& labels,
    int epochs,
    double learning_rate,
    double l2
) {
    if (vectors.empty()) {
        throw std::invalid_argument("cannot train on an empty example set");
    }
    const std::size_t n = vectors.size();
    const std::size_t n_features = vectors.front().size();

    // Feature standardization (neural.py's `means`/`stds` computation),
    // including the same "a constant feature's std is substituted with 1.0,
    // not divided by zero" rule as the Python `... or 1.0` idiom.
    std::vector<double> means(n_features, 0.0);
    for (const auto& v : vectors) {
        for (std::size_t i = 0; i < n_features; ++i) means[i] += v[i];
    }
    for (std::size_t i = 0; i < n_features; ++i) means[i] /= static_cast<double>(n);

    std::vector<double> stds(n_features, 0.0);
    for (const auto& v : vectors) {
        for (std::size_t i = 0; i < n_features; ++i) {
            double d = v[i] - means[i];
            stds[i] += d * d;
        }
    }
    for (std::size_t i = 0; i < n_features; ++i) {
        stds[i] = std::sqrt(stds[i] / static_cast<double>(n));
        if (stds[i] == 0.0) stds[i] = 1.0;
    }

    std::vector<std::vector<double>> standardized(n, std::vector<double>(n_features));
    for (std::size_t r = 0; r < n; ++r) {
        for (std::size_t i = 0; i < n_features; ++i) {
            standardized[r][i] = (vectors[r][i] - means[i]) / stds[i];
        }
    }

    // Batch gradient descent on binary cross-entropy (neural.py's own
    // training loop, same hyperparameter semantics): one full pass over
    // every example per epoch, then one weight update from the accumulated
    // gradient -- not per-example SGD.
    std::vector<double> weights(n_features, 0.0);
    double bias = 0.0;

    for (int epoch = 0; epoch < epochs; ++epoch) {
        std::vector<double> grad_w(n_features, 0.0);
        double grad_b = 0.0;
        for (std::size_t r = 0; r < n; ++r) {
            double z = bias;
            for (std::size_t i = 0; i < n_features; ++i) {
                z += weights[i] * standardized[r][i];
            }
            double error = sigmoid(z) - labels[r];
            for (std::size_t i = 0; i < n_features; ++i) {
                grad_w[i] += error * standardized[r][i];
            }
            grad_b += error;
        }
        for (std::size_t i = 0; i < n_features; ++i) {
            weights[i] -= learning_rate * (grad_w[i] / static_cast<double>(n) + l2 * weights[i]);
        }
        bias -= learning_rate * (grad_b / static_cast<double>(n));
    }

    py::dict result;
    result["weights"] = weights;
    result["bias"] = bias;
    result["feature_means"] = means;
    result["feature_stds"] = stds;
    return result;
}

std::vector<double> predict_proba_batch_native(
    const std::vector<double>& weights,
    double bias,
    const std::vector<double>& means,
    const std::vector<double>& stds,
    const std::vector<std::vector<double>>& vectors
) {
    const std::size_t n_features = weights.size();
    std::vector<double> out;
    out.reserve(vectors.size());
    for (const auto& v : vectors) {
        double z = bias;
        for (std::size_t i = 0; i < n_features; ++i) {
            // Mirrors neural.py's `_standardize`: a near-zero std (never
            // actually hit once a trained scorer's stds have gone through
            // the `or 1.0` substitution above, but preserved here for an
            // exact match on a hand-constructed scorer in a test) maps to a
            // standardized feature of 0.0 rather than dividing by it.
            double s = stds[i] > 1e-9 ? (v[i] - means[i]) / stds[i] : 0.0;
            z += weights[i] * s;
        }
        out.push_back(sigmoid(z));
    }
    return out;
}

PYBIND11_MODULE(_native, m) {
    m.doc() =
        "Phase 9's C++ kernels for the neural router's training loop and "
        "batched inference. A line-for-line port of runtime.routing.neural's "
        "pure-Python reference implementation -- see that module's "
        "train_logistic_regression/predict_proba for the algorithm this "
        "must match, and tests/test_neural_cpp.py for the parity check.";
    m.def(
        "train_logistic_regression", &train_logistic_regression_native,
        py::arg("vectors"), py::arg("labels"), py::arg("epochs"),
        py::arg("learning_rate"), py::arg("l2")
    );
    m.def(
        "predict_proba_batch", &predict_proba_batch_native,
        py::arg("weights"), py::arg("bias"), py::arg("means"), py::arg("stds"),
        py::arg("vectors")
    );
}
