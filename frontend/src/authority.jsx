import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Bell, Check, Cpu, Database, Layers, MapPin, Play, Plus, RefreshCw, ShieldCheck, TrendingUp, UserCheck, X } from 'lucide-react';
import { BLOCKS, CATEGORY_LABELS, METHOD_LABELS, OFFICIAL_CATEGORIES, api, dateTime, label, localISODate, longDate, patch, post, shortDate, targetOptions } from './lib.js';
import { MapSetupNotice, infoContent, useGoogleMap } from './maps.jsx';
import { ComparisonBars, LevelBadge, StatTile, level } from './viz.jsx';

function Heading({ eyebrow, title, subtitle, action }) {
  return <div className="page-heading"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{subtitle}</p></div>{action}</div>;
}
const Empty = ({ title, sub }) => <div className="empty-state"><b>{title}</b>{sub && <small>{sub}</small>}</div>;
const f3 = v => (v == null ? '—' : Number(v).toFixed(3));

// ---------- zone monitor ----------

function ZoneMap({ zones }) {
  const ref = useRef(null);
  const items = useRef([]);
  const { map, mapError } = useGoogleMap(ref, 11);
  useEffect(() => {
    if (!map) return;
    items.current.forEach(item => item.setMap(null));
    items.current = [];
    for (const zone of zones.filter(z => z.latitude != null)) {
      const meta = level(zone.level);
      const circle = new window.google.maps.Circle({ map, center: { lat: zone.latitude, lng: zone.longitude }, radius: zone.radius_m || 2000,
        strokeColor: meta.color, strokeOpacity: 0.9, strokeWeight: 2, fillColor: meta.color, fillOpacity: 0.12 });
      const info = new window.google.maps.InfoWindow({ content: infoContent(zone.name, [meta.text, `Average expected load ${zone.expected_load}`]) });
      circle.addListener('click', () => info.open({ map, position: { lat: zone.latitude, lng: zone.longitude } }));
      items.current.push(circle);
    }
    return () => items.current.forEach(item => item.setMap(null));
  }, [map, zones]);
  return <><div className="zone-map" ref={ref} /><MapSetupNotice message={mapError} /></>;
}

export function ZoneMonitor() {
  const [when, setWhen] = useState('now');
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    setData(null);
    const target = targetOptions().find(o => o.id === when).date.toISOString();
    api(`/api/forecast/zones?target=${encodeURIComponent(target)}`).then(setData).catch(err => setError(err.message));
  }, [when]);
  return (
    <>
      <Heading eyebrow="ZONE MONITOR" title="Zone-wise congestion outlook" subtitle="Each zone averages its roads' forecasts. Methods and confidence differ by road."
        action={<select className="compact-select" value={when} onChange={e => setWhen(e.target.value)} aria-label="Forecast time">{targetOptions().map(o => <option key={o.id} value={o.id}>{o.label}</option>)}</select>} />
      {error && <div className="inline-error">{error}</div>}
      <div className="zone-layout">
        <section className="map-frame"><ZoneMap zones={data?.zones || []} />
          <div className="map-legend">{['Low', 'Medium', 'High'].map(name => <LevelBadge key={name} value={name} compact />)}</div></section>
        <section className="card report-list-card">
          <div className="card-heading"><div><h2>Zones</h2><p>{data ? `${data.zones.length} zones` : 'Loading…'}</p></div><Layers className="muted-icon" /></div>
          {data && <div className="zone-list">{data.zones.map(zone => (
            <article className="zone-item" key={zone.zone_id}>
              <div className="zone-item-head"><b>{zone.name}</b><LevelBadge value={zone.level} compact /><small>load {zone.expected_load}</small></div>
              <div className="zone-roads">{zone.roads.map(r => <span key={r.segment_id} title={`${METHOD_LABELS[r.method] || r.method}, ${r.confidence} confidence`}><LevelBadge value={r.predicted_class} compact /> {r.road}</span>)}</div>
            </article>))}</div>}
        </section>
      </div>
    </>
  );
}

// ---------- alerts ----------

