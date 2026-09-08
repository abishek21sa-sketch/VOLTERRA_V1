import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { SiteDemand } from './demand';
import { ChargingSite } from './site';

const runtimeConfig = (globalThis as typeof globalThis & { __VOLTERRA_API_BASE__?: string }).__VOLTERRA_API_BASE__;
const localHost = typeof location !== 'undefined' &&
  (location.hostname === 'localhost' || location.hostname === '127.0.0.1');
export const API_BASE_URL = (runtimeConfig ||
  (localHost ? 'http://localhost:8090' : 'https://volterra-api.onrender.com')).replace(/\/$/, '');

@Injectable({ providedIn: 'root' })
export class NetworkApi {
  private readonly http = inject(HttpClient);

  getSites(): Observable<ChargingSite[]> {
    return this.http.get<ChargingSite[]>(`${API_BASE_URL}/api/sites`);
  }

  getDemand(): Observable<SiteDemand[]> {
    return this.http.get<SiteDemand[]>(`${API_BASE_URL}/api/demand`);
  }
}
