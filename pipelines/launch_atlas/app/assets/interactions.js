/* Native select and map events feed Dash stores. Navigation needs no server round trip. */
(() => {
  // Plotly exposes the MapLibre instance after drawing. Disable its default
  // repeated worlds and fit one world when the world view or width changes.
  let mapFrame;
  function syncMap() {
    mapFrame = null;
    const graph = document.querySelector('#atlas-map-graph .js-plotly-plot');
    const map = graph?._fullLayout?.map?._subplot?.map;
    if (!graph) return;
    if (typeof graph.on === 'function' && !graph.atlasMapListener) {
      graph.on('plotly_afterplot', scheduleMap);
      graph.atlasMapListener = true;
    }
    if (!map) return;
    if (map.getRenderWorldCopies()) map.setRenderWorldCopies(false);
    if (map.getMinZoom() > -2) map.setMinZoom(-2);
    const frame = document.getElementById('atlas-map-graph');
    const meta = graph.layout.meta || {};
    frame.dataset.mapView = meta.atlas_map_view;
    const width = frame.clientWidth, height = frame.clientHeight;
    const box = map.getContainer().getBoundingClientRect();
    if (Math.abs(box.width - width) > 1 || Math.abs(box.height - height) > 1) return;
    const size = meta.atlas_map_view === 'world' ? `${width}:${height}` : '';
    const key = `${meta.atlas_map_revision}:${meta.atlas_map_view}:${size}`;
    if (width > 0 && map.atlasViewKey !== key) {
      map.atlasViewKey = key;
      const view = graph.layout.map;
      if (meta.atlas_map_view === 'world') {
        const latitudes = (graph.data?.[0]?.lat || []).filter(Number.isFinite);
        const south = latitudes.length ? Math.max(-85, Math.min(...latitudes) - 4) : -50;
        const north = latitudes.length ? Math.min(85, Math.max(...latitudes) + 4) : 70;
        const mercatorY = lat => .5 - Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360)) / (2 * Math.PI);
        const top = mercatorY(north), bottom = mercatorY(south);
        const latitude = 180 / Math.PI * Math.atan(Math.sinh(Math.PI * (1 - top - bottom)));
        const zoom = Math.min(Math.log2(width / 512), Math.log2(height / (512 * (bottom - top))));
        map.jumpTo({center: [0, latitude], zoom});
      } else {
        map.jumpTo({center: [view.center.lon, view.center.lat], zoom: view.zoom});
      }
    }
  }
  function scheduleMap() {
    if (!mapFrame) mapFrame = requestAnimationFrame(syncMap);
  }
  new MutationObserver(scheduleMap).observe(document.documentElement, {childList: true, subtree: true});
  window.addEventListener('resize', scheduleMap);
  function selectPage(name) {
    const root = document.getElementById('printed-launch-atlas');
    if (!root || !root.querySelector(`[data-chapter="${name}"]`)) return;
    root.querySelectorAll('[data-page]').forEach(button => {
      window.dash_clientside.set_props(button.id, {'aria-current': button.dataset.page === name ? 'page' : null});
    });
    root.querySelectorAll('[data-chapter]').forEach(page => {
      window.dash_clientside.set_props(page.id, {hidden: page.dataset.chapter !== name});
    });
    requestAnimationFrame(() => { window.dispatchEvent(new Event('resize')); scheduleMap(); });
  }
  document.addEventListener('click', event => {
    const tab = event.target.closest('[data-page]');
    if (tab) selectPage(tab.dataset.page);
    const marker = event.target.closest('[data-site-id]');
    if (marker && window.dash_clientside) {
      const select = document.getElementById('atlas-site-select');
      if (select) select.value = marker.dataset.siteId;
      window.dash_clientside.set_props('atlas-site-select-value', {data: marker.dataset.siteId});
    }
  });
  document.addEventListener('change', event => {
    const select = event.target.closest('select[data-store]');
    if (select && window.dash_clientside) {
      window.dash_clientside.set_props(select.dataset.store, {data: select.value});
    }
  });
})();
