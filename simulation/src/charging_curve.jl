"""
Vehicle charging-curve and service-time model.

**MODELED, not observed** — Tesla does not publish per-vehicle charging curves at this
granularity (see ../../docs/data-sources.md). This is a piecewise-linear taper approximating
widely-observed real-world DC fast-charging behavior: full rated power up to roughly half state
of charge, then a linear taper down to a floor near 100% SOC. This is what makes charging
service times non-exponential and heterogeneous — the whole reason this project uses M/G/c
queueing and discrete-event simulation instead of a textbook M/M/c model (see queueing.jl).
"""

const TAPER_BREAKPOINT_SOC = 0.5   # SOC fraction where power tapering begins
const TAPER_FLOOR_FRACTION = 0.2   # fraction of rated power retained at 100% SOC

const COLD_DERATE_FLOOR_TEMP_C = -20.0
const COLD_DERATE_START_TEMP_C = 10.0
const COLD_DERATE_FLOOR_FRACTION = 0.5  # fraction of power retained at/below the floor temp

"""
    charging_power_fraction(soc) -> Float64

Fraction of rated charger power delivered at a given state of charge (`soc` in [0, 1]), before
temperature derating. Constant at 1.0 up to `TAPER_BREAKPOINT_SOC`, then linear down to
`TAPER_FLOOR_FRACTION` at soc = 1.0.
"""
function charging_power_fraction(soc::Float64)
    @assert 0.0 <= soc <= 1.0 "soc must be in [0, 1], got $soc"
    soc <= TAPER_BREAKPOINT_SOC && return 1.0
    frac_into_taper = (soc - TAPER_BREAKPOINT_SOC) / (1.0 - TAPER_BREAKPOINT_SOC)
    return 1.0 - frac_into_taper * (1.0 - TAPER_FLOOR_FRACTION)
end

"""
    temperature_derate(temp_c) -> Float64

Fraction of rated power retained due to cold-battery derating: 1.0 at/above
`COLD_DERATE_START_TEMP_C`, `COLD_DERATE_FLOOR_FRACTION` at/below `COLD_DERATE_FLOOR_TEMP_C`,
linear in between. Modeled — no real per-temperature Tesla data backs the exact breakpoints.
"""
function temperature_derate(temp_c::Float64)
    temp_c >= COLD_DERATE_START_TEMP_C && return 1.0
    temp_c <= COLD_DERATE_FLOOR_TEMP_C && return COLD_DERATE_FLOOR_FRACTION
    frac = (temp_c - COLD_DERATE_FLOOR_TEMP_C) / (COLD_DERATE_START_TEMP_C - COLD_DERATE_FLOOR_TEMP_C)
    return COLD_DERATE_FLOOR_FRACTION + frac * (1.0 - COLD_DERATE_FLOOR_FRACTION)
end

"""
    charge_duration_hours(soc_start, soc_target, battery_kwh, rated_power_kw; temp_c=20.0, n_steps=200) -> Float64

Time in hours to charge from `soc_start` to `soc_target`, numerically integrating
`dt = battery_kwh * d(soc) / power(soc)` where
`power(soc) = rated_power_kw * charging_power_fraction(soc) * temperature_derate(temp_c)`.

`rated_power_kw` should be the site's real `max_power_kw` (from the warehouse) — this function
doesn't separately model a vehicle-side acceptance-rate ceiling below the charger's rating.
"""
function charge_duration_hours(soc_start::Float64, soc_target::Float64, battery_kwh::Float64,
                                rated_power_kw::Float64; temp_c::Float64=20.0, n_steps::Int=200)
    @assert soc_target > soc_start "soc_target ($soc_target) must exceed soc_start ($soc_start)"
    derate = temperature_derate(temp_c)
    step = (soc_target - soc_start) / n_steps
    hours = 0.0
    for i in 1:n_steps
        soc_mid = soc_start + (i - 0.5) * step
        power_kw = rated_power_kw * charging_power_fraction(soc_mid) * derate
        power_kw = max(power_kw, 1.0)  # guard against near-zero power in pathological inputs
        hours += (battery_kwh * step) / power_kw
    end
    return hours
end
