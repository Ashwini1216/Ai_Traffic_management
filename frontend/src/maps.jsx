import { useEffect, useRef, useState } from 'react';
import { subscribe } from './events.js';
import { API } from './lib.js';

// Map loading, shared by every map in the app. With a Google browser key the Google Maps
// JavaScript API is used. The key can be built into the website or, if the website was built
// before the key was added, supplied by the server at run time. Without any key, an
// OpenStreetMap map offers the same functions, so the app shows a map with no key at all.
// Google's terms do not allow Google route or place results on a non-Google map, so the
// OpenStreetMap map is only used when the server does not use Google either.
const configuredKey = (import.meta.env.VITE_GOOGLE_MAPS_API_KEY || '').trim();
// The .env.example placeholder counts as "not configured".
const GOOGLE_MAPS_BROWSER_KEY = configuredKey.startsWith('REPLACE_') ? '' : configuredKey;
let providerPromise;
let googleAuthFailed = false;
const authFailureListeners = new Set();
const AUTH_FAILURE_MESSAGE = 'Google Maps rejected the browser key. Check the key, that Maps JavaScript API is enabled, and that this website is allowed in the key restrictions.';

function loadGoogle(key) {
  // Google calls this global when the key is invalid or not allowed for this site.
  window.gm_authFailure = () => { googleAuthFailed = true; authFailureListeners.forEach(listener => listener()); };
  if (window.google?.maps?.importLibrary) return Promise.resolve(window.google.maps);
  return new Promise((resolve, reject) => {
    const callbackName = '__flowstateGoogleMapsLoaded';
    window[callbackName] = () => { delete window[callbackName]; resolve(window.google.maps); };
    const script = document.createElement('script');
    script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}&v=weekly&loading=async&callback=${callbackName}`;
    script.async = true;
    script.onerror = () => { delete window[callbackName]; reject(new Error('Google Maps failed to load. Check the browser key and Maps JavaScript API settings.')); };
    document.head.appendChild(script);
  });
}

async function chooseProvider() {
  if (GOOGLE_MAPS_BROWSER_KEY) return loadGoogle(GOOGLE_MAPS_BROWSER_KEY);
  const config = await fetch(`${API}/api/map/config`).then(r => (r.ok ? r.json() : {})).catch(() => ({}));
  if (config.google_maps_browser_key) return loadGoogle(config.google_maps_browser_key);
  if (config.google_routing_configured) {
    throw new Error('Add VITE_GOOGLE_MAPS_API_KEY to the project .env file (the server already uses a Google key), then restart the server.');
  }
  return (await import('./osm.js')).osmMaps;
}

export function loadMaps() {
  if (!providerPromise) providerPromise = chooseProvider().catch(error => { providerPromise = null; throw error; });
  return providerPromise;
}

// `api` is the map library in use (Google's, or the OpenStreetMap stand-in) for creating markers etc.
export function useMap(containerRef, zoom = 11) {
  const mapRef = useRef(null);
  const [map, setMap] = useState(null);
  const [api, setApi] = useState(null);
  const [mapError, setMapError] = useState('');
  useEffect(() => {
    let active = true;
    let instance = null;
    const onAuthFailure = () => { if (active) setMapError(AUTH_FAILURE_MESSAGE); };
    authFailureListeners.add(onAuthFailure);
    if (googleAuthFailed) onAuthFailure();
    loadMaps().then(async maps => {
      const { Map } = await maps.importLibrary('maps');
      await maps.importLibrary('marker');
      if (!active || !containerRef.current) return;
      instance = new Map(containerRef.current, {
        center: { lat: 12.9716, lng: 77.5946 }, zoom,
        mapTypeControl: false, streetViewControl: false, fullscreenControl: true,
        clickableIcons: false, gestureHandling: 'greedy',
      });
      mapRef.current = instance;
      setApi(maps);
      setMap(instance);
    }).catch(error => { if (active) setMapError(error.message || 'The map could not be loaded.'); });
    return () => {
      active = false;
      mapRef.current = null;
      authFailureListeners.delete(onAuthFailure);
      instance?.destroy?.();
    };
  }, []);
  return { map, mapRef, mapError, api };
}

// The traveller's position during a monitored journey: a blue dot inside its accuracy circle.
export function useLivePosition(map, maps) {
  useEffect(() => {
    if (!map || !maps) return undefined;
    let dot = null;
    let halo = null;
    const clear = () => { dot?.setMap(null); halo?.setMap(null); dot = null; halo = null; };
    const stop = subscribe('live-position', position => {
      if (!position) { clear(); return; }
      const at = { lat: position.latitude, lng: position.longitude };
      const radius = Math.max(10, position.accuracy || 30);
      if (!dot) {
        halo = new maps.Circle({ map, center: at, radius, strokeColor: '#1a73e8', strokeOpacity: 0.5, strokeWeight: 1, fillColor: '#1a73e8', fillOpacity: 0.12, clickable: false });
        dot = new maps.Marker({ map, position: at, title: 'You are here', icon: { path: maps.SymbolPath.CIRCLE, scale: 8, fillColor: '#1a73e8', fillOpacity: 1, strokeColor: '#fff', strokeWeight: 3 } });
      } else {
        dot.setPosition(at);
        halo.setCenter(at);
        halo.setRadius(radius);
      }
      if (position.follow) map.setCenter(at);
    });
    return () => { stop(); clear(); };
  }, [map, maps]);
}

export function MapSetupNotice({ message }) {
  return message ? <div className="google-map-setup-note" role="status"><b>Map setup needed</b><span>{message}</span></div> : null;
}

// A small text-only info window (content is set with textContent, never as HTML).
export function infoContent(title, lines = []) {
  const box = document.createElement('div');
  const heading = document.createElement('b');
  heading.textContent = title;
  box.append(heading);
  for (const line of lines.filter(Boolean)) {
    const row = document.createElement('div');
    row.textContent = line;
    box.append(row);
  }
  return box;
}
