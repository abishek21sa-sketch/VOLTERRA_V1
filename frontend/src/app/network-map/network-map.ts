import { DecimalPipe } from '@angular/common';
import { AfterViewInit, Component, ElementRef, OnDestroy, ViewChild, computed, inject, signal } from '@angular/core';
import * as maplibregl from 'maplibre-gl';
import type { Map as MapLibreMap, Marker } from 'maplibre-gl';

import { QueueRiskPrediction, SiteDemand, UTILIZATION_GRID } from '../core/demand';
import { API_BASE_URL, NetworkApi } from '../core/network-api';
import { ChargingSite } from '../core/site';

// Free vector basemap requiring no API key/token — swap for a hosted style once one exists.
const BASEMAP_STYLE_URL = 'https://tiles.openfreemap.org/styles/liberty';

const US_CENTER: [number, number] = [-98.5, 39.5];
const US_INITIAL_ZOOM = 3.6;

// Matches NetworkApi's API_BASE_URL — surfaced here only for the connection-error message.
const API_HINT = API_BASE_URL;

const DEFAULT_UTILIZATION_INDEX = 5; // UTILIZATION_GRID[5] === 0.75

// p_wait_over_15min color scale for the DEMAND layer. 0.30 saturates to full red rather than 1.0
// -- a p_wait_over_15min above ~0.30 already means "roughly 1 in 3 arrivals wait over 15
// minutes", already a severe risk worth flagging as maximally red. The real predicted range across
// all 17 sites x 10 utilization levels is 0.0-0.64 (checked against ml/data/queue_risk_predictions.json
// as of 2026-08-27); stretching the scale out to 1.0 would compress that real spread into a
// narrow, hard-to-distinguish band near the low end instead of clearly separating low- from
// high-risk sites.
const RISK_SATURATION_POINT = 0.3;
const RISK_LOW_COLOR = { r: 0x2e, g: 0xa0, b: 0x4a }; // green
const RISK_HIGH_COLOR = { r: 0xe0, g: 0x36, b: 0x1f }; // red (matches the existing .site-pin red)

@Component({
  selector: 'app-network-map',
  imports: [DecimalPipe],
  templateUrl: './network-map.html',
  styleUrl: './network-map.scss',
})
export class NetworkMap implements AfterViewInit, OnDestroy {
  @ViewChild('mapContainer', { static: true }) private readonly mapContainer!: ElementRef<HTMLDivElement>;

  private readonly api = inject(NetworkApi);
  private map: MapLibreMap | undefined;
  private markerEntries: { site: ChargingSite; el: HTMLDivElement; marker: Marker }[] = [];

  protected readonly siteCount = signal<number | null>(null);
  protected readonly loadError = signal<string | null>(null);
  protected readonly selectedSite = signal<ChargingSite | null>(null);

  protected readonly demandLayerEnabled = signal(false);
  protected readonly demandData = signal<SiteDemand[] | null>(null);
  protected readonly demandLoading = signal(false);
  protected readonly demandError = signal<string | null>(null);
  protected readonly copilotOpen = signal(false);
  protected readonly copilotMessage = signal('');
  protected readonly copilotAnswer = signal('Ask a question to generate a governed network readout.');
  protected readonly copilotProvider = signal('deterministic fallback');
  protected readonly copilotLoading = signal(false);
  protected readonly utilizationIndex = signal(DEFAULT_UTILIZATION_INDEX);
  protected readonly utilizationGrid = UTILIZATION_GRID;

  private readonly demandBySiteId = computed(() => {
    const byId = new Map<string, SiteDemand>();
    for (const d of this.demandData() ?? []) byId.set(d.site_id, d);
    return byId;
  });

  protected readonly selectedSiteDemand = computed<QueueRiskPrediction | null>(() => {
    const site = this.selectedSite();
    if (!site) return null;
    const demand = this.demandBySiteId().get(site.site_id);
    return demand ? demand.predictions[this.utilizationIndex()] : null;
  });

  protected readonly selectedSiteTempSource = computed<string | null>(() => {
    const site = this.selectedSite();
    if (!site) return null;
    return this.demandBySiteId().get(site.site_id)?.temp_source ?? null;
  });