export function AlertsDesk({ setToast }) {
  const [alerts, setAlerts] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = () => api('/api/alerts?status=active').then(b => setAlerts(b.alerts)).catch(err => setToast(err.message));
  useEffect(() => { load(); }, []);
  async function act(alert, status) { await patch(`/api/alerts/${alert.alert_id}`, { status }); setToast(`Alert ${status}.`); await load(); }
  async function check() {
    setBusy(true);
    try { const r = await post('/api/jobs/zone_alerts/run'); setToast(r.status === 'ok' ? `Checked zones: ${r.details.alerts_created} new alert(s).` : 'The zone check failed; see job status.'); await load(); }
    finally { setBusy(false); }
  }
  return (
    <>
      <Heading eyebrow="OPERATIONS ALERTS" title="High-risk zones and recommendations" subtitle="Raised automatically from forecasts, closures, planned demand, and data quality."
        action={<button className="button button-secondary" onClick={check} disabled={busy}><RefreshCw size={14} className={busy ? 'spin' : ''} /> Check zones now</button>} />
      <section className="card report-list-card">
        {alerts === null ? <Empty title="Loading…" /> : alerts.length === 0 ? <Empty title="No active alerts" sub="New alerts appear here as conditions change." /> : (
          <div className="alert-list">{alerts.map(alert => (
            <article className={`ops-alert ops-${alert.level}`} key={alert.alert_id}>
              <span className="alert-severity"><AlertTriangle size={15} /></span>
              <div>
                <b>{alert.title}</b>
                <p>{alert.message}</p>
                {alert.recommendation && <p className="recommendation"><b>Recommended:</b> {alert.recommendation}</p>}
                <small>{label(alert.kind)} · {alert.level} · raised {dateTime(alert.created_at)}{alert.valid_until ? ` · until ${dateTime(alert.valid_until)}` : ''} · {alert.status}</small>
              </div>
              <div className="trip-actions">
                {alert.status === 'open' && <button className="button button-secondary" onClick={() => act(alert, 'acknowledged')}><Check size={13} /> Acknowledge</button>}
                <button className="button button-secondary" onClick={() => act(alert, 'resolved')}><X size={13} /> Resolve</button>
              </div>
            </article>))}</div>
        )}
      </section>
    </>
  );
}

// ---------- official notices ----------

function PickPointMap({ point, onPick }) {
  const ref = useRef(null);
  const marker = useRef(null);
  const { map, mapError } = useGoogleMap(ref, 12);
  useEffect(() => {
    if (!map) return;
    const listener = map.addListener('click', event => event.latLng && onPick({ latitude: event.latLng.lat(), longitude: event.latLng.lng() }));
    map.setOptions({ draggableCursor: 'crosshair' });
    return () => listener.remove();
  }, [map, onPick]);
  useEffect(() => {
    if (!map) return;
    marker.current?.setMap(null);
    if (point) marker.current = new window.google.maps.Marker({ map, position: { lat: point.latitude, lng: point.longitude }, title: 'Notice location' });
  }, [map, point]);
  return <div className="pick-map"><div className="route-map" ref={ref} /><MapSetupNotice message={mapError} /></div>;
}

export function NoticeDesk({ setToast, places, refresh }) {
  const empty = { category: 'vip_movement', description: '', area: '', road: '', starts_at: '', expires_at: '', severity: '', closure: false, broadcast_message: '' };
  const [form, setForm] = useState(empty);
  const [point, setPoint] = useState(null);
  const [notices, setNotices] = useState([]);
  const [busy, setBusy] = useState(false);
  const load = () => api('/api/incidents?official=true&limit=100').then(b => setNotices(b.incidents)).catch(() => {});
  useEffect(() => { load(); }, []);
  async function submit(e) {
    e.preventDefault();
    if (!point) { setToast('Choose the location on the map or from the list.'); return; }
    setBusy(true);
    try {
      const body = { category: form.category, description: form.description, area: form.area || null, road: form.road || null,
        latitude: point.latitude, longitude: point.longitude, closure: form.closure, severity: form.severity || null,
        starts_at: form.starts_at ? new Date(form.starts_at).toISOString() : null, expires_at: form.expires_at ? new Date(form.expires_at).toISOString() : null,
        broadcast_message: form.broadcast_message.trim() || null };
      const result = await post('/api/incidents/official', body);
      setToast(`Notice published until ${dateTime(result.expires_at)}${result.broadcast ? ' and broadcast to commuters' : ''}.`);
      setForm(empty);
      setPoint(null);
      await load();
      refresh?.();
    } catch (err) { setToast(err.message); } finally { setBusy(false); }
  }
  async function resolve(notice) { await patch(`/api/incidents/${notice.incident_id}`, { status: 'resolved', official_note: '' }); setToast('Notice ended.'); await load(); refresh?.(); }
  return (
    <>
      <Heading eyebrow="OFFICIAL NOTICES" title="Publish a road notice" subtitle="VIP movements, closures, metro work, diversions, events and restrictions. Closures mark routes through them as restricted." />
      <div className="notice-layout">
        <section className="card form-card">
          <form className="incident-form" onSubmit={submit}>
            <div className="form-pair">
              <label>Type<select value={form.category} onChange={e => setForm({ ...form, category: e.target.value, closure: e.target.value === 'road_closure' })}>
                {OFFICIAL_CATEGORIES.map(c => <option key={c} value={c}>{CATEGORY_LABELS[c]}</option>)}</select></label>
              <label>Severity<select value={form.severity} onChange={e => setForm({ ...form, severity: e.target.value })}>
                <option value="">Default for this type</option><option value="info">Information</option><option value="warning">Warning</option><option value="critical">Critical</option></select></label>
            </div>
            <label>Description<textarea required minLength="5" rows="3" value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} placeholder="What is happening and what drivers should do" /></label>
            <label>Location from a known road<select value="" onChange={e => { const p = places.find(x => x.id === e.target.value); if (p) { setPoint(p); setForm({ ...form, area: p.area, road: p.road }); } }}>
              <option value="">Or click the map below</option>{places.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}</select></label>
            <PickPointMap point={point} onPick={p => setPoint(p)} />
            {point && <small className="coordinate-note">{point.latitude.toFixed(5)}, {point.longitude.toFixed(5)}</small>}
            <div className="form-pair">
              <label>Starts <small>empty = now</small><input type="datetime-local" value={form.starts_at} onChange={e => setForm({ ...form, starts_at: e.target.value })} /></label>
              <label>Ends <small>empty = default validity</small><input type="datetime-local" value={form.expires_at} onChange={e => setForm({ ...form, expires_at: e.target.value })} /></label>
            </div>
            <label className="checkbox"><input type="checkbox" checked={form.closure} onChange={e => setForm({ ...form, closure: e.target.checked })} /> Road is closed (routes through it are restricted)</label>
            <label>Broadcast to commuters <small>optional</small><input value={form.broadcast_message} minLength="10" onChange={e => setForm({ ...form, broadcast_message: e.target.value })} placeholder="Short public message" /></label>
            <button className="button button-primary" disabled={busy}><Bell size={15} />{busy ? 'Publishing…' : 'Publish notice'}</button>
          </form>
        </section>
        <section className="card report-list-card">
          <div className="card-heading"><div><h2>Notices</h2><p>Most recent first</p></div></div>
          {notices.length === 0 ? <Empty title="No official notices" /> : <div className="report-list">{notices.map(n => (
            <article className="report-item" key={n.incident_id}>
              <div className="report-content">
                <div className="report-title"><b>{CATEGORY_LABELS[n.category] || label(n.category)}</b><span className={`status-chip ${n.status === 'verified' ? 'status-green' : 'status-gray'}`}><i />{n.status === 'verified' ? 'in force' : n.status}</span>{n.closure ? <span className="status-chip status-orange"><i />closure</span> : null}</div>
                <p>{n.description}</p>
                <div className="report-meta"><span><MapPin size={13} />{[n.area, n.road].filter(Boolean).join(' · ') || `${n.latitude?.toFixed(4)}, ${n.longitude?.toFixed(4)}`}</span><span>{dateTime(n.starts_at)} → {dateTime(n.expires_at)}</span></div>
              </div>
              {n.status === 'verified' && <button className="button button-secondary" onClick={() => resolve(n)}>End notice</button>}
            </article>))}</div>}
        </section>
      </div>
    </>
  );
}

