"""
Analytical M/G/c queueing model for a single charging site.

`c` = stall count (REAL, from the warehouse). Arrivals are Poisson (rate λ, MODELED — see
docs/data-sources.md, Tesla does not publish session-level arrival data). Service time is
general/non-exponential (mean and variance supplied by the caller — see charging_curve.jl for
how a real service-time sample is derived from SOC/power/temperature assumptions).

Uses the Erlang-C formula plus the Allen-Cunneen approximation for M/G/c mean wait time — the
same combination the sibling AirlinesApp project's api/queue_pressure.py uses for a parallel
purpose (see its module docstring). Per this project's discipline, every returned metric is
reported as its own field — never blended into one invented "congestion score".
"""

struct QueueingResult
    utilization::Float64                  # ρ = offered load / c
    p_wait::Float64                       # Erlang-C: P(an arrival must wait at all)
    mean_wait_hours::Float64              # E[Wq], Allen-Cunneen approximation
    mean_queue_length::Float64            # Lq, via Little's Law (Lq = λ·Wq)
    mean_time_in_system_hours::Float64    # W = Wq + E[S]
    mean_number_in_system::Float64        # L, via Little's Law (L = λ·W)
end

"""
    erlang_c(c, offered_load) -> Float64

Erlang-C formula: P(an arriving customer must wait) for an M/M/c queue with `c` servers and
`offered_load` = λ·E[S] in Erlangs. Used here as the base waiting probability that the
Allen-Cunneen approximation then adjusts for non-exponential service.
"""
function erlang_c(c::Int, offered_load::Float64)
    @assert offered_load < c "offered load ($offered_load) must be below server count ($c) for a stable queue"
    terms = Float64[Float64(offered_load^k / factorial(big(k))) for k in 0:(c - 1)]
    sum_terms = sum(terms)
    last_term = Float64((offered_load^c / factorial(big(c))) * (c / (c - offered_load)))
    return last_term / (sum_terms + last_term)
end

"""
    mgc_queue(arrival_rate_per_hour, c, mean_service_hours, var_service_hours) -> QueueingResult

`arrival_rate_per_hour`: λ, vehicles/hour (MODELED).
`c`: stall_count (REAL).
`mean_service_hours`, `var_service_hours`: moments of a real (non-exponential) service-time
sample — see charging_curve.jl. The service-time variance is exactly what an M/M/c model would
ignore; carrying it through is the point of using M/G/c here.
"""
function mgc_queue(arrival_rate_per_hour::Float64, c::Int, mean_service_hours::Float64,
                    var_service_hours::Float64)
    offered_load = arrival_rate_per_hour * mean_service_hours
    rho = offered_load / c
    @assert rho < 1.0 "unstable queue: utilization $rho >= 1 (arrival rate exceeds site capacity)"

    p_wait = erlang_c(c, offered_load)
    wq_mmc = p_wait * mean_service_hours / (c * (1 - rho))

    scv_arrival = 1.0  # Poisson arrivals => exponential inter-arrival times, squared CV = 1
    scv_service = var_service_hours / mean_service_hours^2
    wq = wq_mmc * (scv_arrival + scv_service) / 2  # Allen-Cunneen correction

    lq = arrival_rate_per_hour * wq
    w = wq + mean_service_hours
    l = arrival_rate_per_hour * w

    return QueueingResult(rho, p_wait, wq, lq, w, l)
end

"""
    prob_wait_exceeds(result, threshold_hours) -> Float64

Approximate P(Wq > threshold): the conditional wait-time tail (given an arrival waits at all) is
treated as exponential with a rate calibrated to match the Allen-Cunneen mean wait. This is an
approximation — M/G/c has no exact closed-form waiting-time distribution — that corrects the
*mean* wait for general service but not the tail *shape*, which is a known limitation worth
stating rather than hiding behind a single number.
"""
function prob_wait_exceeds(result::QueueingResult, threshold_hours::Float64)
    result.p_wait <= 0.0 && return 0.0
    mean_wait_given_wait = result.mean_wait_hours / result.p_wait
    mean_wait_given_wait <= 0.0 && return 0.0
    rate = 1.0 / mean_wait_given_wait
    return result.p_wait * exp(-rate * threshold_hours)
end