  ngAfterViewInit(): void {
    const map = new maplibregl.Map({
      container: this.mapContainer.nativeElement,
      style: BASEMAP_STYLE_URL,
      center: US_CENTER,
      zoom: US_INITIAL_ZOOM,
    });
    map.addControl(new maplibregl.NavigationControl(), 'top-right');
    map.on('load', () => this.loadSites());

    this.map = map;
  }

  ngOnDestroy(): void {
    this.map?.remove();
  }

  private loadSites(): void {
    this.api.getSites().subscribe({
      next: (sites) => {
        this.siteCount.set(sites.length);
        this.plotSites(sites);
      },
      error: (err) => {
        this.loadError.set(
          `Could not reach the VOLTERRA API (${API_HINT}) — is the backend running? (${err.message ?? err})`,
        );
      },
    });
  }

  private plotSites(sites: ChargingSite[]): void {
    if (!this.map) return;

    for (const entry of this.markerEntries) entry.marker.remove();
    this.markerEntries = [];

    for (const site of sites) {
      const el = document.createElement('div');
      el.className = 'site-pin';
      el.style.setProperty('--pin-size', `${8 + Math.sqrt(site.stall_count) * 2}px`);

      const marker = new maplibregl.Marker({ element: el })
        .setLngLat([site.longitude, site.latitude])
        .addTo(this.map);

      el.addEventListener('click', () => this.selectedSite.set(site));
      this.markerEntries.push({ site, el, marker });
    }

    this.applyDemandStyling();
  }

  protected closeDossier(): void {
    this.selectedSite.set(null);
  }

  protected toggleDemandLayer(): void {
    const next = !this.demandLayerEnabled();
    this.demandLayerEnabled.set(next);
    if (next && this.demandData() === null && !this.demandLoading()) {
      this.loadDemand();
    }
    this.applyDemandStyling();
  }

  protected onUtilizationIndexChange(index: number): void {
    this.utilizationIndex.set(index);
    this.applyDemandStyling();
  }

  private loadDemand(): void {
    this.demandLoading.set(true);
    this.demandError.set(null);
    this.api.getDemand().subscribe({
      next: (data) => {
        this.demandData.set(data);
        this.demandLoading.set(false);
        this.applyDemandStyling();
      },
      error: (err) => {
        this.demandLoading.set(false);
        this.demandError.set(`Could not load queue-risk predictions (${err.message ?? err})`);
      },
    });
  }

  private applyDemandStyling(): void {
    const enabled = this.demandLayerEnabled();
    const byId = this.demandBySiteId();
    const utilIndex = this.utilizationIndex();

    for (const { site, el } of this.markerEntries) {
      if (!enabled) {
        el.style.removeProperty('--pin-color');
        continue;
      }
      const demand = byId.get(site.site_id);
      const risk = demand?.predictions[utilIndex]?.p_wait_over_15min ?? null;
      el.style.setProperty('--pin-color', risk === null ? '#8f98a3' : riskColor(risk));
    }
  }

  protected closeDemandError(): void {
    this.demandError.set(null);
  }

  protected async askCopilot(): Promise<void> {
    const message = this.copilotMessage().trim();
    if (!message || this.copilotLoading()) return;
    this.copilotLoading.set(true); this.copilotAnswer.set('Computing…');
    try {
      const response = await fetch(`${API_BASE_URL}/api/copilot`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tier: 'public', message }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error ?? 'Copilot unavailable');
      this.copilotAnswer.set(data.answer ?? 'No visible answer returned.');
      this.copilotProvider.set(`${data.provider ?? 'copilot'} / ${data.model ?? 'unknown'}`);
    } catch (error) { this.copilotAnswer.set(`Copilot error: ${error instanceof Error ? error.message : String(error)}`); }
    finally { this.copilotLoading.set(false); }
  }
}

function riskColor(pWaitOver15Min: number): string {
  const t = Math.min(1, Math.max(0, pWaitOver15Min / RISK_SATURATION_POINT));
  const r = Math.round(RISK_LOW_COLOR.r + t * (RISK_HIGH_COLOR.r - RISK_LOW_COLOR.r));
  const g = Math.round(RISK_LOW_COLOR.g + t * (RISK_HIGH_COLOR.g - RISK_LOW_COLOR.g));
  const b = Math.round(RISK_LOW_COLOR.b + t * (RISK_HIGH_COLOR.b - RISK_LOW_COLOR.b));
  return `rgb(${r}, ${g}, ${b})`;
}