// ---------- planned demand ----------

export function DemandDesk() {
  const [hours, setHours] = useState(48);
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => { setData(null); api(`/api/demand?hours=${hours}`).then(setData).catch(err => setError(err.message)); }, [hours]);
  return (
    <>
      <Heading eyebrow="PLANNED DEMAND" title="Future demand and hotspots" subtitle="Commuters' planned trips, weighted by vehicle type (PCU). Individual trips are never shown."
        action={<select className="compact-select" value={hours} onChange={e => setHours(Number(e.target.value))} aria-label="Look-ahead">{[24, 48, 72, 168].map(h => <option key={h} value={h}>Next {h} h</option>)}</select>} />
      {error && <div className="inline-error">{error}</div>}
      {data && <>
        <div className="stat-row">
          <StatTile label="Planned passages" value={data.segments.reduce((s, c) => s + c.planned_trips, 0)} sub="road × time block" />
          <StatTile label="Planned load" value={`${data.segments.reduce((s, c) => s + c.pcu, 0).toFixed(1)} PCU`} />
          <StatTile label="Hotspots" value={data.hotspots.filter(h => h.level === 'High').length} sub="high level" />
        </div>
        <section className="card report-list-card"><div className="card-heading"><div><h2>Hotspots</h2><p>Score = 0.6 × forecast load + 0.4 × planned PCU share</p></div></div>
          {data.hotspots.length === 0 ? <Empty title="No planned demand in this window" /> : <div className="table-wrap"><table><thead><tr><th>Road</th><th>When</th><th>Trips</th><th>PCU</th><th>Forecast</th><th>Score</th></tr></thead><tbody>
            {data.hotspots.map(h => <tr key={`${h.segment_id}-${h.date}-${h.time_block}`}><td className="road-name">{h.road}<small className="table-sub">{h.area}</small></td><td>{shortDate(h.date)} {h.time_block}</td><td>{h.planned_trips}</td><td>{h.pcu}</td><td><LevelBadge value={h.predicted_class} compact /></td><td><LevelBadge value={h.level} compact /> {h.score}</td></tr>)}
          </tbody></table></div>}
        </section>
        <section className="card report-list-card"><div className="card-heading"><div><h2>Corridors</h2><p>Zone to zone by departure hour. Groups under {data.minimum_group_size} trips are hidden ({data.suppressed_trips} trips).</p></div></div>
          {data.corridors.length === 0 ? <Empty title="No corridor large enough to show" /> : <div className="table-wrap"><table><thead><tr><th>From</th><th>To</th><th>Departure hour (IST)</th><th>Trips</th><th>PCU</th></tr></thead><tbody>
            {data.corridors.map(c => <tr key={`${c.origin_zone}-${c.destination_zone}-${c.departure_hour_ist}`}><td>{label(c.origin_zone)}</td><td>{label(c.destination_zone)}</td><td>{dateTime(c.departure_hour_ist)}</td><td>{c.planned_trips}</td><td>{c.pcu}</td></tr>)}
          </tbody></table></div>}
        </section>
        <small className="form-hint">PCU factors: {Object.entries(data.pcu_factors).map(([k, v]) => `${label(k)} ${v}`).join(', ')}. {data.notice}</small>
      </>}
    </>
  );
}

