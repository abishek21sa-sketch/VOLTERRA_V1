export interface ChargingSite {
  site_id: string;
  operator_id: string;
  name: string;
  city: string | null;
  state: string | null;
  latitude: number;
  longitude: number;
  stall_count: number;
  max_power_kw: number;
  access_restricted: boolean;
}
