import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Bell, Check, Clock3, KeyRound, LocateFixed, MapPin, Navigation, Radio, Route, Search, ShieldCheck, Square, TrendingUp, UserPlus, Users } from 'lucide-react';
import { publish } from './events.js';
import { CATEGORY_LABELS, METHOD_LABELS, VEHICLE_LABELS, api, dateTime, km, label, localDateTimeValue, minutes, patch, post, targetOptions } from './lib.js';
import { MapSetupNotice, infoContent, useLivePosition, useMap } from './maps.jsx';
import { ComponentBars, ConfidenceTag, LevelBadge, ProbabilityMeters, StatTile } from './viz.jsx';

function Heading({ eyebrow, title, subtitle, action }) {
  return <div className="page-heading"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{subtitle}</p></div>{action}</div>;
}

// ---------- places ----------

// Known places: registered roads that are on the map. They let every form work without Google search.
export function useKnownPlaces() {
  const [places, setPlaces] = useState([]);
  useEffect(() => {
    api('/api/segments').then(body => setPlaces(body.segments.filter(s => s.latitude != null).map(s => ({
      id: s.segment_id, label: `${s.road}, ${s.area}`, latitude: s.latitude, longitude: s.longitude, area: s.area, road: s.road,
    })))).catch(() => setPlaces([]));
  }, []);
  return places;
}

// A searched place, in the same shape as the known places.
const searchedPlace = result => ({
  id: `place:${result.latitude.toFixed(5)},${result.longitude.toFixed(5)}`,
  label: (result.name && result.display_name && !result.display_name.startsWith(result.name) ? `${result.name}, ${result.display_name}` : (result.display_name || result.name)).slice(0, 190),
  latitude: result.latitude, longitude: result.longitude,
});