// ---------- data coverage ----------

export function CoverageDesk({ setToast, googleRouting }) {
  const [segments, setSegments] = useState(null);
  const [roadTypes, setRoadTypes] = useState([]);
  const [editing, setEditing] = useState(null);
  const load = () => api('/api/segments').then(b => { setSegments(b.segments); setRoadTypes(b.road_types); });
  useEffect(() => { load().catch(err => setToast(err.message)); }, []);
  async function save(e) {
    e.preventDefault();
    try {
      await patch(`/api/segments/${editing.segment_id}`, { latitude: editing.latitude === '' ? null : Number(editing.latitude), longitude: editing.longitude === '' ? null : Number(editing.longitude), road_type: editing.road_type });
      setToast('Road updated.');
      setEditing(null);
      await load();
    } catch (err) { setToast(err.message); }
  }
  async function geocode(segment) { try { await post(`/api/segments/${segment.segment_id}/geocode`); setToast('Location set from Google Maps.'); await load(); } catch (err) { setToast(err.message); } }
  return (
    <>
      <Heading eyebrow="DATA COVERAGE" title="Road and data-coverage registry" subtitle="Being on the map is not the same as having traffic data. The method column shows what each road's data supports." />
      <section className="card segment-card">
        {segments === null ? <Empty title="Loading…" /> : <div className="table-wrap"><table><thead><tr><th>Road</th><th>Location</th><th>Type</th><th>Records</th><th>Period</th><th>Missing</th><th>Weather</th><th>Method</th><th></th></tr></thead><tbody>
          {segments.map(s => <tr key={s.segment_id}>
            <td className="road-name">{s.road}<small className="table-sub">{s.area}</small></td>
            <td>{s.has_map_location ? label(s.location_source) : <span className="status-chip status-orange"><i />not mapped</span>}</td>
            <td>{label(s.road_type)}</td><td>{s.record_count.toLocaleString('en-IN')}</td>
            <td>{s.earliest_observation ? `${longDate(s.earliest_observation)} – ${longDate(s.latest_observation)}` : '—'}</td>
            <td>{s.missing_slot_pct == null ? '—' : `${s.missing_slot_pct}%`}</td><td>{s.weather_coverage_pct == null ? '—' : `${s.weather_coverage_pct}%`}</td>
            <td title={s.notes.join(' ')}>{label(s.data_method)}</td>
            <td><button className="text-action" onClick={() => setEditing({ segment_id: s.segment_id, road: s.road, latitude: s.latitude ?? '', longitude: s.longitude ?? '', road_type: s.road_type })}>Edit</button>
              {googleRouting && <button className="text-action" onClick={() => geocode(s)}>Geocode</button>}</td>
          </tr>)}
        </tbody></table></div>}
      </section>
      {editing && <section className="card form-card"><div className="card-heading"><div><h2>Edit {editing.road}</h2><p>Set the map location or road type used for routing and transfer.</p></div></div>
        <form className="incident-form" onSubmit={save}>
          <div className="form-pair"><label>Latitude<input type="number" step="any" value={editing.latitude} onChange={e => setEditing({ ...editing, latitude: e.target.value })} /></label>
            <label>Longitude<input type="number" step="any" value={editing.longitude} onChange={e => setEditing({ ...editing, longitude: e.target.value })} /></label></div>
          <label>Road type<select value={editing.road_type} onChange={e => setEditing({ ...editing, road_type: e.target.value })}>{roadTypes.map(t => <option key={t} value={t}>{label(t)}</option>)}</select></label>
          <div className="trip-actions"><button className="button button-primary">Save</button><button type="button" className="button button-secondary" onClick={() => setEditing(null)}>Cancel</button></div>
        </form></section>}
    </>
  );
}

// ---------- observations, quarantine, jobs ----------

