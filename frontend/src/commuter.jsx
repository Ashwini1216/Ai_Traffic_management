import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Bell, Check, Clock3, LocateFixed, MapPin, Navigation, Radio, Route, ShieldCheck, Square, TrendingUp, UserPlus } from 'lucide-react';
import { CATEGORY_LABELS, METHOD_LABELS, VEHICLE_LABELS, api, dateTime, km, label, localDateTimeValue, minutes, patch, post, targetOptions } from './lib.js';
import { MapSetupNotice, infoContent, useGoogleMap } from './maps.jsx';
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

export function PlacePicker({ label: title, places, value, onChange, pin }) {
  const options = pin ? [{ id: 'pin', label: pin.label || 'Point chosen on the map', latitude: pin.latitude, longitude: pin.longitude }, ...places] : places;
  return (
    <label>{title}
      <select required value={value?.id || ''} onChange={e => onChange(options.find(p => p.id === e.target.value) || null)}>
        <option value="">Choose a place</option>
        {options.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
      </select>
    </label>
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

export function PlanTrips({ setToast, places, pins }) {
  const [origin, setOrigin] = useState(null);
  const [destination, setDestination] = useState(null);
  const [departure, setDeparture] = useState(() => localDateTimeValue(new Date(Date.now() + 2 * 3600 * 1000)));
  const [vehicle, setVehicle] = useState('car');
  const [vehicles, setVehicles] = useState([]);
  const [trips, setTrips] = useState(null);
  const [busy, setBusy] = useState(false);
  const [busyTrip, setBusyTrip] = useState('');
  const load = () => api('/api/trips').then(body => setTrips(body.trips)).catch(err => setToast(err.message));
  useEffect(() => { load(); api('/api/demand/vehicle-types').then(b => setVehicles(b.vehicle_types)).catch(() => {}); }, []);
  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    try {
      const created = await post('/api/trips', {
        origin: origin.label, destination: destination.label, origin_lat: origin.latitude, origin_lon: origin.longitude,
        destination_lat: destination.latitude, destination_lon: destination.longitude,
        departure_at: new Date(departure).toISOString(), vehicle_type: vehicle,
      });
      setToast(`Trip saved: it passes ${created.segments.length} monitored road${created.segments.length === 1 ? '' : 's'}.`);
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

export function NotificationsPage({ onChange }) {
  const [items, setItems] = useState(null);
  const load = () => api('/api/notifications').then(body => { setItems(body.notifications); onChange?.(body.unread); });
  useEffect(() => { load().catch(() => setItems([])); }, []);
  async function read(item) { await post(`/api/notifications/${item.notification_id}/read`); await load(); }
  async function readAll() { await post('/api/notifications/read-all'); await load(); }
  return (
    <>
      <Heading eyebrow="NOTIFICATIONS" title="Trip alerts" subtitle="Sent when the risk on a planned trip rises materially, or a disruption appears ahead on a journey."
        action={<button className="button button-secondary" onClick={readAll}><Check size={14} /> Mark all read</button>} />
      <section className="card report-list-card">
        {items === null ? <div className="empty-state"><b>Loading…</b></div> : items.length === 0 ? <div className="empty-state"><span><Bell size={22} /></span><b>No notifications</b><small>You will be told here if a planned trip needs attention.</small></div> : (
          <div className="alert-list">{items.map(item => (
            <article className={`notification ${item.read_at ? '' : 'notification-unread'}`} key={item.notification_id}>
              <span className="alert-severity"><AlertTriangle size={15} /></span>
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

// ---------- route comparison ----------

export function RoutePreviewMap({ routes, incidents = [] }) {
  const containerRef = useRef(null);
  const linesRef = useRef([]);
  const { map, mapError } = useGoogleMap(containerRef, 12);
  useEffect(() => {
    if (!map) return;
    linesRef.current.forEach(item => item.setMap(null));
    linesRef.current = routes.map((route, index) => new window.google.maps.Polyline({
      map, path: (route.geometry?.coordinates || []).map(([lng, lat]) => ({ lat, lng })), geodesic: true,
      strokeColor: route.restricted ? '#d03b3b' : index === 0 ? '#087e83' : '#90aaa6', strokeOpacity: index === 0 ? 0.9 : 0.55, strokeWeight: index === 0 ? 6 : 4,
    }));
    for (const incident of incidents) {
      const marker = new window.google.maps.Marker({ map, position: { lat: incident.latitude, lng: incident.longitude }, title: CATEGORY_LABELS[incident.category] || incident.category });
      const info = new window.google.maps.InfoWindow({ content: infoContent(CATEGORY_LABELS[incident.category] || label(incident.category), [incident.road, incident.closure ? 'Road closed' : null]) });
      marker.addListener('click', () => info.open({ map, anchor: marker }));
      linesRef.current.push(marker);
    }
    const points = routes[0]?.geometry?.coordinates || [];
    if (points.length) {
      const bounds = new window.google.maps.LatLngBounds();
      points.forEach(([lng, lat]) => bounds.extend({ lat, lng }));
      map.fitBounds(bounds, 32);
    }
    return () => linesRef.current.forEach(item => item.setMap(null));
  }, [map, routes, incidents]);
  return <><div className="route-map" ref={containerRef} /><MapSetupNotice message={mapError} /></>;
}

function RouteCard({ route, index }) {
  const band = route.risk_score >= 60 ? 'high' : route.risk_score >= 30 ? 'medium' : 'low';
  const c = route.components || {};
  return (
    <article className={`card route-card ${index === 0 ? 'route-card-best' : ''}`}>
      <div className="route-card-head">
        <span className={`route-rank ${index === 0 ? 'route-rank-best' : ''}`}>{index === 0 ? <Check size={16} /> : index + 1}</span>
        <div className="route-choice-main"><b>{minutes(route.baseline_duration_s)} <small>· {km(route.distance_m)}</small></b>
          <span>{route.source_type === 'google_route' ? 'Google traffic-aware travel time' : 'Estimated corridor (road routing unavailable)'}</span></div>
        <StatTile label="Route risk" value={`${Math.round(route.risk_score)}/100`} sub={`${band} risk`} tone={band} />
      </div>
      {route.restricted && <div className="inline-error"><AlertTriangle size={14} /> An official road closure is on this route when you would reach it.</div>}
      <ComponentBars items={[
        { label: 'Congestion forecast', value: c.congestion }, { label: 'Planned demand above usual', value: c.planned_demand },
        { label: 'Incidents and notices', value: c.incidents }, { label: 'Rain', value: c.weather },
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

function DepartureAdvice({ advice }) {
  if (!advice) return null;
  if (!advice.supported) return <div className="route-notice"><Clock3 size={15} /> {advice.reason}</div>;
  return (
    <section className="card advice-card">
      <div className="card-heading"><div><h2>Departure time</h2><p>{advice.message}</p></div><Clock3 className="muted-icon" /></div>
      <div className="table-wrap"><table><thead><tr><th>Leave at</th><th>Change</th><th>Route risk</th></tr></thead><tbody>
        {advice.options.map(o => <tr key={o.departure_at} className={advice.best?.departure_at === o.departure_at ? 'row-highlight' : ''}><td>{dateTime(o.departure_at)}</td><td>{o.offset_minutes === 0 ? 'Your time' : `${o.offset_minutes > 0 ? '+' : ''}${o.offset_minutes} min`}</td><td>{Math.round(o.risk_score)}/100</td></tr>)}
      </tbody></table></div>
    </section>
  );
}

export function JourneyMonitor({ route, destination, setToast, onReroute }) {
  const [journey, setJourney] = useState(null);
  const [status, setStatus] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const watchRef = useRef(null);
  const lastSent = useRef(0);
  useEffect(() => () => { if (watchRef.current != null) navigator.geolocation?.clearWatch(watchRef.current); }, []);
  async function start() {
    if (!navigator.geolocation) { setToast('Location is not available in this browser.'); return; }
    try {
      const created = await post('/api/journeys', { coordinates: route.geometry.coordinates, destination_lat: destination.latitude, destination_lon: destination.longitude });
      setJourney(created);
      setStatus({ status: 'waiting', message: 'Waiting for your location…' });
      watchRef.current = navigator.geolocation.watchPosition(position => send(created.journey_id, position), error => {
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
      if (result.status === 'arrived') stop(false);
    } catch (err) { setStatus({ status: 'error', message: err.message }); }
  }
  async function stop(notifyServer = true) {
    if (watchRef.current != null) navigator.geolocation.clearWatch(watchRef.current);
    watchRef.current = null;
    if (notifyServer && journey) await post(`/api/journeys/${journey.journey_id}/end`).catch(() => {});
    setJourney(null);
  }
  return (
    <section className="card journey-monitor">
      <div className="card-heading"><div><h2>Journey monitoring</h2><p>With your permission, your location is checked against the road ahead. Positions are not kept after the journey ends.</p></div><LocateFixed className="muted-icon" /></div>
      {!journey ? <button className="button button-primary" onClick={start}><Navigation size={15} /> Start monitoring this route</button> : (
        <>
          <div className="monitor-status"><span className="coverage-signal" /><b>{status?.status === 'on_route' ? `On route · ${status.progress_pct}% done · ${km(status.distance_remaining_m)} left` : status?.status === 'off_route' ? 'You appear to be off the planned route' : status?.status === 'low_accuracy' ? 'Waiting for a more accurate location' : status?.status === 'arrived' ? 'Arrived' : 'Monitoring'}</b><small>{status?.message}</small></div>
          {status?.suggestion && <div className="inline-error">{status.suggestion.message} <button className="text-action" onClick={() => onReroute?.(status.suggestion.from)}>Compare routes from here</button></div>}
          {alerts.map(a => <div className="evidence-item" key={a.incident_id}><AlertTriangle size={14} /> {CATEGORY_LABELS[a.category] || label(a.category)} about {a.distance_ahead_m} m ahead{a.closure ? ' (road closed)' : ''}</div>)}
          <button className="button button-secondary" onClick={() => stop(true)}><Square size={13} /> Stop monitoring</button>
        </>
      )}
    </section>
  );
}

export function StartJourney({ setToast, places, pins, googleRouting }) {
  const [origin, setOrigin] = useState(pins?.origin ? { ...pins.origin, id: 'pin' } : null);
  const [destination, setDestination] = useState(null);
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
      <Heading eyebrow="JOURNEY START" title="Compare routes" subtitle="Routes are scored on forecast congestion, planned demand, incidents and notices, and rain, at the time you reach each road." />
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
          <div className="card route-results-map"><RoutePreviewMap routes={result.routes} incidents={publicIncidents} /></div>
          <div className="route-card-list">{result.routes.map((route, index) => <RouteCard key={route.route_index} route={route} index={index} />)}</div>
          <DepartureAdvice advice={best.departure_advice} />
          <JourneyMonitor route={best} destination={result.destination} setToast={setToast}
            onReroute={from => { const point = { id: 'here', label: 'Current location', ...from }; setOrigin(point); compare(point, result.destination); }} />
          <div className="route-notice"><AlertTriangle size={15} />{result.notice} <b>Confirm conditions before travelling.</b></div>
        </>
      )}
    </>
  );
}