// Search any place in Bengaluru by name. The search runs when the person presses Search or
// Enter, not on every keystroke (OpenStreetMap's usage policy does not allow search-as-you-type).
export function PlaceSearch({ onSelect, placeholder = 'Search a place, e.g. Avalahalli' }) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState(null);
  const [attribution, setAttribution] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  async function search() {
    if (query.trim().length < 3) { setMessage('Type at least 3 letters of the place name.'); return; }
    setBusy(true);
    setMessage('');
    setResults(null);
    try {
      const body = await api(`/api/geocode?q=${encodeURIComponent(query.trim())}`);
      setResults(body.results);
      setAttribution(body.attribution || '');
      if (!body.results.length) setMessage('No place with that name was found in Bengaluru.');
    } catch (err) {
      setMessage(err.message);
    } finally {
      setBusy(false);
    }
  }
  function choose(result) {
    const place = searchedPlace(result);
    setResults(null);
    setQuery(result.name || query);
    publish('focus-place', place);
    onSelect({ ...place, attribution });
  }
  return (
    <div className="place-search">
      <div className="search-box">
        <Search size={18} aria-hidden="true" />
        <input value={query} onChange={e => setQuery(e.target.value)} placeholder={placeholder} aria-label={placeholder}
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); search(); } }} />
        <button type="button" onClick={search} disabled={busy}>{busy ? 'Searching…' : 'Search'}</button>
      </div>
      {message && <small className="form-hint">{message}</small>}
      {results?.length > 0 && (
        <ul className="place-results">
          {results.map(result => (
            <li key={`${result.latitude},${result.longitude}`}>
              <button type="button" onClick={() => choose(result)}>
                <MapPin size={16} aria-hidden="true" />
                <span><b>{result.name || result.display_name}</b>{result.display_name && <small>{result.display_name}</small>}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {results?.length > 0 && attribution && <small className="place-attribution">{attribution}</small>}
    </div>
  );
}

export function PlacePicker({ label: title, places, value, onChange, pin }) {
  const [found, setFound] = useState(null);
  const extra = [found, pin && { id: 'pin', label: pin.label || 'Point chosen on the map', latitude: pin.latitude, longitude: pin.longitude }]
    .filter(Boolean);
  const options = [...extra, ...places.filter(p => !extra.some(e => e.id === p.id))];
  if (value && !options.some(p => p.id === value.id)) options.unshift(value);
  return (
    <div className="place-picker">
      <label>{title}
        <select required value={value?.id || ''} onChange={e => {
          const chosen = options.find(p => p.id === e.target.value) || null;
          onChange(chosen);
          if (chosen) publish('focus-place', chosen);
        }}>
          <option value="">Choose a place</option>
          {options.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>
      </label>
      <PlaceSearch placeholder={`Or search any place for the ${title.toLowerCase()}`} onSelect={place => { setFound(place); onChange(place); }} />
    </div>
  );
}

// ---------- road forecast ----------

export function ForecastCard({ forecast }) {
  if (!forecast) return null;
  if (forecast.error) return <div className="inline-error">{forecast.error}</div>;
  const s = forecast.signals || {};
  const evidence = [];
  if (s.closure) evidence.push('A road closure is in force nearby.');
  for (const i of s.incidents || []) evidence.push(`${CATEGORY_LABELS[i.category] || label(i.category)} ${i.distance_m} m away${i.official ? ' (official notice)' : ''}.`);
  if (s.weather) evidence.push(s.weather.wet ? `Rain is forecast (${s.weather.precipitation_mm ?? 0} mm).` : 'No rain is forecast.');
  if (s.planned_demand?.planned_trips) evidence.push(`${s.planned_demand.planned_trips} planned trip${s.planned_demand.planned_trips === 1 ? '' : 's'} (${s.planned_demand.pcu} PCU) expected in this block.`);
  if (s.camera) evidence.push(`Camera: ${s.camera.vehicle_count ?? '—'} vehicles, occupancy ${Math.round((s.camera.occupancy || 0) * 100)}% in the last measurement.`);
  if (s.note) evidence.push(s.note);
  return (
    <section className="card forecast-card">
      <div className="forecast-head">
        <div>
          <div className="eyebrow">{forecast.road.toUpperCase()} · {forecast.target_time_block.toUpperCase()} · {forecast.target_date}</div>
          <h2><LevelBadge value={forecast.predicted_class} /></h2>
        </div>
        <ConfidenceTag confidence={forecast.confidence} />
      </div>
      <ProbabilityMeters probabilities={forecast.probabilities} />
      <div className="forecast-method"><TrendingUp size={14} /> <b>{METHOD_LABELS[forecast.method] || forecast.method}</b><span>{forecast.method_label}</span></div>
      {forecast.predicted_values && (
        <div className="stat-row">
          <StatTile label="Forecast volume" value={Math.round(forecast.predicted_values.traffic_volume).toLocaleString('en-IN')} sub="vehicles in the block" />
          <StatTile label="Forecast speed" value={`${forecast.predicted_values.average_speed.toFixed(1)} km/h`} />
          <StatTile label="Travel time index" value={forecast.predicted_values.travel_time_index.toFixed(2)} />
        </div>
      )}
      {evidence.length > 0 && <ul className="evidence-list">{evidence.map(e => <li key={e}>{e}</li>)}</ul>}
      <p className="forecast-notice">{forecast.notice} {forecast.latest_observation ? `Latest observation for this road: ${forecast.latest_observation}.` : 'This road has no observations yet.'}</p>
    </section>
  );
}

export function RoadForecast({ segments }) {
  const roads = segments.filter(s => s.has_traffic_data || s.has_map_location);
  const areas = [...new Set(roads.map(s => s.area))].sort();
  const [area, setArea] = useState('');
  const [segmentId, setSegmentId] = useState('');
  const [when, setWhen] = useState('1h');
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setResult(null);
    try {
      const target = targetOptions().find(o => o.id === when).date;
      setResult(await api(`/api/forecast?segment_id=${encodeURIComponent(segmentId)}&target=${encodeURIComponent(target.toISOString())}`));
    } catch (err) {
      setResult({ error: err.message });
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Heading eyebrow="ROAD FORECAST" title="Check a road before you go" subtitle="Expected congestion for a road and time, with how the estimate was made." />
      <div className="model-warning"><TrendingUp size={17} /><span><b>Research estimate, not a live reading.</b> Each result names its method and confidence; roads without recent observations use their historical pattern.</span></div>
      <section className="card form-card">
        <form className="incident-form" onSubmit={submit}>
          <div className="form-pair">
            <label>Area<select required value={area} onChange={e => { setArea(e.target.value); setSegmentId(''); setResult(null); }}>
              <option value="">Choose an area</option>{areas.map(a => <option key={a}>{a}</option>)}</select></label>
            <label>Road or junction<select required value={segmentId} onChange={e => { setSegmentId(e.target.value); setResult(null); }}>
              <option value="">Choose a road</option>{roads.filter(s => s.area === area).map(s => <option key={s.segment_id} value={s.segment_id}>{s.road}</option>)}</select></label>
          </div>
          <label>When<select value={when} onChange={e => setWhen(e.target.value)}>{targetOptions().map(o => <option key={o.id} value={o.id}>{o.label}</option>)}</select></label>
          <button className="button button-primary" disabled={busy || !segmentId}><TrendingUp size={15} />{busy ? 'Estimating…' : 'Get forecast'}</button>
        </form>
      </section>
      <ForecastCard forecast={result} />
    </>
  );
}

// ---------- planned trips ----------

// Counts below the privacy minimum are shown only as "fewer than".
export function sameDestinationText(count) {
  if (!count) return '';
  const where = `within ${count.radius_m} m of your destination, arriving within ${count.window_minutes} minutes of you`;
  return count.travellers != null ? `${count.travellers} other travellers plan to arrive ${where}.` : `Fewer than ${count.fewer_than} other travellers plan to arrive ${where}.`;
}

export function PlanTrips({ setToast, places, pins }) {
  // Points chosen on the home screen (on the map or by search) are filled in.
  const [origin, setOrigin] = useState(pins?.origin ? { ...pins.origin, id: 'pin' } : null);
  const [destination, setDestination] = useState(pins?.destination ? { ...pins.destination, id: 'pin' } : null);
  const [departure, setDeparture] = useState(() => localDateTimeValue(new Date(Date.now() + 2 * 3600 * 1000)));
  const [vehicle, setVehicle] = useState('car');
  const [vehicles, setVehicles] = useState([]);
  const [trips, setTrips] = useState(null);
  const [busy, setBusy] = useState(false);
  const [busyTrip, setBusyTrip] = useState('');
  const load = () => api('/api/trips').then(body => setTrips(body.trips)).catch(err => setToast(err.message));
  useEffect(() => { load(); api('/api/demand/vehicle-types').then(b => setVehicles(b.vehicle_types)).catch(() => {}); }, []);
  // Other people's plans change, so the counts of travellers heading the same way are refreshed every minute.
  useEffect(() => {
    const timer = setInterval(() => api('/api/trips').then(body => setTrips(body.trips)).catch(() => {}), 60000);
    return () => clearInterval(timer);
  }, []);
  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    try {
      const created = await post('/api/trips', {
        origin: origin.label, destination: destination.label, origin_lat: origin.latitude, origin_lon: origin.longitude,
        destination_lat: destination.latitude, destination_lon: destination.longitude,
        departure_at: new Date(departure).toISOString(), vehicle_type: vehicle,
      });
      setToast(`Trip saved: it passes ${created.segments.length} monitored road${created.segments.length === 1 ? '' : 's'}. ${sameDestinationText(created.same_destination)}`);
      await load();
    } catch (err) {
      setToast(err.message);
    } finally {
      setBusy(false);
    }
  }
  async function reassess(trip) {
    setBusyTrip(trip.trip_id);
    try {
      const result = await post(`/api/trips/${trip.trip_id}/reassess`);
      setToast(result.notification_id ? 'Risk changed: a notification was sent.' : `Checked: route risk ${Math.round(result.risk_score)}/100.`);
      await load();
    } catch (err) { setToast(err.message); } finally { setBusyTrip(''); }
  }
  async function cancel(trip) {
    setBusyTrip(trip.trip_id);
    try { await patch(`/api/trips/${trip.trip_id}/cancel`, {}); setToast('Trip cancelled.'); await load(); }
    catch (err) { setToast(err.message); } finally { setBusyTrip(''); }
  }
  const pinOrigin = pins?.origin && { ...pins.origin, id: 'pin' };
  return (
    <>
      <Heading eyebrow="JOURNEY PLANNING" title="Plan a trip" subtitle="Save future trips. Your plans help forecast demand and you get alerts if conditions change."
        action={<span className="pill pill-neutral"><ShieldCheck size={14} /> Only you see your trips</span>} />
      <section className="card form-card">
        <form className="incident-form" onSubmit={submit}>
          <PlacePicker label="Starting point" places={places} value={origin} onChange={setOrigin} pin={pinOrigin} />
          <PlacePicker label="Destination" places={places} value={destination} onChange={setDestination} pin={pins?.destination && { ...pins.destination, id: 'pin' }} />
          <div className="form-pair">
            <label>Departure<input type="datetime-local" required value={departure} onChange={e => setDeparture(e.target.value)} /></label>
            <label>Vehicle<select value={vehicle} onChange={e => setVehicle(e.target.value)}>
              {(vehicles.length ? vehicles : Object.keys(VEHICLE_LABELS).map(id => ({ id }))).map(v => (
                <option key={v.id} value={v.id}>{VEHICLE_LABELS[v.id]}{v.pcu != null ? ` (${v.pcu} PCU)` : ''}</option>))}
            </select></label>
          </div>
          <small className="form-hint">PCU (passenger car units) weights vehicles by the road space they take, e.g. a bus counts as more than a car.</small>
          <button className="button button-primary" disabled={busy || !origin || !destination}><Route size={16} />{busy ? 'Saving…' : 'Save planned trip'}</button>
        </form>
      </section>
      <section className="card report-list-card">
        <div className="card-heading"><div><h2>My trips</h2><p>Upcoming trips are re-checked automatically as departure approaches.</p></div></div>
        {trips === null ? <div className="empty-state"><b>Loading…</b></div> : trips.length === 0 ? <div className="empty-state"><b>No trips yet</b><small>Planned trips appear here.</small></div> : (
          <div className="trip-list">{trips.map(trip => {
            const assessment = trip.last_assessment;
            return (
              <article className="trip-item" key={trip.trip_id}>
                <div className="trip-main">
                  <b>{trip.origin} → {trip.destination}</b>
                  <small><Clock3 size={12} /> {dateTime(trip.departure_at)} · {VEHICLE_LABELS[trip.vehicle_type] || 'Vehicle'} · {trip.pcu ?? 1} PCU · {km(trip.route_distance_m)} · {minutes(trip.route_duration_s)}</small>
                  <small>{trip.segments.length ? `Roads: ${trip.segments.map(s => s.road).join(', ')}` : 'No monitored roads on this route.'}{trip.route_source === 'straight_line_estimate' ? ' (estimated corridor)' : ''}</small>
                  {assessment && <small>Last check {dateTime(trip.last_assessed_at)}: risk {Math.round(assessment.risk_score)}/100 ({assessment.band}){assessment.closures?.length ? ' · closure on route' : ''}</small>}
                  {trip.same_destination && <span className="same-destination"><Users size={14} aria-hidden="true" /> {sameDestinationText(trip.same_destination)}</span>}
                </div>
                <span className={`status-chip ${trip.state === 'scheduled' ? 'status-blue' : trip.state === 'cancelled' ? 'status-gray' : 'status-green'}`}><i />{trip.state}</span>
                {trip.state === 'scheduled' && <div className="trip-actions">
                  <button className="button button-secondary" disabled={busyTrip === trip.trip_id} onClick={() => reassess(trip)}><Radio size={13} /> Check now</button>
                  <button className="button button-secondary" disabled={busyTrip === trip.trip_id} onClick={() => cancel(trip)}>Cancel</button>
                </div>}
              </article>
            );
          })}</div>
        )}
      </section>
    </>
  );
}

// ---------- notifications ----------

// Desktop alerts pop up while FlowState is open in a browser tab. Browsers allow them only on
// https:// addresses or on this computer's own address (127.0.0.1 or localhost).
export const desktopAlertsOn = () => {
  try { return localStorage.getItem('flowstateDesktopAlerts') === 'on'; } catch { return false; }
};

function DesktopAlerts() {
  const supported = typeof window !== 'undefined' && 'Notification' in window && window.isSecureContext;
  const [permission, setPermission] = useState(() => (supported ? Notification.permission : 'unsupported'));
  const [on, setOn] = useState(desktopAlertsOn);
  function remember(value) {
    try { localStorage.setItem('flowstateDesktopAlerts', value ? 'on' : 'off'); } catch { /* storage may be unavailable */ }
    setOn(value);
  }
  async function turnOn() {
    const result = await Notification.requestPermission();
    setPermission(result);
    if (result === 'granted') remember(true);
  }
  const text = permission === 'unsupported' ? 'This browser cannot show desktop alerts for this address. They need an https:// address or this computer\'s own address (127.0.0.1).'
    : permission === 'denied' ? 'Desktop alerts are blocked in this browser\'s settings for this site.'
    : permission === 'granted' && on ? 'Desktop alerts are on. New trip alerts pop up while FlowState is open in a browser tab.'
    : 'Get new trip alerts as desktop pop-ups while FlowState is open in a browser tab.';
  return (
    <section className="card desktop-alerts">
      <Bell size={20} aria-hidden="true" /><p>{text}</p>
      {supported && permission !== 'denied' && (permission === 'granted' && on
        ? <button className="button button-secondary" onClick={() => remember(false)}>Turn off desktop alerts</button>
        : <button className="button button-primary" onClick={turnOn}>Turn on desktop alerts</button>)}
    </section>
  );
}

export function NotificationsPage({ onChange }) {
  const [items, setItems] = useState(null);
  const load = () => api('/api/notifications').then(body => { setItems(body.notifications); onChange?.(body.unread); });
  useEffect(() => { load().catch(() => setItems([])); }, []);
  async function read(item) { await post(`/api/notifications/${item.notification_id}/read`); await load(); }
  async function readAll() { await post('/api/notifications/read-all'); await load(); }
  return (
    <>
      <Heading eyebrow="NOTIFICATIONS" title="Trip alerts" subtitle="An advance notice before each planned trip with the expected traffic, delay and better routes, plus alerts when a trip's risk rises or a disruption appears ahead on a journey."
        action={<button className="button button-secondary" onClick={readAll}><Check size={14} /> Mark all read</button>} />
      <DesktopAlerts />
      <section className="card report-list-card">
        {items === null ? <div className="empty-state"><b>Loading…</b></div> : items.length === 0 ? <div className="empty-state"><span><Bell size={22} /></span><b>No notifications</b><small>You will be told here if a planned trip needs attention.</small></div> : (
          <div className="alert-list">{items.map(item => (
            <article className={`notification ${item.read_at ? '' : 'notification-unread'}`} key={item.notification_id}>
              <span className="alert-severity">{item.kind === 'departure_reminder' ? <Clock3 size={15} /> : <AlertTriangle size={15} />}</span>
              <div>
                <b>{item.title}</b>
                <p>{item.message}</p>
                <small>{dateTime(item.created_at)}</small>
              </div>
              {!item.read_at && <button className="text-action" onClick={() => read(item)}>Mark read</button>}
            </article>
          ))}</div>
        )}
      </section>
    </>
  );
}

// ---------- account: authority access request ----------

export function AccountPage({ user, setToast }) {
  const [form, setForm] = useState({ full_name: '', organization: '', designation: '', official_email: '', employee_id: '', reason: '' });
  const [requests, setRequests] = useState([]);
  const [busy, setBusy] = useState(false);
  const load = () => api('/api/access-requests/mine').then(b => setRequests(b.requests)).catch(() => {});
  useEffect(() => { load(); }, []);
  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    try {
      const body = Object.fromEntries(Object.entries(form).map(([k, v]) => [k, v.trim() || null]));
      const result = await post('/api/access-requests', body);
      setToast(result.message);
      await load();
    } catch (err) { setToast(err.message); } finally { setBusy(false); }
  }
  const field = (key, title, props = {}) => <label>{title}<input value={form[key]} onChange={e => setForm({ ...form, [key]: e.target.value })} {...props} /></label>;
  return (
    <>
      <Heading eyebrow="ACCOUNT" title={user?.username || 'Account'} subtitle={`${user?.email ? `Signed in as ${user.email}. ` : ''}Commuter account. Authority access is granted only after an existing authority verifies your details.`} />
      <ChangePassword setToast={setToast} changedAt={user?.password_changed_at} />
      <section className="card form-card">
        <div className="card-heading"><div><h2>Request authority access</h2><p>For traffic police and city officials. Provide an official email or staff ID so the request can be verified.</p></div><UserPlus className="muted-icon" /></div>
        <form className="incident-form" onSubmit={submit}>
          <div className="form-pair">{field('full_name', 'Full name', { required: true, minLength: 3 })}{field('organization', 'Organisation', { required: true, minLength: 3 })}</div>
          <div className="form-pair">{field('designation', 'Designation', { required: true, minLength: 2 })}{field('employee_id', 'Staff ID (optional)')}</div>
          {field('official_email', 'Official email (optional)', { type: 'email' })}
          <label>Reason<textarea required minLength="20" rows="3" value={form.reason} onChange={e => setForm({ ...form, reason: e.target.value })} placeholder="Your role and why you need authority access" /></label>
          <button className="button button-primary" disabled={busy}><ShieldCheck size={15} />{busy ? 'Sending…' : 'Send request'}</button>
        </form>
      </section>
      {requests.length > 0 && <section className="card report-list-card"><div className="card-heading"><div><h2>My requests</h2><p>Sign in again after approval to use the authority console.</p></div></div>
        <div className="table-wrap"><table><thead><tr><th>Organisation</th><th>Designation</th><th>Status</th><th>Submitted</th><th>Note</th></tr></thead><tbody>
          {requests.map(r => <tr key={r.request_id}><td>{r.organization}</td><td>{r.designation}</td><td><span className={`status-chip ${r.status === 'approved' ? 'status-green' : r.status === 'rejected' ? 'status-orange' : 'status-gray'}`}><i />{r.status}</span></td><td>{dateTime(r.created_at)}</td><td>{r.review_note || '—'}</td></tr>)}
        </tbody></table></div></section>}
    </>
  );
}

// Changing the password signs out the account's other sessions; this one continues with a new token.
export function ChangePassword({ setToast, minLength = 10, changedAt }) {
  const [form, setForm] = useState({ current: '', next: '', repeat: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function submit(e) {
    e.preventDefault();
    setError('');
    if (form.next !== form.repeat) { setError('The two new passwords do not match.'); return; }
    setBusy(true);
    try {
      const result = await post('/api/auth/password', { current_password: form.current, new_password: form.next });
      sessionStorage.setItem('flowstateToken', result.access_token);
      setForm({ current: '', next: '', repeat: '' });
      setToast('Password changed. Other devices signed in to this account were signed out.');
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  }
  const input = (key, title, autoComplete) => (
    <label>{title}<input type="password" required autoComplete={autoComplete} minLength={key === 'current' ? 1 : minLength}
      value={form[key]} onChange={e => setForm({ ...form, [key]: e.target.value })} /></label>
  );
  return (
    <section className="card form-card">
      <div className="card-heading"><div><h2>Change password</h2><p>At least {minLength} characters. Other devices signed in to this account are signed out.{changedAt ? ` Last changed ${dateTime(changedAt)}.` : ''}</p></div><KeyRound className="muted-icon" /></div>
      <form className="incident-form" onSubmit={submit}>
        {input('current', 'Current password', 'current-password')}
        <div className="form-pair">{input('next', 'New password', 'new-password')}{input('repeat', 'Repeat the new password', 'new-password')}</div>
        {error && <div className="inline-error">{error}</div>}
        <button className="button button-primary" disabled={busy}><KeyRound size={15} />{busy ? 'Changing…' : 'Change password'}</button>
      </form>
    </section>
  );
}

// ---------- route comparison ----------

export function RoutePreviewMap({ routes, incidents = [] }) {
  const containerRef = useRef(null);
  const linesRef = useRef([]);
  const { map, api: maps, mapError } = useMap(containerRef, 12);
  useLivePosition(map, maps);
  useEffect(() => {
    if (!map) return;
    linesRef.current.forEach(item => item.setMap(null));
    linesRef.current = routes.map((route, index) => new maps.Polyline({
      map, path: (route.geometry?.coordinates || []).map(([lng, lat]) => ({ lat, lng })), geodesic: true,
      strokeColor: route.restricted ? '#d03b3b' : index === 0 ? '#087e83' : '#90aaa6', strokeOpacity: index === 0 ? 0.9 : 0.55, strokeWeight: index === 0 ? 6 : 4,
    }));
    for (const incident of incidents) {
      const marker = new maps.Marker({ map, position: { lat: incident.latitude, lng: incident.longitude }, title: CATEGORY_LABELS[incident.category] || incident.category });
      const info = new maps.InfoWindow({ content: infoContent(CATEGORY_LABELS[incident.category] || label(incident.category), [incident.road, incident.closure ? 'Road closed' : null]) });
      marker.addListener('click', () => info.open({ map, anchor: marker }));
      linesRef.current.push(marker);
    }
    const points = routes[0]?.geometry?.coordinates || [];
    if (points.length) {
      const bounds = new maps.LatLngBounds();
      points.forEach(([lng, lat]) => bounds.extend({ lat, lng }));
      map.fitBounds(bounds, 32);
    }
    return () => linesRef.current.forEach(item => item.setMap(null));
  }, [map, maps, routes, incidents]);
  return <><div className="route-map" ref={containerRef} /><MapSetupNotice message={mapError} /></>;
}

function RouteCard({ route, index }) {
  const band = route.risk_level || (route.risk_score >= 60 ? 'high' : route.risk_score >= 30 ? 'medium' : 'low');
  const c = route.components || {};
  const delay = route.traffic_delay_s;
  return (
    <article className={`card route-card ${index === 0 ? 'route-card-best' : ''}`}>
      <div className="route-card-head">
        <span className={`route-rank ${index === 0 ? 'route-rank-best' : ''}`}>{index === 0 ? <Check size={16} /> : index + 1}</span>
        <div className="route-choice-main"><b>{minutes(route.baseline_duration_s)} <small>· {km(route.distance_m)}</small></b>
          <span>{route.source_type === 'google_route' ? `Google traffic-aware travel time${delay != null ? ` · traffic adds about ${Math.round(delay / 60)} min` : ''}` : 'Estimated corridor (road routing unavailable)'}</span></div>
        <StatTile label="Route risk" value={`${Math.round(route.risk_score)}/100`} sub={`${band} risk`} tone={band} />
      </div>
      {route.restricted && <div className="inline-error"><AlertTriangle size={14} /> An official road closure is on this route when you would reach it.</div>}
      {route.reasons?.length > 0 && <div className="risk-reasons"><b>Why this risk</b><ul>{route.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul></div>}
      <ComponentBars items={[
        { label: c.traffic_delay != null ? 'Traffic (forecast or live)' : 'Traffic forecast', value: c.traffic ?? c.congestion },
        { label: 'Planned demand above usual', value: c.planned_demand },
        { label: 'Accidents, hazards and notices', value: c.incidents }, { label: 'Weather', value: c.weather },
      ]} />
      {route.roads?.length > 0 && <div className="road-steps">{route.roads.map(r => (
        <div className="road-step" key={r.segment_id} title={`${METHOD_LABELS[r.method] || r.method}, ${r.confidence} confidence`}>
          <LevelBadge value={r.predicted_class} compact /><span>{r.road}</span><small>{dateTime(r.passes_at)} · {METHOD_LABELS[r.method] || r.method}{r.planned_pcu ? ` · ${r.planned_pcu} planned PCU` : ''}</small>
        </div>))}</div>}
      {route.incidents?.length > 0 && <ul className="evidence-list">{route.incidents.map(i => <li key={i.incident_id}>{CATEGORY_LABELS[i.category] || label(i.category)} {i.distance_m} m from the route{i.official ? ' (official notice)' : ''}{i.closure ? ' · road closed' : ''}</li>)}</ul>}
      {route.data_gaps?.length > 0 && <small className="form-hint">{route.data_gaps.join(' ')}</small>}
    </article>
  );
}

// Advice above the routes: a safer route than the fastest, high risk everywhere, or leaving earlier.
const NOTE_ICONS = { safer_route: ShieldCheck, high_risk: AlertTriangle, leave_early: Clock3 };

function TravelNotes({ notes }) {
  if (!notes?.length) return null;
  return <div className="travel-notes">{notes.map(note => {
    const Icon = NOTE_ICONS[note.kind] || AlertTriangle;
    return <div className={`travel-note travel-note-${note.kind}`} key={note.kind}><Icon size={18} aria-hidden="true" /><span>{note.message}</span></div>;
  })}</div>;
}

function DepartureAdvice({ advice }) {
  if (!advice) return null;
  if (!advice.supported) return <div className="route-notice"><Clock3 size={15} /> {advice.reason}</div>;
  return (
    <section className="card advice-card">
      <div className="card-heading"><div><h2>Departure time</h2><p>{advice.message} Times are compared on forecasts, because live traffic is known only for now.</p></div><Clock3 className="muted-icon" /></div>
      <div className="table-wrap"><table><thead><tr><th>Leave at</th><th>Change</th><th>Forecast route risk</th></tr></thead><tbody>
        {advice.options.map(o => <tr key={o.departure_at} className={advice.best?.departure_at === o.departure_at ? 'row-highlight' : ''}><td>{dateTime(o.departure_at)}</td><td>{o.offset_minutes === 0 ? 'Your time' : `${o.offset_minutes > 0 ? '+' : ''}${o.offset_minutes} min`}</td><td>{Math.round(o.risk_score)}/100</td></tr>)}
      </tbody></table></div>
    </section>
  );
}

// Route checks are paid routing requests: automatic ones run at most every two minutes, plus every ten minutes.
const AUTO_CHECK_GAP_MS = 2 * 60 * 1000;
const PERIODIC_CHECK_MS = 10 * 60 * 1000;

export function JourneyMonitor({ route, destination, setToast, onReroute, onRouteChange }) {
  const [journey, setJourney] = useState(null);
  const [status, setStatus] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [follow, setFollow] = useState(true);
  const [offer, setOffer] = useState(null);
  const [checking, setChecking] = useState(false);
  const watchRef = useRef(null);
  const lastSent = useRef(0);
  const lastCheck = useRef(0);
  const positionRef = useRef(null);
  const followRef = useRef(true);
  followRef.current = follow;
  useEffect(() => () => {
    if (watchRef.current != null) navigator.geolocation?.clearWatch(watchRef.current);
    publish('live-position', null);
  }, []);
  useEffect(() => {
    if (!journey) return undefined;
    const timer = setInterval(() => checkRoutes(journey.journey_id, true), PERIODIC_CHECK_MS);
    return () => clearInterval(timer);
  }, [journey]);
  async function start() {
    if (!navigator.geolocation) { setToast('Location is not available in this browser.'); return; }
    try {
      const created = await post('/api/journeys', { coordinates: route.geometry.coordinates, destination_lat: destination.latitude, destination_lon: destination.longitude });
      setJourney(created);
      setStatus({ status: 'waiting', message: 'Waiting for your location…' });
      watchRef.current = navigator.geolocation.watchPosition(position => {
        const { latitude, longitude, accuracy } = position.coords;
        positionRef.current = { latitude, longitude, accuracy };
        publish('live-position', { latitude, longitude, accuracy, follow: followRef.current });
        send(created.journey_id, position);
      }, error => {
        setStatus({ status: 'error', message: error.code === 1 ? 'Location permission was denied, so the road ahead cannot be checked.' : 'Your location is unavailable right now; checks resume when it returns.' });
      }, { enableHighAccuracy: true, maximumAge: 15000, timeout: 30000 });
    } catch (err) { setToast(err.message); }
  }
  async function send(journeyId, position) {
    if (Date.now() - lastSent.current < 15000) return;  // at most one update every 15 s
    lastSent.current = Date.now();
    if (!navigator.onLine) { setStatus({ status: 'offline', message: 'You are offline; checks resume when the connection returns.' }); return; }
    try {
      const result = await post(`/api/journeys/${journeyId}/position`, { latitude: position.coords.latitude, longitude: position.coords.longitude, accuracy_m: position.coords.accuracy });
      setStatus(result);
      if (result.alerts?.length) { setAlerts(previous => [...result.alerts, ...previous]); setToast('Disruption ahead on your route.'); }
      // A hazard ahead or leaving the route: look for a better route from here straight away.
      if (result.suggestion && Date.now() - lastCheck.current > AUTO_CHECK_GAP_MS) checkRoutes(journeyId, true);
      if (result.status === 'arrived') stop(false);
    } catch (err) { setStatus({ status: 'error', message: err.message }); }
  }
  async function checkRoutes(journeyId, automatic) {
    const here = positionRef.current;
    if (!here) { if (!automatic) setToast('Waiting for your location before checking routes.'); return; }
    lastCheck.current = Date.now();
    setChecking(true);
    try {
      const result = await post(`/api/journeys/${journeyId}/better-route`, { latitude: here.latitude, longitude: here.longitude, accuracy_m: here.accuracy });
      if (result.better) setOffer(result);
      else if (!automatic) setToast(result.message);
    } catch (err) { if (!automatic) setToast(err.message); } finally { setChecking(false); }
  }
  async function switchRoute() {
    try {
      await post(`/api/journeys/${journey.journey_id}/route`, { coordinates: offer.route.geometry.coordinates });
      onRouteChange?.(offer.route);
      setOffer(null);
      setToast('Route updated. Follow the new route on the map.');
    } catch (err) { setToast(err.message); }
  }
  async function stop(notifyServer = true) {
    if (watchRef.current != null) navigator.geolocation.clearWatch(watchRef.current);
    watchRef.current = null;
    publish('live-position', null);
    if (notifyServer && journey) await post(`/api/journeys/${journey.journey_id}/end`).catch(() => {});
    setJourney(null);
    setOffer(null);
  }
  return (
    <section className="card journey-monitor">
      <div className="card-heading"><div><h2>Journey monitoring</h2><p>With your permission, your location is shown on the map and checked against the road ahead. Positions are not kept after the journey ends.</p></div><LocateFixed className="muted-icon" /></div>
      {!journey ? <button className="button button-primary" onClick={start}><Navigation size={15} /> Start monitoring this route</button> : (
        <>
          <div className="monitor-status"><span className="coverage-signal" /><b>{status?.status === 'on_route' ? `On route · ${status.progress_pct}% done · ${km(status.distance_remaining_m)} left` : status?.status === 'off_route' ? 'You appear to be off the planned route' : status?.status === 'low_accuracy' ? 'Waiting for a more accurate location' : status?.status === 'arrived' ? 'Arrived' : 'Monitoring'}</b><small>{status?.message}</small></div>
          {offer && <div className="route-offer" role="status"><b>{offer.message}</b><div className="route-offer-actions"><button className="button button-primary" onClick={switchRoute}><Route size={14} /> Switch to this route</button><button className="button button-secondary" onClick={() => setOffer(null)}>Keep my route</button></div></div>}
          {status?.suggestion && !offer && <div className="inline-error">{status.suggestion.message} <button className="text-action" onClick={() => onReroute?.(status.suggestion.from)}>Compare all routes from here</button></div>}
          {alerts.map(a => <div className="evidence-item" key={a.incident_id}><AlertTriangle size={14} /> {CATEGORY_LABELS[a.category] || label(a.category)} about {a.distance_ahead_m} m ahead{a.closure ? ' (road closed)' : ''}</div>)}
          <label className="checkbox"><input type="checkbox" checked={follow} onChange={e => setFollow(e.target.checked)} /> Keep the map on my location</label>
          <div className="monitor-actions">
            <button className="button button-secondary" disabled={checking} onClick={() => checkRoutes(journey.journey_id, false)}><Route size={14} />{checking ? 'Checking routes…' : 'Check for a better route'}</button>
            <button className="button button-secondary" onClick={() => stop(true)}><Square size={13} /> Stop monitoring</button>
          </div>
        </>
      )}
    </section>
  );
}

export function StartJourney({ setToast, places, pins, googleRouting }) {
  const [origin, setOrigin] = useState(pins?.origin ? { ...pins.origin, id: 'pin' } : null);
  const [destination, setDestination] = useState(pins?.destination ? { ...pins.destination, id: 'pin' } : null);
  const [departure, setDeparture] = useState('');
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [publicIncidents, setPublicIncidents] = useState([]);
  useEffect(() => { api('/api/incidents/public').then(b => setPublicIncidents(b.incidents)).catch(() => {}); }, []);
  async function compare(from = origin, to = destination) {
    if (!from || !to) { setError('Choose a starting point and a destination.'); return; }
    setBusy(true);
    setError('');
    setResult(null);
    try {
      await Promise.allSettled([post(`/api/weather/fetch?latitude=${from.latitude}&longitude=${from.longitude}`), post(`/api/weather/fetch?latitude=${to.latitude}&longitude=${to.longitude}`)]);
      const query = new URLSearchParams({ origin_lat: from.latitude, origin_lon: from.longitude, destination_lat: to.latitude, destination_lon: to.longitude });
      if (departure) query.set('departure_at', new Date(departure).toISOString());
      setResult({ ...(await api(`/api/routes?${query}`)), origin: from, destination: to });
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  }
  const best = result?.routes?.[0];
  return (
    <>
      <Heading eyebrow="JOURNEY START" title="Compare routes" subtitle="Routes are scored on traffic, planned demand, accidents and hazards, official notices, and weather, at the time you reach each road." />
      {!googleRouting && <div className="model-warning"><MapPin size={16} /><span><b>Google routing is not configured.</b> Routes are shown as estimated straight-line corridors until a Google Maps key is added.</span></div>}
      <section className="card form-card">
        <form className="incident-form" onSubmit={e => { e.preventDefault(); compare(); }}>
          <PlacePicker label="Starting point" places={places} value={origin} onChange={setOrigin} pin={pins?.origin && { ...pins.origin, id: 'pin' }} />
          <PlacePicker label="Destination" places={places} value={destination} onChange={setDestination} pin={pins?.destination && { ...pins.destination, id: 'pin' }} />
          <label>Departure <small>leave empty for now</small><input type="datetime-local" value={departure} onChange={e => setDeparture(e.target.value)} /></label>
          <button className="button button-primary" disabled={busy}><Route size={16} />{busy ? 'Comparing…' : 'Compare routes'}</button>
        </form>
        {error && <div className="inline-error">{error}</div>}
      </section>
      {best && (
        <>
          <TravelNotes notes={result.travel_notes} />
          <div className="card route-results-map"><RoutePreviewMap routes={result.routes} incidents={publicIncidents} /></div>
          <div className="route-card-list">{result.routes.map((route, index) => <RouteCard key={route.route_index} route={route} index={index} />)}</div>
          <DepartureAdvice advice={best.departure_advice} />
          <JourneyMonitor route={best} destination={result.destination} setToast={setToast}
            onReroute={from => { const point = { id: 'here', label: 'Current location', ...from }; setOrigin(point); compare(point, result.destination); }}
            onRouteChange={route => setResult(previous => ({ ...previous, routes: [route], travel_notes: [] }))} />
          <div className="route-notice"><AlertTriangle size={15} />{result.notice} <b>Confirm conditions before travelling.</b></div>
        </>
      )}
    </>
  );
}