export function DataDesk({ setToast, segments, refresh }) {
  const [form, setForm] = useState({ segment: '', observed_date: localISODate(), time_block: 'Morning', traffic_volume: '', average_speed: '', travel_time_index: '', congestion_level: '', weather: '', roadwork: '' });
  const [pending, setPending] = useState([]);
  const [quarantine, setQuarantine] = useState([]);
  const [jobs, setJobs] = useState(null);
  const [running, setRunning] = useState('');
  const load = () => Promise.all([
    api('/api/observations?quality_status=unverified&source_type=manual&limit=50'), api('/api/observations?quality_status=unverified&source_type=camera&limit=50'),
    api('/api/quarantine?limit=30'), api('/api/jobs'),
  ]).then(([manual, camera, q, j]) => { setPending([...manual.observations, ...camera.observations]); setQuarantine(q.records); setJobs(j); });
  useEffect(() => { load().catch(err => setToast(err.message)); }, []);
  async function submit(e) {
    e.preventDefault();
    const segment = segments.find(s => s.segment_id === form.segment);
    const num = v => (v === '' ? null : Number(v));
    try {
      await post('/api/observations', { observed_date: form.observed_date, time_block: form.time_block, area: segment.area, road: segment.road,
        traffic_volume: num(form.traffic_volume), average_speed: num(form.average_speed), travel_time_index: num(form.travel_time_index),
        congestion_level: form.congestion_level || null, weather: form.weather || null, roadwork: form.roadwork || null, source_name: 'authority_entry' });
      setToast('Observation stored as unverified; verify it to add it to the training history.');
      await load();
      refresh?.();
    } catch (err) { setToast(err.message); }
  }
  async function review(row, status) { await patch(`/api/observations/${row.id}`, { quality_status: status, note: '' }); setToast(`Observation ${status}.`); await load(); }
  async function run(job) { setRunning(job); try { const r = await post(`/api/jobs/${job}/run`); setToast(`${label(job)}: ${r.status}`); await load(); } finally { setRunning(''); } }
  return (
    <>
      <Heading eyebrow="DATA PIPELINE" title="Observations, review, and jobs" subtitle="Verified observations join the history used to retrain the model. Invalid input is quarantined, never silently dropped." />
      <div className="notice-layout">
        <section className="card form-card"><div className="card-heading"><div><h2>Add a block observation</h2><p>Measured values for one road and time block.</p></div><Plus className="muted-icon" /></div>
          <form className="incident-form" onSubmit={submit}>
            <label>Road<select required value={form.segment} onChange={e => setForm({ ...form, segment: e.target.value })}><option value="">Choose a road</option>
              {segments.map(s => <option key={s.segment_id} value={s.segment_id}>{s.road}, {s.area}</option>)}</select></label>
            <div className="form-pair"><label>Date<input type="date" required value={form.observed_date} onChange={e => setForm({ ...form, observed_date: e.target.value })} /></label>
              <label>Time block<select value={form.time_block} onChange={e => setForm({ ...form, time_block: e.target.value })}>{BLOCKS.map(b => <option key={b}>{b}</option>)}</select></label></div>
            <div className="form-pair"><label>Traffic volume<input type="number" min="0" value={form.traffic_volume} onChange={e => setForm({ ...form, traffic_volume: e.target.value })} /></label>
              <label>Average speed <small>km/h</small><input type="number" min="0" max="250" step="any" value={form.average_speed} onChange={e => setForm({ ...form, average_speed: e.target.value })} /></label></div>
            <div className="form-pair"><label>Travel time index<input type="number" min="0.01" max="20" step="any" value={form.travel_time_index} onChange={e => setForm({ ...form, travel_time_index: e.target.value })} /></label>
              <label>Congestion<select value={form.congestion_level} onChange={e => setForm({ ...form, congestion_level: e.target.value })}><option value="">Not observed</option>{['Low', 'Medium', 'High'].map(c => <option key={c}>{c}</option>)}</select></label></div>
            <div className="form-pair"><label>Weather <small>optional</small><input value={form.weather} onChange={e => setForm({ ...form, weather: e.target.value })} placeholder="Clear, Rain…" /></label>
              <label>Roadwork<select value={form.roadwork} onChange={e => setForm({ ...form, roadwork: e.target.value })}><option value="">Unknown</option><option>No</option><option>Yes</option></select></label></div>
            <button className="button button-primary"><Plus size={15} /> Store observation</button>
          </form></section>
        <section className="card report-list-card"><div className="card-heading"><div><h2>Awaiting review</h2><p>Manual and camera observations</p></div><Database className="muted-icon" /></div>
          {pending.length === 0 ? <Empty title="Nothing to review" /> : <div className="table-wrap"><table><thead><tr><th>Road</th><th>When</th><th>Values</th><th>Source</th><th></th></tr></thead><tbody>
            {pending.map(o => <tr key={o.id}><td className="road-name">{o.road}<small className="table-sub">{o.area}</small></td><td>{o.observed_date} {o.time_block}</td>
              <td>{[o.traffic_volume != null && `vol ${o.traffic_volume}`, o.vehicle_count != null && `${o.vehicle_count} veh`, o.flow_per_hour != null && `${o.flow_per_hour}/h`, o.congestion_level].filter(Boolean).join(' · ') || '—'}</td>
              <td>{label(o.source_name)}</td><td><button className="text-action" onClick={() => review(o, 'verified')}>Verify</button><button className="text-action" onClick={() => review(o, 'rejected')}>Reject</button></td></tr>)}
          </tbody></table></div>}
        </section>
      </div>
      <section className="card report-list-card"><div className="card-heading"><div><h2>Background jobs</h2><p>{jobs ? (jobs.scheduler_running ? 'Scheduler running in the API process' : 'Scheduler off; run jobs manually') : 'Loading…'}</p></div><Cpu className="muted-icon" /></div>
        {jobs && <div className="table-wrap"><table><thead><tr><th>Job</th><th>Every</th><th>Last run</th><th>Status</th><th></th></tr></thead><tbody>
          {jobs.jobs.map(j => <tr key={j.job}><td className="road-name">{label(j.job)}<small className="table-sub">{j.description}</small></td><td>{j.every_seconds >= 3600 ? `${j.every_seconds / 3600} h` : `${j.every_seconds / 60} min`}</td>
            <td>{dateTime(j.last_run?.started_at)}</td><td>{j.last_run ? <span className={`status-chip ${j.last_run.status === 'ok' ? 'status-green' : j.last_run.status === 'failed' ? 'status-orange' : 'status-gray'}`}><i />{j.last_run.status}</span> : '—'}</td>
            <td><button className="text-action" disabled={running === j.job} onClick={() => run(j.job)}><Play size={12} /> {running === j.job ? 'Running…' : 'Run now'}</button></td></tr>)}
        </tbody></table></div>}
      </section>
      <section className="card report-list-card"><div className="card-heading"><div><h2>Quarantine</h2><p>Rejected input with the reason</p></div></div>
        {quarantine.length === 0 ? <Empty title="Quarantine is empty" /> : <div className="table-wrap"><table><thead><tr><th>Source</th><th>Received</th><th>Reason</th></tr></thead><tbody>
          {quarantine.map(q => <tr key={q.id}><td>{label(q.source_type)}</td><td>{dateTime(q.ingested_at)}</td><td className="wrap-cell">{String(q.reason).slice(0, 220)}</td></tr>)}
        </tbody></table></div>}
      </section>
    </>
  );
}

