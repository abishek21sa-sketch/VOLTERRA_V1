"""
Greenfield charging-network expansion MILP for VOLTERRA.

The model decides *when* to open candidate sites, how many stalls to add in each
planning period, and which corridor segments are served by which open sites. It
couples infrastructure capex, annual budgets, grid interconnection limits,
service capacity, and mandatory final-period coverage of critical corridors.

This is a planning model. Candidate-site costs, demand forecasts, and grid limits
must be supplied explicitly and are never presented as Tesla-published values.
"""

using JuMP
using HiGHS
import MathOptInterface as MOI

struct ExpansionSite
    site_id::String
    fixed_open_cost_usd::Float64
    stall_cost_usd::Float64
    charger_power_kw::Float64
    max_stalls::Int
    grid_capacity_kw::Vector{Float64}
end

struct CorridorDemand
    segment_id::String
    demand_sessions::Vector{Float64}
    critical::Bool
end

struct NetworkExpansionResult
    termination_status
    objective_usd::Float64
    opened_period::Dict{String,Int}
    stalls_by_period::Dict{String,Vector{Int}}
    assignments::Dict{Tuple{String,Int},String}
    unserved_sessions::Dict{Tuple{String,Int},Float64}
    annual_capex_usd::Vector{Float64}
end

"""
    optimize_network_expansion(sites, corridors, coverage; kwargs...)

`coverage[(segment_id, site_id)] == true` means the candidate site can satisfy
that corridor's geometric/service coverage rule. Each corridor is either assigned
to one eligible open site in a period or left unserved with an explicit penalty.
Critical corridors must be served in the final planning period.
"""
function optimize_network_expansion(
    sites::Vector{ExpansionSite},
    corridors::Vector{CorridorDemand},
    coverage::Dict{Tuple{String,String},Bool};
    annual_budget_usd::Vector{Float64},
    sessions_per_stall_per_period::Float64=900.0,
    unserved_penalty_usd_per_session::Float64=80.0,
    discount_rate::Float64=0.08,
    require_n_minus_one_critical::Bool=false,
)
    T = length(annual_budget_usd)
    T > 0 || throw(ArgumentError("annual_budget_usd must be non-empty"))
    sessions_per_stall_per_period > 0 || throw(ArgumentError("sessions_per_stall_per_period must be positive"))
    length(unique(s.site_id for s in sites)) == length(sites) || throw(ArgumentError("site ids must be unique"))
    length(unique(c.segment_id for c in corridors)) == length(corridors) || throw(ArgumentError("segment ids must be unique"))
    all(length(s.grid_capacity_kw) == T for s in sites) || throw(ArgumentError("every site needs one grid-capacity value per period"))
    all(length(c.demand_sessions) == T for c in corridors) || throw(ArgumentError("every corridor needs one demand value per period"))

    S = 1:length(sites)
    C = 1:length(corridors)
    P = 1:T

    model = Model(HiGHS.Optimizer)
    set_silent(model)

    @variable(model, open_now[S,P], Bin)              # opening occurs in period p
    @variable(model, add_stalls[S,P] >= 0, Int)
    @variable(model, assign[C,S,P], Bin)
    @variable(model, unserved[C,P], Bin)

    # A site can be opened at most once.
    @constraint(model, [s in S], sum(open_now[s,p] for p in P) <= 1)

    for s in S, p in P
        cumulative_open = sum(open_now[s,k] for k in 1:p)
        cumulative_stalls = sum(add_stalls[s,k] for k in 1:p)
        @constraint(model, add_stalls[s,p] <= sites[s].max_stalls * cumulative_open)
        @constraint(model, cumulative_stalls <= sites[s].max_stalls * cumulative_open)
        @constraint(model, sites[s].charger_power_kw * cumulative_stalls <= sites[s].grid_capacity_kw[p])
    end

    for c in C, p in P
        eligible = [s for s in S if get(coverage, (corridors[c].segment_id, sites[s].site_id), false)]
        @constraint(model, sum(assign[c,s,p] for s in eligible) + unserved[c,p] == 1)
        for s in S
            if s in eligible
                @constraint(model, assign[c,s,p] <= sum(open_now[s,k] for k in 1:p))
            else
                @constraint(model, assign[c,s,p] == 0)
            end
        end
    end

    # Site service capacity by period.
    for s in S, p in P
        cumulative_stalls = sum(add_stalls[s,k] for k in 1:p)
        @constraint(
            model,
            sum(corridors[c].demand_sessions[p] * assign[c,s,p] for c in C)
            <= sessions_per_stall_per_period * cumulative_stalls,
        )
    end

    # Annual capital budgets.
    @constraint(model, [p in P],
        sum(sites[s].fixed_open_cost_usd * open_now[s,p] + sites[s].stall_cost_usd * add_stalls[s,p] for s in S)
        <= annual_budget_usd[p]
    )

    # Critical corridors cannot remain uncovered at the horizon.
    for c in C
        if corridors[c].critical
            @constraint(model, unserved[c,T] == 0)
        end
    end

    # Optional N-1 critical-corridor resilience. At the final horizon,
    # critical demand must remain assignable with sufficient installed service
    # capacity after loss of any single candidate site. This is a planning
    # resilience gate, not a real-grid contingency certification.
    if require_n_minus_one_critical
        critical = [c for c in C if corridors[c].critical]
        K = 1:length(critical)
        @variable(model, contingency_assign[K,S,S], Bin)  # critical, serving site, outage site
        for (k,c) in enumerate(critical), h in S
            eligible = [s for s in S if s != h && get(coverage, (corridors[c].segment_id, sites[s].site_id), false)]
            isempty(eligible) && @constraint(model, 0 == 1)
            if !isempty(eligible)
                @constraint(model, sum(contingency_assign[k,s,h] for s in eligible) == 1)
            end
            for s in S
                if s in eligible
                    @constraint(model, contingency_assign[k,s,h] <= sum(open_now[s,q] for q in P))
                else
                    @constraint(model, contingency_assign[k,s,h] == 0)
                end
            end
        end
        for h in S, s in S
            cumulative_stalls = sum(add_stalls[s,q] for q in P)
            @constraint(
                model,
                sum(corridors[critical[k]].demand_sessions[T] * contingency_assign[k,s,h] for k in K)
                <= sessions_per_stall_per_period * cumulative_stalls,
            )
        end
    end

    discount(p) = 1.0 / (1.0 + discount_rate)^(p-1)
    @objective(model, Min,
        sum(discount(p) * (sites[s].fixed_open_cost_usd * open_now[s,p] + sites[s].stall_cost_usd * add_stalls[s,p]) for s in S, p in P)
        + sum(discount(p) * unserved_penalty_usd_per_session * corridors[c].demand_sessions[p] * unserved[c,p] for c in C, p in P)
    )

    optimize!(model)
    status = termination_status(model)
    if status != MOI.OPTIMAL && status != MOI.LOCALLY_SOLVED
        return NetworkExpansionResult(status, Inf, Dict{String,Int}(), Dict{String,Vector{Int}}(), Dict{Tuple{String,Int},String}(), Dict{Tuple{String,Int},Float64}(), zeros(T))
    end

    opened = Dict{String,Int}()
    stalls = Dict{String,Vector{Int}}()
    assignments = Dict{Tuple{String,Int},String}()
    unserved_sessions = Dict{Tuple{String,Int},Float64}()
    annual_capex = zeros(T)
    for s in S
        chosen = [p for p in P if value(open_now[s,p]) > 0.5]
        if !isempty(chosen)
            opened[sites[s].site_id] = first(chosen)
        end
        running = 0
        profile = Int[]
        for p in P
            running += round(Int, value(add_stalls[s,p]))
            push!(profile, running)
            annual_capex[p] += sites[s].fixed_open_cost_usd * value(open_now[s,p]) + sites[s].stall_cost_usd * value(add_stalls[s,p])
        end
        stalls[sites[s].site_id] = profile
    end
    for c in C, p in P
        chosen = [s for s in S if value(assign[c,s,p]) > 0.5]
        if !isempty(chosen)
            assignments[(corridors[c].segment_id,p)] = sites[first(chosen)].site_id
            unserved_sessions[(corridors[c].segment_id,p)] = 0.0
        else
            unserved_sessions[(corridors[c].segment_id,p)] = corridors[c].demand_sessions[p]
        end
    end

    return NetworkExpansionResult(
        status,
        objective_value(model),
        opened,
        stalls,
        assignments,
        unserved_sessions,
        annual_capex,
    )
end
