// Shared helpers: API access, formatting, and labels used across the app.

export const API = import.meta.env.VITE_API_BASE || '';
export const BLOCKS = ['Midnight', 'Morning', 'Daytime', 'Noon', 'Evening', 'Night'];

export async function api(path, options = {}) {
  const token = typeof window !== 'undefined' ? sessionStorage.getItem('flowstateToken') : null;
  const isForm = typeof FormData !== 'undefined' && options.body instanceof FormData;
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: {
      ...(options.body && !isForm ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(errorMessage(body, response.status));
  return body;
}

// FastAPI validation errors arrive as a list of {loc, msg}; show their messages, not "[object Object]".
export function errorMessage(body, status) {
  const detail = body.detail?.message || body.detail || body.message;
  if (Array.isArray(detail)) return detail.map(item => String(item?.msg || item).replace(/^Value error, /, '')).join(' ');
  if (detail && typeof detail === 'object') return `Request failed (${status})`;
  return detail || `Request failed (${status})`;
}

export const post = (path, body) => api(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });
export const patch = (path, body) => api(path, { method: 'PATCH', body: JSON.stringify(body) });

// YYYY-MM-DD in the browser's own timezone (toISOString() would give the UTC date).
export const localISODate = (date = new Date()) => new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
// Value for <input type="datetime-local"> in the browser's timezone.
export const localDateTimeValue = (date = new Date()) => new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);

export const n = value => Number(value || 0).toLocaleString('en-IN');
export const pct = value => (value == null ? '—' : `${Math.round(Number(value) * 100)}%`);
export const shortDate = value => (value ? new Date(`${value}T00:00:00`).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' }) : '—');
export const longDate = value => (value ? new Date(`${value.slice(0, 10)}T00:00:00`).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) : '—');
export const dateTime = value => (value ? new Date(value).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—');
export const minutes = seconds => `${Math.round(Number(seconds || 0) / 60)} min`;
export const km = metres => `${(Number(metres || 0) / 1000).toFixed(1)} km`;
export const label = text => String(text || '').replaceAll('_', ' ').replace(/^./, c => c.toUpperCase());

export const CATEGORY_LABELS = {
  accident: 'Accident', breakdown: 'Vehicle breakdown', road_blockage: 'Road blockage', heavy_traffic: 'Heavy traffic',
  construction: 'Construction', waterlogging: 'Waterlogging', signal_failure: 'Signal failure', diversion: 'Diversion',
  other: 'Other', vip_movement: 'VIP movement', road_closure: 'Road closure', metro_construction: 'Metro construction',
  public_event: 'Public event', restriction: 'Restriction',
};
export const CITIZEN_CATEGORIES = ['heavy_traffic', 'accident', 'breakdown', 'road_blockage', 'construction', 'waterlogging', 'signal_failure', 'diversion', 'other'];
export const OFFICIAL_CATEGORIES = ['vip_movement', 'road_closure', 'diversion', 'metro_construction', 'construction', 'public_event', 'restriction', 'road_blockage', 'other'];

export const VEHICLE_LABELS = {
  two_wheeler: 'Two-wheeler', car: 'Car', auto_rickshaw: 'Auto-rickshaw', cab: 'Cab', bus: 'Bus', truck: 'Truck', bicycle: 'Bicycle',
};

export const METHOD_LABELS = {
  sequence_model: 'Hybrid LSTM → Random Forest', historical_pattern: 'Historical pattern', transfer: 'Similar roads',
  cold_start: 'Limited-data estimate',
};

export function readJson(key, fallback) {
  try { return JSON.parse(sessionStorage.getItem(key) || 'null') ?? fallback; } catch { return fallback; }
}
export function writeJson(key, value) {
  try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* storage may be unavailable */ }
}

// Target times offered when asking for a forecast.
export function targetOptions() {
  const now = new Date();
  const at = hours => new Date(now.getTime() + hours * 3600 * 1000);
  const tomorrow = hour => { const d = new Date(now); d.setDate(d.getDate() + 1); d.setHours(hour, 0, 0, 0); return d; };
  return [
    { id: 'now', label: 'Now', date: now },
    { id: '1h', label: 'In 1 hour', date: at(1) },
    { id: '3h', label: 'In 3 hours', date: at(3) },
    { id: 'tm9', label: 'Tomorrow 9:00', date: tomorrow(9) },
    { id: 'tm18', label: 'Tomorrow 18:00', date: tomorrow(18) },
  ];
}