// ---------- models ----------

export function ModelsDesk({ setToast, segments }) {
  const [models, setModels] = useState(null);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [training, setTraining] = useState(null);
  const [evaluation, setEvaluation] = useState(null);
  const [replayForm, setReplayForm] = useState({ segment_id: '', as_of_date: '', as_of_time_block: 'Noon' });
  const [replay, setReplay] = useState(null);
  const load = () => api('/api/models').then(b => { setModels(b.models); setTraining(b.training); if (!selected && b.models[0]) setSelected(b.models[0].version); });
  useEffect(() => { load().catch(err => setToast(err.message)); api('/api/evaluation').then(setEvaluation).catch(() => {}); }, []);
  useEffect(() => { if (selected) api(`/api/models/${selected}`).then(setDetail).catch(() => setDetail(null)); }, [selected]);
  useEffect(() => {
    if (!training?.running) return undefined;
    const timer = setInterval(() => api('/api/models/train/status').then(status => { setTraining(status); if (!status.running) { load(); setToast('Training finished.'); } }), 3000);
    return () => clearInterval(timer);
  }, [training?.running]);
  async function train() { try { setTraining(await post('/api/models/train')); } catch (err) { setToast(err.message); } }
  async function activate(version) { try { await post(`/api/models/${version}/activate`); setToast(`${version} is now active.`); await load(); } catch (err) { setToast(err.message); } }
  async function runReplay(e) {
    e.preventDefault();
    setReplay(null);
    try { setReplay(await post('/api/forecast/replay', { ...replayForm, horizons: 3, version: selected })); } catch (err) { setReplay({ error: err.message }); }
  }
  const m = detail?.metrics;
  const active = models?.find(x => x.status === 'active');
  return (
    <>
      <Heading eyebrow="FORECASTING MODELS" title="Hybrid LSTM → Random Forest" subtitle="Each version is evaluated on the latest held-out period and must pass predefined acceptance checks before it can be used."
        action={<button className="button button-primary" disabled={training?.running} onClick={train}><Cpu size={15} />{training?.running ? 'Training…' : 'Train new version'}</button>} />
      <div className="stat-row">
        <StatTile label="Active model" value={active ? active.version.replace('hybrid-', '') : 'None'} sub={active ? `macro-F1 ${f3(active.test.macro_f1)}` : 'Forecasts use historical patterns'} />
        <StatTile label="Versions" value={models?.length ?? '—'} />
        <StatTile label="Live forecasts checked" value={evaluation?.evaluated_count ?? 0} sub={`of ${evaluation?.prediction_count ?? 0} issued`} />
      </div>
      {training?.running && <div className="model-warning"><Cpu size={16} /><span><b>Training in progress.</b> <code className="log-tail">{training.log_tail?.split('\n').filter(Boolean).slice(-1)[0]}</code></span></div>}
      <section className="card report-list-card"><div className="card-heading"><div><h2>Versions</h2><p>Select a version to see its evaluation</p></div></div>
        {models === null ? <Empty title="Loading…" /> : models.length === 0 ? <Empty title="No model trained yet" sub="Train a version once the historical dataset is in place." /> : <div className="table-wrap"><table><thead><tr><th>Version</th><th>Status</th><th>Accuracy</th><th>Macro-F1</th><th>Acceptance</th><th></th></tr></thead><tbody>
          {models.map(v => <tr key={v.version} className={selected === v.version ? 'row-highlight' : ''} onClick={() => setSelected(v.version)}>
            <td className="road-name">{v.version}<small className="table-sub">{dateTime(v.created_at)} · {v.trained_by}</small></td>
            <td><span className={`status-chip ${v.status === 'active' ? 'status-green' : v.status === 'rejected' ? 'status-orange' : 'status-gray'}`}><i />{v.status}</span></td>
            <td>{f3(v.test.accuracy)}</td><td>{f3(v.test.macro_f1)}</td><td>{v.acceptance.passed ? 'passed' : 'not passed'}</td>
            <td>{['candidate', 'retired'].includes(v.status) && <button className="text-action" onClick={e => { e.stopPropagation(); activate(v.version); }}>{v.status === 'retired' ? 'Roll back to this' : 'Activate'}</button>}</td></tr>)}
        </tbody></table></div>}
      </section>
      {m && <div className="notice-layout">
        <section className="card report-list-card"><div className="card-heading"><div><h2>Test period: macro-F1</h2><p>{m.split.method}; test from {m.split.test_from}</p></div><TrendingUp className="muted-icon" /></div>
          <ComparisonBars highlight="Hybrid model" items={[{ label: 'Hybrid model', value: m.test.macro_f1 }, ...Object.entries(m.baselines_test).map(([k, b]) => ({ label: label(k), value: b.macro_f1 })),
            ...(m.lstm_contribution ? [{ label: 'RF without LSTM (ablation)', value: m.lstm_contribution.rf_without_lstm_macro_f1 }] : [])]} />
          <div className="table-wrap"><table><thead><tr><th>Class</th><th>Precision</th><th>Recall</th><th>F1</th><th>Records</th></tr></thead><tbody>
            {Object.entries(m.test.per_class).map(([c, v]) => <tr key={c}><td><LevelBadge value={c} compact /></td><td>{f3(v.precision)}</td><td>{f3(v.recall)}</td><td>{f3(v.f1)}</td><td>{v.support}</td></tr>)}
          </tbody></table></div>
          <small className="form-hint">{m.lstm_contribution?.note}</small>
        </section>
        <section className="card report-list-card"><div className="card-heading"><div><h2>Acceptance checks</h2><p>{detail.acceptance.passed ? 'All checks passed' : 'Not accepted for live use'}</p></div><ShieldCheck className="muted-icon" /></div>
          <ul className="check-list">{detail.acceptance.checks.map(c => <li key={c.check} className={c.passed ? 'check-pass' : 'check-fail'}>{c.passed ? <Check size={14} /> : <X size={14} />} <span>{c.check}</span><small>{typeof c.value === 'number' ? `${f3(c.value)} vs ${f3(c.threshold)}` : String(c.value)}</small></li>)}</ul>
          <div className="table-wrap"><table><thead><tr><th>Blocks ahead</th><th>Accuracy</th><th>Macro-F1</th></tr></thead><tbody>
            {Object.entries(m.horizons_test).map(([h, v]) => <tr key={h}><td>{h}</td><td>{f3(v.accuracy)}</td><td>{f3(v.macro_f1)}</td></tr>)}</tbody></table></div>
          <div className="table-wrap"><table><thead><tr><th>LSTM forecast</th><th>MAE</th><th>RMSE</th><th>Same-as-last MAE</th></tr></thead><tbody>
            {Object.entries(m.lstm_regression_test).map(([k, v]) => <tr key={k}><td>{label(k)}</td><td>{f3(v.mae)}</td><td>{f3(v.rmse)}</td><td>{f3(v.persistence_mae)}</td></tr>)}</tbody></table></div>
          <small className="form-hint">Transfer to similar roads: {m.transfer_validation?.reason}</small>
        </section>
      </div>}
      {m && <section className="card report-list-card"><div className="card-heading"><div><h2>Accuracy by group</h2><p>Test period, by time block, day type and weather</p></div></div>
        <div className="table-wrap"><table><thead><tr><th>Group</th><th>Records</th><th>Accuracy</th><th>Macro-F1</th></tr></thead><tbody>
          {['time_block', 'day_type', 'weather'].flatMap(g => Object.entries(m.breakdowns_test[g] || {}).map(([k, v]) => <tr key={`${g}-${k}`}><td>{label(g)}: {k}</td><td>{v.count}</td><td>{f3(v.accuracy)}</td><td>{f3(v.macro_f1)}</td></tr>))}
        </tbody></table></div></section>}
      <section className="card form-card"><div className="card-heading"><div><h2>Replay on past records</h2><p>Run the selected version as if it were a past moment and compare with what was observed.</p></div></div>
        <form className="replay-form" onSubmit={runReplay}>
          <label>Road<select required value={replayForm.segment_id} onChange={e => setReplayForm({ ...replayForm, segment_id: e.target.value })}><option value="">Choose a road</option>
            {segments.filter(s => s.has_traffic_data).map(s => <option key={s.segment_id} value={s.segment_id}>{s.road}, {s.area}</option>)}</select></label>
          <label>As of date<input type="date" required value={replayForm.as_of_date} onChange={e => setReplayForm({ ...replayForm, as_of_date: e.target.value })} /></label>
          <label>Block<select value={replayForm.as_of_time_block} onChange={e => setReplayForm({ ...replayForm, as_of_time_block: e.target.value })}>{BLOCKS.map(b => <option key={b}>{b}</option>)}</select></label>
          <button className="button button-secondary" disabled={!selected}><Play size={14} /> Replay</button>
        </form>
        {replay?.error && <div className="inline-error">{replay.error}</div>}
        {replay?.steps && <div className="table-wrap"><table><thead><tr><th>Block</th><th>Predicted</th><th>Observed</th><th>Forecast volume</th><th>Observed volume</th></tr></thead><tbody>
          {replay.steps.map(s => <tr key={`${s.date}-${s.time_block}`}><td>{longDate(s.date)} {s.time_block}</td><td><LevelBadge value={s.predicted_class} compact /></td><td>{s.observed_class ? <LevelBadge value={s.observed_class} compact /> : '—'}</td>
            <td>{Math.round(s.predicted_values.traffic_volume).toLocaleString('en-IN')}</td><td>{s.observed_values?.traffic_volume != null ? Math.round(s.observed_values.traffic_volume).toLocaleString('en-IN') : '—'}</td></tr>)}
        </tbody></table><small className="form-hint">{replay.notice}</small></div>}
      </section>
    </>
  );
}

