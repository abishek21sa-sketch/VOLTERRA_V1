"""
Great-circle distance between two real coordinates. Used to build the resilience graph's edges
— see resilience_graph.jl's module docstring for why a geographic proximity graph, not a real
road-network graph, is what's used here.
"""

const EARTH_RADIUS_MI = 3958.8

"""
    haversine_miles(lat1, lon1, lat2, lon2) -> Float64

Great-circle distance in miles between two (latitude, longitude) points in decimal degrees.
"""
function haversine_miles(lat1::Float64, lon1::Float64, lat2::Float64, lon2::Float64)
    φ1, φ2 = deg2rad(lat1), deg2rad(lat2)
    Δφ = deg2rad(lat2 - lat1)
    Δλ = deg2rad(lon2 - lon1)
    a = sin(Δφ / 2)^2 + cos(φ1) * cos(φ2) * sin(Δλ / 2)^2
    return 2 * EARTH_RADIUS_MI * atan(sqrt(a), sqrt(1 - a))
end
