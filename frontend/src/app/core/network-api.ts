import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { SiteDemand } from './demand';
import { ChargingSite } from './site';

// 8090, not docker-compose.yml's 8080 — this dev machine already has another project's backend
// on 8080 (see backend/README.md). Not yet wired to a build-time env var — see
// docs/frontend-notes.md item 2 for the follow-up once real deployment config exists.
const API_BASE_URL = 'http://localhost:8090';

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
