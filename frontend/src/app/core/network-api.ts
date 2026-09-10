import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { SiteDemand } from './demand';
import { ChargingSite } from './site';

// Set by index.html's inline script: localhost:8090 for local dev (not docker-compose.yml's
// 8080 — this dev machine already has another project's backend on 8080, see
// backend/README.md), the real deployed Render origin otherwise. See docs/frontend-notes.md
// item 2, now resolved.
const API_BASE_URL = (window as any).__VOLTERRA_API_BASE__ ?? 'http://localhost:8090';

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
