"""Phase 11 support script: one real queue-risk prediction per invocation, for backend/'s copilot
tool (see ../../backend/internal/copilot) to shell out to.

**Why a subprocess, not a live model-serving endpoint.** Same reasoning as
predict_real_sites_grid.py's module docstring for why the DEMAND layer precomputes a file: no
cross-language runtime bridge from Go to Python/LightGBM exists in this project. Unlike that
layer's fixed 17-site x 10-utilization grid (small, cacheable, computed once), the copilot needs
arbitrary (stall_count, max_power_kw, temp_c, utilization) queries a user might ask about — not
precomputable in advance. A synchronous subprocess call is the pragmatic middle ground: confirmed
by direct measurement during Phase 11 development to take roughly 10-20 real seconds end to end,
variable across runs (mostly Python/LightGBM import and model-loading overhead, not the actual
prediction, which is microseconds) — real, tolerable latency for an interactive copilot tool call,
unlike the ~53 seconds a warm Julia one-shot subprocess needs (see graph/examples/export_resilience_json.jl's
docstring), which is why THAT data is precomputed to a file instead.

Usage:
    python examples/predict_one.py <stall_count> <max_power_kw> <temp_c> <utilization>

Prints one JSON object to stdout: the real (modeled) queue-risk prediction, or a JSON error object
on invalid input -- never a Python traceback, so the calling Go process can always parse stdout.
"""

from __future__ import annotations

import json
import sys

from volterra_ml.queue_risk import load_models, predict_queue_risk


def main() -> int:
    if len(sys.argv) != 5:
        print(json.dumps({"error": "usage: predict_one.py <stall_count> <max_power_kw> <temp_c> <utilization>"}))
        return 1

    try:
        stall_count = int(sys.argv[1])
        max_power_kw = float(sys.argv[2])
        temp_c = float(sys.argv[3])
        utilization = float(sys.argv[4])
    except ValueError as e:
        print(json.dumps({"error": f"invalid numeric argument: {e}"}))
        return 1

    if not (0.0 < utilization < 1.0):
        print(json.dumps({"error": f"utilization must be in (0, 1), got {utilization}"}))
        return 1
    if stall_count <= 0 or max_power_kw <= 0:
        print(json.dumps({"error": "stall_count and max_power_kw must be positive"}))
        return 1

    try:
        models = load_models()
    except FileNotFoundError as e:
        print(json.dumps({"error": str(e)}))
        return 1

    result = predict_queue_risk(models, stall_count=stall_count, max_power_kw=max_power_kw,
                                 temp_c=temp_c, utilization=utilization)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
