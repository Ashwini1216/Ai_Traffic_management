// Stand-in for the Google Maps JavaScript API, installed before the app loads.
// Like the real API, string InfoWindow content is inserted as HTML, so the tests can catch unsafe popups.
(() => {
  window.__info = [];
  window.__markers = [];
  window.__polylines = [];
  window.__circles = [];
  window.__maps = [];
  class Events {
    constructor() { this.listeners = {}; }
    addListener(name, fn) {
      (this.listeners[name] ||= []).push(fn);
      return { remove: () => { this.listeners[name] = (this.listeners[name] || []).filter(f => f !== fn); } };
    }
    trigger(name, event) { (this.listeners[name] || []).forEach(fn => fn(event)); }
  }
  class Map extends Events {
    constructor(el, opts) { super(); this.el = el; this.opts = opts; el.dataset.stubMap = '1'; window.__maps.push(this); }
    setOptions() {}
    setCenter(c) { this.center = c; }
    setZoom(z) { this.zoom = z; }
    fitBounds() {}
  }
  class Overlay extends Events {
    constructor(o) { super(); this.opts = o; this.map = o.map; }
    setMap(m) { this.map = m; }
  }
  class Marker extends Overlay { constructor(o) { super(o); window.__markers.push(this); } }
  class Circle extends Overlay { constructor(o) { super(o); window.__circles.push(this); } }
  class Polyline extends Overlay { constructor(o) { super(o); window.__polylines.push(this); } }
  class InfoWindow {
    constructor(o) {
      window.__info.push(typeof o.content === 'string' ? 'html-string' : 'dom-node');
      const host = document.createElement('div');
      if (typeof o.content === 'string') host.innerHTML = o.content; else host.appendChild(o.content);
      host.className = 'stub-info-window';
      host.style.display = 'none';
      document.body.appendChild(host);
    }
    open(options) { this.openedWith = options; }
  }
  class LatLngBounds {
    constructor() { this.points = []; }
    extend(p) { this.points.push(p); }
    getCenter() { return this.points[0]; }
  }
  window.google = { maps: { Map, Marker, Circle, InfoWindow, Polyline, LatLngBounds, SymbolPath: { CIRCLE: 0 },
    importLibrary: async name => (name === 'maps' ? { Map } : {}) } };
  // What is currently drawn on any map, for the checks.
  window.__live = () => ({
    circles: window.__circles.filter(c => c.map).length,
    markers: window.__markers.filter(m => m.map).length,
    polylines: window.__polylines.filter(p => p.map).length,
  });
})();
