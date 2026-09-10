# Frontend follow-ups (post `ng new`)

The Angular app in `frontend/` was generated with `ng new frontend --style=scss --routing=true
--skip-git --package-manager=npm --ssr=false` (Angular CLI 22), then built out for Phase 2 (see
`roadmap.md`) into the full-screen map interface. Routing was removed entirely — `app.routes.ts`
was deleted since there's deliberately no router-based navigation; see item 3 below.

## Done (Phase 2)

1. `maplibre-gl` installed; its CSS wired through `angular.json`'s `styles` array (a Sass `@use`
   of the node_modules CSS was tried first and dropped — the `angular.json` route is the reliable
   one for third-party CSS in this Angular CLI setup).
2. `NetworkApi` (`src/app/core/network-api.ts`) reads `API_BASE_URL` from
   `window.__VOLTERRA_API_BASE__`, set by an inline script in `index.html`: the real deployed
   Render origin for any non-localhost hostname, `http://localhost:8090` for local dev (not
   `docker-compose.yml`'s 8080, because this dev machine already has another project's backend on
   8080, see `backend/README.md`). `network-map.ts`'s `API_HINT` reads the same bridge. Resolved
   now that real deployment config exists — this was previously hardcoded to localhost, which
   silently broke the deployed site (confirmed live: every `/api/*` call from the actual compiled
   Vercel bundle was going to `localhost:8090` and failing with `net::ERR_BLOCKED_BY_CLIENT`, even
   though direct `fetch()` calls against the real Render origin — used for the "verified
   end-to-end" note below — worked fine and masked the bug).
4. Site dossier: `network-map/network-map.html`'s `<aside class="dossier">`, a slide-over panel
   (not a modal) triggered by clicking a marker. Shows name, city/state, stalls, power, operator,
   access.

## Done (Phase 6)

6. DEMAND layer: the first layer beyond LIVE NETWORK. A toggle button + utilization slider
   (`network-map.ts`'s `demandLayerEnabled`/`utilizationIndex` signals) re-colors the *same*
   markers LIVE NETWORK plots — not separate markers — confirming item 3 below's instinct that
   layers should toggle presentation of one shared marker set, not stack independent layers.
   Colors come from `GET /api/demand` (`backend/internal/mlpredictions`, reading a plain JSON file
   `ml/`'s trained surrogate model precomputed — see that package's doc comment for why this isn't
   a warehouse query or a live model call). Verified end-to-end against the real backend +
   real ml/ output (not just compiled): toggling the layer fires a real `GET /api/demand` (200
   OK), and a full site→demand join across all 17 real sites was confirmed correct via direct
   `fetch()` in the running page — the actual color-per-pin visual paint itself hit the same
   backgrounded-tab rendering gotcha below (`document.hidden` stayed `true` even after explicitly
   foregrounding the tab in this automation environment), so that specific pixel-level check
   wasn't directly screenshotted, consistent with Phase 2's map paint verification.

## Still open

3. ~~Layer model: only LIVE NETWORK exists~~ — DEMAND landed in Phase 6 (see above). CONGESTION /
   RESILIENCE / GRID / EXPANSION / SIMULATION layers are still ahead, gated on the modules that
   compute them (Phases 5/7-9 as applicable) or, for RESILIENCE specifically, on exposing
   `graph/`'s output the same way DEMAND exposes `ml/`'s.
5. Planning Mode: a distinct input state (budget/horizon/service-level fields) that changes what
   clicking "OPTIMIZE NETWORK" sends to the backend — gated on Phase 4 (facility-location
   optimizer existing at all).

## A rendering gotcha worth knowing

MapLibre's `load` event (and therefore anything gated on it, like the `network-map` component's
initial site fetch) depends on completing an actual WebGL render pass via
`requestAnimationFrame`. If the tab/pane is backgrounded (`document.hidden === true` — e.g. an
automation harness's browser pane that isn't actively displayed to a human), rAF-driven work is
suspended and `load` never fires — no error, it just silently never happens. Confirmed this
during Phase 2 development: WebGL, `fetch`, Web Workers, and marker DOM mechanics all verified
working independently, but the map only visibly renders once the pane/tab is actually foregrounded
and visible. Not a code bug — just don't burn time debugging "the map never loads" against a
backgrounded tab.
