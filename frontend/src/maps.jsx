import { useEffect, useRef, useState } from 'react';

// Google Maps loading, shared by every map in the app.
const configuredKey = (import.meta.env.VITE_GOOGLE_MAPS_API_KEY || '').trim();
// The .env.example placeholder counts as "not configured".
const GOOGLE_MAPS_BROWSER_KEY = configuredKey.startsWith('REPLACE_') ? '' : configuredKey;
let googleMapsLoadPromise;
let googleAuthFailed = false;
const authFailureListeners = new Set();
const AUTH_FAILURE_MESSAGE = 'Google Maps rejected the browser key. Check the key, that Maps JavaScript API is enabled, and that this website is allowed in the key restrictions.';

export function loadGoogleMaps() {
  if (!GOOGLE_MAPS_BROWSER_KEY) return Promise.reject(new Error('Add VITE_GOOGLE_MAPS_API_KEY to the project .env file, then restart the website.'));
  // Google calls this global when the key is invalid or not allowed for this site.
  window.gm_authFailure = () => { googleAuthFailed = true; authFailureListeners.forEach(listener => listener()); };
  if (window.google?.maps?.importLibrary) return Promise.resolve(window.google.maps);
  if (!googleMapsLoadPromise) {
    googleMapsLoadPromise = new Promise((resolve, reject) => {
      const callbackName = '__flowstateGoogleMapsLoaded';
      window[callbackName] = () => { delete window[callbackName]; resolve(window.google.maps); };
      const script = document.createElement('script');
      script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(GOOGLE_MAPS_BROWSER_KEY)}&v=weekly&loading=async&callback=${callbackName}`;
      script.async = true;
      script.onerror = () => { delete window[callbackName]; googleMapsLoadPromise = null; reject(new Error('Google Maps failed to load. Check the browser key and Maps JavaScript API settings.')); };
      document.head.appendChild(script);
    });
  }
  return googleMapsLoadPromise;
}

export function useGoogleMap(containerRef, zoom = 11) {
  const mapRef = useRef(null);
  const [map, setMap] = useState(null);
  const [mapError, setMapError] = useState('');
  useEffect(() => {
    let active = true;
    const onAuthFailure = () => { if (active) setMapError(AUTH_FAILURE_MESSAGE); };
    authFailureListeners.add(onAuthFailure);
    if (googleAuthFailed) onAuthFailure();
    loadGoogleMaps().then(async maps => {
      const { Map } = await maps.importLibrary('maps');
      await maps.importLibrary('marker');
      if (!active || !containerRef.current) return;
      const instance = new Map(containerRef.current, {
        center: { lat: 12.9716, lng: 77.5946 }, zoom,
        mapTypeControl: false, streetViewControl: false, fullscreenControl: true,
        clickableIcons: false, gestureHandling: 'greedy',
      });
      mapRef.current = instance;
      setMap(instance);
    }).catch(error => { if (active) setMapError(error.message || 'Google Maps could not be loaded.'); });
    return () => { active = false; mapRef.current = null; authFailureListeners.delete(onAuthFailure); };
  }, []);
  return { map, mapRef, mapError };
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
