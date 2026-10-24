// OpenStreetMap map (Leaflet), used when no Google Maps browser key is configured.
// It offers the small part of the Google Maps JavaScript API this app uses, so every map
// screen works the same with either provider. OpenStreetMap's tile policy asks for visible
// attribution and a configurable tile address (VITE_MAP_TILE_URL in .env).
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

const TILE_URL = import.meta.env.VITE_MAP_TILE_URL || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
  + ' · <a href="https://www.openstreetmap.org/fixthemap">Report a map issue</a>';

const toLeaflet = point => [Number(point.lat), Number(point.lng)];
const toEvent = latlng => ({ latLng: { lat: () => latlng.lat, lng: () => latlng.lng } });

class OsmMap {
  constructor(element, options) {
    // Zoom buttons go top right, where Google Maps has its controls; the app's legends sit top left.
    this.leaflet = L.map(element, { center: toLeaflet(options.center), zoom: options.zoom, zoomControl: false });
    L.control.zoom({ position: 'topright' }).addTo(this.leaflet);
    L.tileLayer(TILE_URL, { maxZoom: 19, attribution: ATTRIBUTION }).addTo(this.leaflet);
    // Map boxes change size with the panels beside them; Leaflet only notices window resizes.
    this.resizer = new ResizeObserver(() => this.leaflet.invalidateSize());
    this.resizer.observe(element);
  }

  // Not animated, like Google Maps: an animated pan followed by setZoom would zoom around a point
  // part-way there, and a size change during an animation moves the view off centre.
  setCenter(point) { this.leaflet.setView(toLeaflet(point), this.leaflet.getZoom(), { animate: false }); }
  setZoom(zoom) { this.leaflet.setZoom(zoom, { animate: false }); }
  getZoom() { return this.leaflet.getZoom(); }
  fitBounds(bounds, padding = 32) {
    if (bounds.points.length) this.leaflet.fitBounds(bounds.points.map(toLeaflet), { padding: [padding, padding], maxZoom: 16 });
  }
  setOptions(options) {
    if ('draggableCursor' in options) this.leaflet.getContainer().style.cursor = options.draggableCursor || '';
  }
  addListener(name, handler) {
    const listener = event => handler(event.latlng ? toEvent(event.latlng) : {});
    this.leaflet.on(name, listener);
    return { remove: () => this.leaflet.off(name, listener) };
  }
  destroy() {
    this.resizer.disconnect();
    this.leaflet.remove();
  }
}

class Overlay {
  constructor(layer, map) {
    this.layer = layer;
    this.map = null;
    this.setMap(map);
  }
  setMap(map) {
    if (map) this.layer.addTo(map.leaflet); else this.layer.remove();
    this.map = map || null;
  }
  addListener(name, handler) {
    const listener = event => handler(event.latlng ? toEvent(event.latlng) : {});
    this.layer.on(name, listener);
    return { remove: () => this.layer.off(name, listener) };
  }
}

// Markers are drawn as circles, which also avoids Leaflet's image-based default icon.
class Marker extends Overlay {
  constructor(options) {
    const icon = options.icon || {};
    const layer = L.circleMarker(toLeaflet(options.position), {
      radius: icon.scale || 8, color: icon.strokeColor || '#ffffff', weight: icon.strokeWeight ?? 2,
      fillColor: icon.fillColor || '#c4483b', fillOpacity: icon.fillOpacity ?? 1,
    });
    if (options.title) layer.bindTooltip(options.title);
    super(layer, options.map);
  }
  setPosition(point) { this.layer.setLatLng(toLeaflet(point)); }
}

class Circle extends Overlay {
  constructor(options) {
    super(L.circle(toLeaflet(options.center), {
      radius: options.radius, color: options.strokeColor, opacity: options.strokeOpacity, weight: options.strokeWeight,
      fillColor: options.fillColor, fillOpacity: options.fillOpacity, interactive: options.clickable !== false,
    }), options.map);
  }
  setCenter(point) { this.layer.setLatLng(toLeaflet(point)); }
  setRadius(radius) { this.layer.setRadius(radius); }
}

class Polyline extends Overlay {
  constructor(options) {
    super(L.polyline((options.path || []).map(toLeaflet), {
      color: options.strokeColor, opacity: options.strokeOpacity, weight: options.strokeWeight,
    }), options.map);
  }
}

class InfoWindow {
  constructor(options) { this.content = options.content; }
  open({ map, anchor, position }) {
    const at = anchor ? anchor.layer.getLatLng() : toLeaflet(position);
    L.popup().setLatLng(at).setContent(this.content).openOn(map.leaflet);
  }
}

class LatLngBounds {
  constructor() { this.points = []; }
  extend(point) { this.points.push(point); }
  getCenter() {
    const n = this.points.length || 1;
    return { lat: this.points.reduce((s, p) => s + Number(p.lat), 0) / n, lng: this.points.reduce((s, p) => s + Number(p.lng), 0) / n };
  }
}

export const osmMaps = {
  provider: 'openstreetmap',
  Map: OsmMap, Marker, Circle, Polyline, InfoWindow, LatLngBounds, SymbolPath: { CIRCLE: 'circle' },
  importLibrary: async () => ({ Map: OsmMap }),
};
