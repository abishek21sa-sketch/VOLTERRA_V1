// Mirrors backend/internal/mlpredictions.SitePredictions / Prediction — see that package's doc
// comment for where this data comes from (ml/'s precomputed queue-risk surrogate model output,
// NOT the warehouse, NOT real observed demand — Tesla publishes none).

export interface QueueRiskPrediction {
  utilization: number;
  mean_wait_min: number;
  p_wait: number;
  mean_queue_length: number;
  p_wait_over_15min: number;
}

export interface SiteDemand {
  site_id: string;
  name: string;
  city: string | null;
  state: string | null;
  stall_count: number;
  max_power_kw: number;
  temp_c: number;
  // 'noaa_nws' = real current forecast reading; 'assumed_20c_no_real_forecast' = modeled
  // fallback for the 2 real sites without one — never presented as if it were observed.
  temp_source: 'noaa_nws' | 'assumed_20c_no_real_forecast';
  predictions: QueueRiskPrediction[];
}

// Matches ml/examples/predict_real_sites_grid.py's UTILIZATION_GRID exactly — the frontend
// selects among these precomputed levels rather than interpolating/fabricating intermediate ones.
export const UTILIZATION_GRID = [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95] as const;