// ---------- access requests ----------

export function AccessDesk({ setToast, user }) {
  const [requests, setRequests] = useState([]);
  const [authorities, setAuthorities] = useState([]);
  const load = () => Promise.all([api('/api/access-requests?status=pending'), api('/api/authorities')]).then(([r, a]) => { setRequests(r.requests); setAuthorities(a.authorities); });
  useEffect(() => { load().catch(err => setToast(err.message)); }, []);
  async function decide(request, status) {
    const note = status === 'rejected' ? (window.prompt('Reason for rejecting (shown to the applicant):') || '') : '';
    try { await patch(`/api/access-requests/${request.request_id}`, { status, note }); setToast(`Request ${status}.`); await load(); } catch (err) { setToast(err.message); }
  }
  async function revoke(username) {
    if (!window.confirm(`Revoke authority access for ${username}?`)) return;
    try { await post(`/api/authorities/${encodeURIComponent(username)}/revoke`); setToast('Access revoked.'); await load(); } catch (err) { setToast(err.message); }
  }
  return (
    <>
      <Heading eyebrow="ACCESS CONTROL" title="Authority access" subtitle="Approve only after checking the applicant's official details. Every decision is recorded in the audit log." />
      <section className="card report-list-card"><div className="card-heading"><div><h2>Pending requests</h2></div><UserCheck className="muted-icon" /></div>
        {requests.length === 0 ? <Empty title="No pending requests" /> : <div className="table-wrap"><table><thead><tr><th>Applicant</th><th>Organisation</th><th>Verification</th><th>Reason</th><th></th></tr></thead><tbody>
          {requests.map(r => <tr key={r.request_id}><td className="road-name">{r.full_name}<small className="table-sub">{r.username} · {dateTime(r.created_at)}</small></td>
            <td>{r.organization}<small className="table-sub">{r.designation}</small></td><td>{[r.official_email, r.employee_id].filter(Boolean).join(' · ')}</td>
            <td className="wrap-cell">{r.reason}</td><td><button className="text-action" onClick={() => decide(r, 'approved')}>Approve</button><button className="text-action" onClick={() => decide(r, 'rejected')}>Reject</button></td></tr>)}
        </tbody></table></div>}
      </section>
      <section className="card report-list-card"><div className="card-heading"><div><h2>Authority accounts</h2></div></div>
        <div className="table-wrap"><table><thead><tr><th>Username</th><th>Since</th><th></th></tr></thead><tbody>
          {authorities.map(a => <tr key={a.username}><td>{a.username}{a.username === user?.username ? ' (you)' : ''}</td><td>{dateTime(a.created_at)}</td><td>{a.username !== user?.username && <button className="text-action" onClick={() => revoke(a.username)}>Revoke</button>}</td></tr>)}
        </tbody></table></div>
      </section>
    </>
  );
}
