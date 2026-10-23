import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Activity, AlertTriangle, ArrowRight, Bell, Camera, Check, ChevronDown, Clock3, Construction, Cpu, Database,
  Gauge, Inbox, Layers, LayoutDashboard, LocateFixed, Map as MapIcon, MapPin, Menu, Navigation, Plus, Radio,
  RefreshCw, Route, Search, ShieldCheck, SlidersHorizontal, TrendingUp, UserCheck, UserPlus, Users, Video, X,
} from 'lucide-react';
import { API, BLOCKS, CATEGORY_LABELS, CITIZEN_CATEGORIES, api, dateTime, localISODate, longDate, n, readJson, writeJson } from './lib.js';
import { MapSetupNotice, infoContent, useGoogleMap } from './maps.jsx';
import { LEVELS, LevelBadge, level } from './viz.jsx';
import { AccountPage, NotificationsPage, PlanTrips, RoadForecast, StartJourney } from './commuter.jsx';
import { AccessDesk, AlertsDesk, CoverageDesk, DataDesk, DemandDesk, ModelsDesk, NoticeDesk, ZoneMonitor } from './authority.jsx';

const USER_NAV = [
  { section: 'MY TRAVEL', items: [
    { id: 'user-home', label: 'My commute', icon: LayoutDashboard },
    { id: 'journeys', label: 'Start a journey', icon: Navigation },
    { id: 'plan', label: 'Plan a trip', icon: Route },
    { id: 'forecast-road', label: 'Road forecast', icon: TrendingUp },
    { id: 'incidents', label: 'Report an incident', icon: AlertTriangle },
  ] },
  { section: 'ACCOUNT', items: [
    { id: 'notifications', label: 'Notifications', icon: Bell },
    { id: 'account', label: 'Account', icon: UserPlus },
  ] },
];
const AUTH_NAV = [
  { section: 'OPERATIONS', items: [
    { id: 'authority-home', label: 'Operations overview', icon: LayoutDashboard },
    { id: 'zones', label: 'Zone monitor', icon: Layers },
    { id: 'authority-map', label: 'Incident map', icon: MapIcon },
    { id: 'notices', label: 'Official notices', icon: Radio },
    { id: 'alerts', label: 'Alerts', icon: AlertTriangle },
    { id: 'broadcasts', label: 'Broadcast alerts', icon: Bell },
  ] },
  { section: 'DATA AND MODELS', items: [
    { id: 'demand', label: 'Planned demand', icon: Users },
    { id: 'coverage', label: 'Data coverage', icon: Database },
    { id: 'data', label: 'Observations & jobs', icon: Inbox },
    { id: 'models', label: 'Models', icon: Cpu },
    { id: 'cameras', label: 'CCTV review', icon: Camera },
    { id: 'overview', label: 'Traffic data', icon: Gauge },
  ] },
  { section: 'ADMINISTRATION', items: [
    { id: 'access', label: 'Access requests', icon: UserCheck },
    { id: 'map', label: 'Place search', icon: Search },
  ] },
];
const EMPTY_DATA = { summary: null, coverage: [], segments: [], incidents: [], cameras: [], models: [], synthetic: null, broadcasts: [], footage: [], alerts: [], mapConfig: null };
const todayLabel = new Date().toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }).toUpperCase();

function App() {
  const [portal, setPortal] = useState(() => window.location.pathname === '/authority' ? 'authority' : window.location.pathname === '/user' ? 'user' : 'landing');
  const [page, setPage] = useState(() => window.location.pathname === '/authority' ? 'authority-home' : 'user-home');
  const [data, setData] = useState(EMPTY_DATA);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [toast, setToast] = useState('');
  const [apiOnline, setApiOnline] = useState(null);
  const [mobileNav, setMobileNav] = useState(false);
  const [user, setUser] = useState(null);
  const [unread, setUnread] = useState(0);

  async function refresh() {
    setBusy(true); setError('');
    try {
      const isAuthority = user?.role === 'authority';
      const optional = (enabled, path, fallback = null) => enabled ? api(path).catch(() => fallback) : Promise.resolve(fallback);
      const [coverage, segments, mapConfig, broadcasts, incidents, cameras, footage, summary, models, synthetic, alerts] = await Promise.all([
        api('/api/coverage'), api('/api/segments'), api('/api/map/config').catch(() => null),
        api('/api/broadcasts').catch(() => ({ broadcasts: [] })),
        optional(isAuthority, '/api/incidents?limit=200', { incidents: [] }), optional(isAuthority, '/api/cameras', { cameras: [] }),
        optional(isAuthority, '/api/camera-footage', { footage: [] }), optional(isAuthority, '/api/summary'),
        optional(isAuthority, '/api/models', { models: [] }), optional(isAuthority, '/api/synthetic/summary'),
        optional(isAuthority, '/api/alerts?status=active', { alerts: [] }),
      ]);
      setData({ coverage: coverage.segments || [], segments: segments.segments || [], mapConfig, broadcasts: broadcasts.broadcasts || [],
        incidents: incidents.incidents || [], cameras: cameras.cameras || [], footage: footage.footage || [], summary,
        models: models.models || [], synthetic, alerts: alerts.alerts || [] });
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
  useEffect(() => { refresh(); }, [user?.role]);
  useEffect(() => { if (sessionStorage.getItem('flowstateToken')) api('/api/auth/me').then(setUser).catch(() => { sessionStorage.removeItem('flowstateToken'); }); }, []);
  useEffect(() => {
    let active = true;
    const check = () => api('/api/health').then(() => { if (active) setApiOnline(true); }).catch(() => { if (active) setApiOnline(false); });
    check();
    const timer = setInterval(check, 30000);
    return () => { active = false; clearInterval(timer); };
  }, []);
  useEffect(() => {
    if (user?.role !== 'citizen') return undefined;
    const poll = () => api('/api/notifications').then(body => setUnread(body.unread)).catch(() => {});
    poll();
    const timer = setInterval(poll, 60000);
    return () => clearInterval(timer);
  }, [user?.role]);
  useEffect(() => { if (!toast) return; const id = setTimeout(() => setToast(''), 4200); return () => clearTimeout(id); }, [toast]);

  function signOut() { sessionStorage.removeItem('flowstateToken'); setUser(null); history.pushState({}, '', '/'); setPortal('landing'); setPage('user-home'); }
  const nav = portal === 'authority' ? AUTH_NAV : USER_NAV;
  const title = nav.flatMap(group => group.items).find(item => item.id === page)?.label || (portal === 'authority' ? 'Operations overview' : 'My commute');
  if (portal === 'landing') return <PortalLanding onChoose={mode => { history.pushState({}, '', mode === 'authority' ? '/authority' : '/user'); setPortal(mode); setPage(mode === 'authority' ? 'authority-home' : 'user-home'); }} />;
  if (portal === 'authority' && user?.role !== 'authority') return <PortalLogin role="authority" onUser={setUser} onBack={() => { history.pushState({}, '', '/'); setPortal('landing'); }} />;
  if (portal === 'user' && user?.role !== 'citizen') return <PortalLogin role="citizen" onUser={setUser} onBack={signOut} />;
  const places = data.segments.filter(s => s.latitude != null).map(s => ({ id: s.segment_id, label: `${s.road}, ${s.area}`, latitude: s.latitude, longitude: s.longitude, area: s.area, road: s.road }));
  const pins = readJson('flowstateJourneyPoints', {});
  const googleRouting = Boolean(data.mapConfig?.google_routing_configured);
  const badge = id => {
    if (portal === 'authority' && id === 'authority-map') return data.incidents.filter(x => x.status === 'reported').length;
    if (portal === 'authority' && id === 'alerts') return data.alerts.filter(a => a.status === 'open').length;
    if (portal === 'user' && id === 'notifications') return unread;
    return 0;
  };
  const commuterPages = {
    journeys: <StartJourney setToast={setToast} places={places} pins={pins} googleRouting={googleRouting} />,
    plan: <PlanTrips setToast={setToast} places={places} pins={pins} />,
    'forecast-road': <RoadForecast segments={data.segments} />,
    incidents: <Incidents setToast={setToast} />,
    notifications: <NotificationsPage onChange={setUnread} />,
    account: <AccountPage user={user} setToast={setToast} />,
  };
  return <div className="app-shell">
    <aside className={`sidebar ${mobileNav ? 'sidebar-open' : ''}`}>
      <div className="brand"><span className="brand-mark"><Activity size={21} /></span><span>flow<span>state</span><small>TRAFFIC INTELLIGENCE</small></span><button className="mobile-close icon-button" onClick={() => setMobileNav(false)} aria-label="Close menu"><X size={18} /></button></div>
      <div className="city-picker"><span className="city-icon"><MapPin size={16} /></span><span><b>Bengaluru</b><small>Karnataka, India</small></span><ChevronDown size={15} /></div>
      <div className="sidebar-scroll">{nav.map(group => <div className="nav-section" key={group.section}>
        <div className="nav-label">{group.section}</div>
        <nav>{group.items.map(item => <button key={item.id} className={`nav-item ${page === item.id ? 'active' : ''}`} onClick={() => { setPage(item.id); setMobileNav(false); }}>
          <item.icon size={18} /><span>{item.label}</span>{badge(item.id) > 0 && <i className="nav-count">{badge(item.id)}</i>}</button>)}</nav>
      </div>)}</div>
      <div className="coverage-note"><span className="coverage-signal" /><div><b>{portal === 'authority' ? 'Authority console' : 'FlowState commuter'}</b><p>{portal === 'authority' ? 'Review first, then publish alerts.' : 'Forecasts are estimates. Report only what you observe.'}</p></div></div>
      <div className="profile"><div className="avatar">{portal === 'authority' ? 'AO' : 'FS'}</div><div><b>{user?.username || 'Public access'}</b><small>{portal === 'authority' ? 'Authorized operations' : 'Bengaluru commuter'}</small></div><button className="profile-exit" onClick={signOut} aria-label="Sign out or switch portal"><SlidersHorizontal size={17} /></button></div>
    </aside>
    {mobileNav && <button className="scrim" onClick={() => setMobileNav(false)} aria-label="Close navigation" />}
    <main className="main-area">
      <header className="topbar"><div className="topbar-left"><button className="mobile-menu icon-button" onClick={() => setMobileNav(true)} aria-label="Open menu"><Menu size={20} /></button><span className="breadcrumb"><span className="crumb-root">{portal === 'authority' ? 'Authority console' : 'Commuter portal'}</span><span className="crumb-sep">/</span>{title}</span></div>
        <div className="top-actions"><span className={`api-state ${apiOnline === false ? 'api-state-offline' : ''}`}><i />{apiOnline === null ? 'Checking connection…' : apiOnline ? 'Connected' : 'Offline'}</span>
          <button className="refresh-button" onClick={refresh} disabled={busy}><RefreshCw size={15} className={busy ? 'spin' : ''} /><span>Refresh</span></button>
          <button className="account-button" onClick={signOut}><span className="account-dot">{user?.username?.slice(0, 1).toUpperCase()}</span><span className="account-name">{user?.username}</span><small>Sign out</small></button></div></header>
      <div className="page-wrap">
        {error && <div className="alert-banner"><AlertTriangle size={18} /><span><b>Could not load project data.</b> {error} Start the FastAPI server and refresh.</span><button onClick={() => setError('')}><X size={16} /></button></div>}
        {portal === 'user' && page !== 'user-home'
          ? <div className="commuter-workspace"><CommuterWorkspaceMap /><section className="commuter-workspace-panel">{commuterPages[page]}</section></div>
          : <>
            {page === 'user-home' && <UserHome data={data} navigate={setPage} />}
            {page === 'authority-home' && <AuthorityHome data={data} navigate={setPage} />}
            {page === 'zones' && <ZoneMonitor />}
            {page === 'authority-map' && <AuthorityIncidentMap data={data} refresh={refresh} setToast={setToast} />}
            {page === 'notices' && <NoticeDesk setToast={setToast} places={places} refresh={refresh} />}
            {page === 'alerts' && <AlertsDesk setToast={setToast} />}
            {page === 'broadcasts' && <Broadcasts data={data} refresh={refresh} setToast={setToast} />}
            {page === 'demand' && <DemandDesk />}
            {page === 'coverage' && <CoverageDesk setToast={setToast} googleRouting={googleRouting} />}
            {page === 'data' && <DataDesk setToast={setToast} segments={data.segments} refresh={refresh} />}
            {page === 'models' && <ModelsDesk setToast={setToast} segments={data.segments} />}
            {page === 'cameras' && <CameraScreen data={data} refresh={refresh} setToast={setToast} user={user} />}
            {page === 'overview' && <Overview data={data} navigate={setPage} />}
            {page === 'access' && <AccessDesk setToast={setToast} user={user} />}
            {page === 'map' && <TrafficMap data={data} />}
          </>}
        <footer className="page-footer"><span>FlowState · Final year research project</span><span>Historical dataset · Bengaluru</span></footer>
      </div>
    </main>
    {toast && <div className="toast" role="status"><Check size={17} />{toast}</div>}
  </div>;
}

function PortalLanding({ onChoose }) {
  return <main className="portal-landing"><nav className="landing-nav"><a className="brand landing-brand" href="/"><span className="brand-mark"><Activity size={21} /></span><span>flow<span>state</span><small>TRAFFIC INTELLIGENCE</small></span></a><span className="landing-location"><MapPin size={15} /> Bengaluru, India</span></nav>
    <section className="landing-hero"><div className="landing-copy"><div className="eyebrow">A CLEARER WAY THROUGH THE CITY</div><h1>Move with a little more <em>confidence.</em></h1><p>Plan your route, share road incidents, and see research-based congestion forecasts for Bengaluru.</p>
      <div className="landing-actions"><button className="button button-primary" onClick={() => onChoose('user')}>Continue as a commuter <ArrowRight size={16} /></button><button className="authority-link" onClick={() => onChoose('authority')}><ShieldCheck size={16} /> Authority sign in</button></div>
      <div className="landing-note"><span className="coverage-signal" /> Forecasts, community reports and official notices are clearly labelled.</div></div>
      <div className="landing-art"><div className="art-orbit orbit-one" /><div className="art-orbit orbit-two" /><div className="art-center"><Activity size={40} /></div><span className="art-pin pin-one"><MapPin size={17} /></span><span className="art-pin pin-two"><Navigation size={17} /></span><span className="art-pin pin-three"><AlertTriangle size={17} /></span><div className="art-card"><span className="coverage-signal" /><span><b>City pulse</b><small>Traffic insight · Bengaluru</small></span><TrendingUp size={19} /></div></div></section>
    <section className="landing-features"><div><Route /><b>Plan your trip</b><small>Compare routes and get alerts if things change.</small></div><div><AlertTriangle /><b>Report an incident</b><small>Help authorities notice road issues.</small></div><div><TrendingUp /><b>Check a road</b><small>See a forecast with its method and confidence.</small></div></section>
    <footer className="landing-footer">FLOWSTATE · COLLABORATIVE TRAFFIC MONITORING <span>Research prototype · estimates are not live sensor readings</span></footer></main>;
}

function PortalLogin({ role, onUser, onBack }) {
  const authority = role === 'authority';
  const [mode, setMode] = useState('login'), [login, setLogin] = useState(''), [password, setPassword] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  async function submit(e) {
    e.preventDefault(); setError(''); setBusy(true);
    try {
      // Everyone signs in with an email; the authority account set up in .env may use its username instead.
      const identifier = login.trim();
      const credentials = mode === 'register' || identifier.includes('@') ? { email: identifier } : { username: identifier };
      const result = await api(mode === 'login' ? '/api/auth/login' : '/api/auth/register', { method: 'POST', body: JSON.stringify({ ...credentials, password }) });
      if (result.user?.role !== (authority ? 'authority' : 'citizen')) throw new Error(authority ? 'This account is not an authority account.' : 'Use the authority sign-in page for an authority account.');
      sessionStorage.setItem('flowstateToken', result.access_token);
      onUser(result.user);
    } catch (err) {
      sessionStorage.removeItem('flowstateToken');
      setError(err.message === 'Failed to fetch' ? 'Could not connect. Check that the project is running, then try again.' : err.message);
    } finally { setBusy(false); }
  }
  return <main className="portal-login"><button className="login-back" onClick={onBack}><ArrowRight size={15} /> Back to portal selection</button><section className="login-card"><a className="brand landing-brand" href="/"><span className="brand-mark"><Activity size={21} /></span><span>flow<span>state</span><small>TRAFFIC INTELLIGENCE</small></span></a><span className={`login-emblem ${authority ? 'authority-emblem' : ''}`}>{authority ? <ShieldCheck size={22} /> : <Navigation size={22} />}</span><div className="eyebrow">{authority ? 'RESTRICTED AUTHORITY ACCESS' : 'COMMUTER PORTAL'}</div><h1>{authority ? 'Authority sign in' : mode === 'register' ? 'Create your commuter account' : 'Welcome back'}</h1><p>{authority ? 'Sign in with an authority account. Officials can request access from a commuter account.' : 'Sign in to use trip planning and community reporting.'}</p>
    <form onSubmit={submit}>{authority
      ? <label>Email or username<input autoComplete="username" required minLength="3" value={login} onChange={e => setLogin(e.target.value)} placeholder="Authority email or username" /></label>
      : <label>Email<input type="email" autoComplete="email" required value={login} onChange={e => setLogin(e.target.value)} placeholder="you@example.com" /></label>}<label>Password<input type="password" autoComplete={mode === 'login' ? 'current-password' : 'new-password'} minLength={mode === 'register' ? 10 : 1} required value={password} onChange={e => setPassword(e.target.value)} placeholder={mode === 'register' ? 'At least 10 characters' : 'Your password'} /></label>{error && <div className="inline-error">{error}</div>}<button className="button button-primary auth-submit" disabled={busy}>{busy ? 'Checking…' : mode === 'register' ? 'Create commuter account' : 'Sign in securely'}</button></form>
    {!authority && <button className="auth-switch" onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError(''); }}>{mode === 'login' ? 'New here? Create an account' : 'Already have an account? Sign in'}</button>}<div className="login-foot"><ShieldCheck size={14} />{authority ? 'Authority access is granted only after verification by an existing authority.' : 'Your personal trip details remain private.'}</div></section><span className="login-watermark">FLOWSTATE · BENGALURU</span></main>;
}

function PageHeading({ eyebrow, title, subtitle, action }) { return <div className="page-heading"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{subtitle}</p></div>{action}</div>; }

// ---------- commuter home ----------

function UserHome({ data, navigate }) {
  const [pins, setPins] = useState(() => readJson('flowstateJourneyPoints', {}));
  const [pickMode, setPickMode] = useState('');
  const [zones, setZones] = useState([]);
  const [notices, setNotices] = useState([]);
  useEffect(() => {
    api('/api/forecast/zones').then(b => setZones(b.zones)).catch(() => setZones([]));
    api('/api/incidents/public').then(b => setNotices(b.incidents)).catch(() => setNotices([]));
  }, []);
  const choosePoint = useCallback(point => {
    setPins(previous => {
      const next = { ...previous, [pickMode]: { ...point, label: `Map point (${point.latitude.toFixed(5)}, ${point.longitude.toFixed(5)})` } };
      writeJson('flowstateJourneyPoints', next);
      return next;
    });
    setPickMode('');
  }, [pickMode]);
  const options = [['journeys', Navigation, 'Start a journey', 'Compare routes, risk and departure time.'], ['plan', Route, 'Plan a trip', 'Save a future trip and get alerts.'], ['forecast-road', TrendingUp, 'Road forecast', 'Expected congestion for a road and time.'], ['incidents', AlertTriangle, 'Report an incident', 'Share a road issue with the authority.']];
  const busiest = [...zones].sort((a, b) => b.expected_load - a.expected_load).slice(0, 3);
  return <><PageHeading eyebrow="YOUR BENGALURU COMMUTE" title="Where are you headed?" subtitle="Zones show the current congestion outlook; pins show verified incidents and official notices." />
    <section className="commuter-map-layout"><div className="commuter-map-card"><CommuterMap pickMode={pickMode} onPick={choosePoint} pins={pins} zones={zones} notices={notices} />{pickMode && <div className="map-pick-banner">Zoom in, then tap the exact spot for your {pickMode === 'origin' ? 'starting point' : 'destination'}. <button onClick={() => setPickMode('')}>Cancel</button></div>}
      <div className="map-legend">{Object.keys(LEVELS).map(name => <LevelBadge key={name} value={name} compact />)}</div></div>
      <aside className="commuter-tools-panel"><div className="commuter-panel-head"><span className="welcome-kicker"><Activity size={14} /> FLOWSTATE COMMUTER</span><h2>Plan around your day</h2><p>Choose exact points on the map, then start your route.</p></div>
        <div className="pin-pickers">{['origin', 'destination'].map(key => <button key={key} className={`pin-picker ${pickMode === key ? 'pin-picker-active' : ''}`} onClick={() => setPickMode(key)}><span className={`pin-picker-dot ${key}-dot`} /><span><small>{key === 'origin' ? 'STARTING POINT' : 'DESTINATION'}</small><b>{pins[key]?.label || 'Choose on map'}</b></span><MapPin size={16} /></button>)}</div>
        <div className="commuter-tool-list">{options.map(([id, Icon, title, desc]) => <button className="commuter-tool" key={id} onClick={() => navigate(id)}><span><Icon size={18} /></span><div><b>{title}</b><small>{desc}</small></div><ArrowRight size={16} /></button>)}</div>
        <div className="commuter-alert-mini"><div><b>Busiest zones now</b><span>{zones.length} zones</span></div>{busiest.map(z => <p key={z.zone_id}><LevelBadge value={z.level} compact /> {z.name}</p>)}{!zones.length && <small>Zone outlook is loading or unavailable.</small>}</div>
        <div className="commuter-alert-mini"><div><b>City alerts</b><span>{data.broadcasts?.length || 0} active</span></div>{data.broadcasts?.slice(0, 2).map(alert => <p key={alert.broadcast_id}><AlertTriangle size={13} />{alert.message}</p>)}{!data.broadcasts?.length && <small>No active alerts right now.</small>}</div>
        <small className="commuter-map-note">Forecasts are research estimates; each one names its method and confidence.</small></aside></section></>;
}

function CommuterMap({ pickMode, onPick, pins, zones = [], notices = [] }) {
  const containerRef = useRef(null), itemsRef = useRef([]);
  const { map, mapError } = useGoogleMap(containerRef, 12);
  useEffect(() => {
    if (!map) return;
    const listener = map.addListener('click', event => { if (pickMode && event.latLng) onPick({ latitude: event.latLng.lat(), longitude: event.latLng.lng() }); });
    map.setOptions({ draggableCursor: pickMode ? 'crosshair' : null });
    return () => listener.remove();
  }, [map, pickMode, onPick]);
  useEffect(() => {
    if (!map) return;
    itemsRef.current.forEach(item => item.setMap(null));
    itemsRef.current = [];
    for (const zone of zones.filter(z => z.latitude != null)) {
      const meta = level(zone.level);
      itemsRef.current.push(new window.google.maps.Circle({ map, center: { lat: zone.latitude, lng: zone.longitude }, radius: zone.radius_m || 2000, strokeColor: meta.color, strokeOpacity: 0.8, strokeWeight: 2, fillColor: meta.color, fillOpacity: 0.1, clickable: false }));
    }
    for (const notice of notices) {
      const marker = new window.google.maps.Marker({ map, position: { lat: notice.latitude, lng: notice.longitude }, title: CATEGORY_LABELS[notice.category] || notice.category });
      const info = new window.google.maps.InfoWindow({ content: infoContent(CATEGORY_LABELS[notice.category] || notice.category, [notice.official ? 'Official notice' : 'Verified report', notice.closure ? 'Road closed' : null, notice.details, notice.expires_at ? `Until ${dateTime(notice.expires_at)}` : null]) });
      marker.addListener('click', () => info.open({ map, anchor: marker }));
      itemsRef.current.push(marker);
    }
    for (const [key, color, title] of [['origin', '#087e83', 'Starting point'], ['destination', '#d47763', 'Destination']]) {
      const point = pins?.[key];
      if (point) itemsRef.current.push(new window.google.maps.Marker({ map, position: { lat: Number(point.latitude), lng: Number(point.longitude) }, title, icon: { path: window.google.maps.SymbolPath.CIRCLE, scale: 8, fillColor: color, fillOpacity: 1, strokeColor: '#fff', strokeWeight: 3 } }));
    }
    return () => itemsRef.current.forEach(item => item.setMap(null));
  }, [map, pins, zones, notices]);
  return <><div className="commuter-map" id="commuter-home-map" ref={containerRef} aria-label="Interactive Bengaluru map" /><MapSetupNotice message={mapError} /></>;
}

function CommuterWorkspaceMap() {
  const [pins] = useState(() => readJson('flowstateJourneyPoints', {}));
  return <section className="commuter-workspace-map"><div className="commuter-map-card"><CommuterMap pickMode="" onPick={() => {}} pins={pins} /><div className="workspace-map-label"><MapPin size={14} /> Bengaluru map</div></div></section>;
}

// ---------- authority screens ----------

function AuthorityHome({ data, navigate }) {
  const open = data.incidents.filter(x => x.status === 'reported').length;
  const reports = data.incidents.filter(x => x.status !== 'duplicate');  // duplicates are counted on the original
  const active = data.models.find(m => m.status === 'active');
  return <><PageHeading eyebrow="RESTRICTED OPERATIONS CONSOLE" title="City operations" subtitle="Alerts, reports, notices, and the forecasting pipeline at a glance." action={<span className="authority-badge"><ShieldCheck size={14} /> AUTHORIZED</span>} />
    <div className="authority-banner"><div><span className="welcome-kicker"><Activity size={14} /> OPERATIONS DESK</span><h2>Review first. Publish with confidence.</h2><p>Community reports are unverified until reviewed by your team.</p></div><button className="button button-light" onClick={() => navigate('alerts')}>Open alerts <ArrowRight size={15} /></button></div>
    <section className="metric-grid authority-metrics"><Metric icon={AlertTriangle} label="Awaiting review" value={n(open)} sub="Community reports not yet verified" tone="orange" /><Metric icon={Bell} label="Active alerts" value={n(data.alerts.length)} sub="Zones, closures, hotspots, data quality" tone="violet" /><Metric icon={Camera} label="Registered cameras" value={n(data.cameras.length)} sub={`${data.cameras.filter(c => c.has_stream).length} with live streams`} tone="blue" /><Metric icon={Cpu} label="Forecasting model" value={active ? 'Active' : 'Patterns'} sub={active ? active.version : 'No accepted model in use'} tone="green" /></section>
    <section className="authority-actions"><button className="authority-action" onClick={() => navigate('zones')}><span><Layers /></span><div><b>Zone monitor</b><small>Congestion outlook by zone and road.</small></div><ArrowRight /></button><button className="authority-action" onClick={() => navigate('notices')}><span><Radio /></span><div><b>Official notices</b><small>VIP movement, closures, metro work, diversions.</small></div><ArrowRight /></button><button className="authority-action" onClick={() => navigate('demand')}><span><Users /></span><div><b>Planned demand</b><small>Future trips, corridors and hotspots.</small></div><ArrowRight /></button></section>
    <section className="card report-list-card"><div className="card-heading"><div><h2>Active alerts</h2><p>Newest first</p></div><button className="text-action" onClick={() => navigate('alerts')}>All alerts <ArrowRight size={14} /></button></div>
      {data.alerts.length === 0 ? <div className="empty-state"><b>No active alerts</b></div> : <div className="alert-list">{data.alerts.slice(0, 4).map(a => <article className={`ops-alert ops-${a.level}`} key={a.alert_id}><span className="alert-severity"><AlertTriangle size={15} /></span><div><b>{a.title}</b><p>{a.message}</p>{a.recommendation && <p className="recommendation"><b>Recommended:</b> {a.recommendation}</p>}</div></article>)}</div>}</section>
    <section className="card report-list-card"><div className="card-heading"><div><h2>Latest incoming reports</h2><p>Location and verification state</p></div><button className="text-action" onClick={() => navigate('authority-map')}>Open incident map <ArrowRight size={14} /></button></div><div className="report-list">{reports.slice(0, 4).map(item => <ReportItem key={item.incident_id} item={item} />)}{!reports.length && <div className="empty-state"><b>No reports received yet</b></div>}</div></section></>;
}

function AuthorityIncidentMap({ data, refresh, setToast }) {
  const containerRef = useRef(null), markersRef = useRef([]);
  const { map, mapRef, mapError } = useGoogleMap(containerRef, 11);
  const [filter, setFilter] = useState('reported');
  const [busyId, setBusyId] = useState('');
  const shown = data.incidents.filter(x => filter === 'all' || x.status === filter);
  const located = shown.filter(x => x.latitude != null && x.longitude != null);
  useEffect(() => {
    if (!map) return;
    markersRef.current.forEach(marker => marker.setMap(null));
    markersRef.current = [];
    located.forEach(item => {
      const marker = new window.google.maps.Marker({ map, position: { lat: Number(item.latitude), lng: Number(item.longitude) }, title: CATEGORY_LABELS[item.category] || item.category });
      const info = new window.google.maps.InfoWindow({ content: infoContent(CATEGORY_LABELS[item.category] || item.category, [[item.area, item.road].filter(Boolean).join(' · '), item.status, item.corroboration_count ? `${item.corroboration_count} confirmation${item.corroboration_count === 1 ? '' : 's'}` : null]) });
      marker.addListener('click', () => info.open({ map, anchor: marker }));
      markersRef.current.push(marker);
    });
    if (located.length) {
      const bounds = new window.google.maps.LatLngBounds();
      located.forEach(item => bounds.extend({ lat: Number(item.latitude), lng: Number(item.longitude) }));
      if (located.length === 1) { map.setCenter(bounds.getCenter()); map.setZoom(14); } else map.fitBounds(bounds, 40);
    }
    return () => markersRef.current.forEach(marker => marker.setMap(null));
  }, [map, data.incidents, filter]);
  async function review(item, status) {
    setBusyId(item.incident_id);
    try { await api(`/api/incidents/${item.incident_id}`, { method: 'PATCH', body: JSON.stringify({ status, official_note: '' }) }); await refresh(); setToast(`Incident marked ${status}.`); }
    catch (e) { setToast(e.message); } finally { setBusyId(''); }
  }
  return <><PageHeading eyebrow="INCIDENT COMMAND" title="Reported incident map" subtitle="Reports with coordinates are pinned. Review each report before making it official." action={<span className="authority-badge"><ShieldCheck size={14} /> AUTHORITY ONLY</span>} />
    <div className="incident-map-layout"><section className="map-frame authority-map-frame"><div id="authority-incident-map" ref={containerRef} /><MapSetupNotice message={mapError} /><div className="map-overlay"><MapPin size={14} /> {located.length} mapped · {shown.length - located.length} without coordinates</div></section>
      <aside className="card authority-incident-panel"><div className="card-heading"><div><h2>Report queue</h2><p>Review and update incident status</p></div><select className="compact-select" value={filter} onChange={e => setFilter(e.target.value)}><option value="reported">Needs review</option><option value="verified">Verified</option><option value="resolved">Resolved</option><option value="stale">Stale</option><option value="all">All reports</option></select></div>
        <div className="report-list">{shown.map(item => <article className="authority-report" key={item.incident_id}><ReportItem item={item} /><div className="authority-report-actions">{item.latitude != null && <button className="text-action" onClick={() => { mapRef.current?.setCenter({ lat: Number(item.latitude), lng: Number(item.longitude) }); mapRef.current?.setZoom(16); }}><LocateFixed size={14} /> Locate</button>}
          {item.status === 'reported' && <><button className="button button-primary" disabled={busyId === item.incident_id} onClick={() => review(item, 'verified')}><Check size={13} /> Verify</button><button className="button button-secondary" disabled={busyId === item.incident_id} onClick={() => review(item, 'rejected')}><X size={13} /> Reject</button></>}
          {item.status === 'verified' && <button className="button button-secondary" disabled={busyId === item.incident_id} onClick={() => review(item, 'resolved')}><Check size={13} /> Resolve</button>}</div></article>)}
          {!shown.length && <div className="empty-state"><b>No reports in this queue</b><small>New commuter reports appear here.</small></div>}</div></aside></div></>;
}

function Broadcasts({ data, refresh, setToast }) {
  const [incidentId, setIncidentId] = useState(''), [message, setMessage] = useState(''), [severity, setSeverity] = useState('warning'), [busy, setBusy] = useState(false);
  const verified = data.incidents.filter(x => x.status === 'verified');
  async function submit(e) {
    e.preventDefault(); setBusy(true);
    try { await api(`/api/incidents/${incidentId}/broadcast`, { method: 'POST', body: JSON.stringify({ message, severity }) }); setMessage(''); await refresh(); setToast('Public alert is now active.'); }
    catch (err) { setToast(err.message); } finally { setBusy(false); }
  }
  return <><PageHeading eyebrow="PUBLIC INFORMATION DESK" title="Broadcast an incident" subtitle="Only incidents verified by an authority can be announced to commuters." />
    <div className="broadcast-layout"><section className="card form-card"><div className="card-heading"><div><h2>Compose city alert</h2><p>Keep the wording clear and include practical route advice.</p></div><Bell className="muted-icon" /></div>
      <form className="incident-form" onSubmit={submit}><label>Verified incident<select required value={incidentId} onChange={e => setIncidentId(e.target.value)}><option value="">Choose verified report</option>{verified.map(x => <option key={x.incident_id} value={x.incident_id}>{CATEGORY_LABELS[x.category] || x.category} · {[x.area, x.road].filter(Boolean).join(' / ') || x.incident_id.slice(0, 8)}</option>)}</select></label>
        <label>Alert message<textarea required minLength="10" maxLength="500" rows="4" value={message} onChange={e => setMessage(e.target.value)} placeholder="What happened, where, and what should drivers do?" /></label>
        <label>Severity<select value={severity} onChange={e => setSeverity(e.target.value)}><option value="info">Information</option><option value="warning">Warning</option><option value="critical">Critical</option></select></label>
        <button className="button button-primary" disabled={busy || !verified.length}><Bell size={15} />{busy ? 'Publishing…' : 'Publish alert'}</button>{!verified.length && <small className="form-hint">There are no verified incidents ready to broadcast.</small>}</form></section>
      <section className="card report-list-card"><div className="card-heading"><div><h2>Active public alerts</h2><p>Visible on the commuter portal</p></div><span className="pill pill-neutral">{data.broadcasts?.length || 0} ACTIVE</span></div><div className="alert-list">{data.broadcasts?.map(alert => <article className={`public-alert severity-${alert.severity}`} key={alert.broadcast_id}><span className="alert-severity"><Bell size={15} /></span><div><b>{alert.message}</b><small>{[alert.area, alert.road].filter(Boolean).join(' · ') || 'Bengaluru'} · {dateTime(alert.created_at)}</small></div><span className="status-chip status-orange">{alert.severity}</span></article>)}</div>{!data.broadcasts?.length && <div className="empty-state"><b>No active alerts</b><small>Published alerts appear on the commuter home screen.</small></div>}</section></div></>;
}

function Overview({ data, navigate }) {
  const summary = data.summary;
  const totals = summary?.traffic_situation_counts || {};
  const labeled = ['High', 'Medium', 'Low'].reduce((sum, key) => sum + (totals[key] || 0), 0);
  const latest = data.models[0];
  const active = data.models.find(m => m.status === 'active');
  return <>
    <PageHeading eyebrow={`${todayLabel} · BENGALURU`} title="Traffic data" subtitle="The historical dataset behind the forecasts, and the current model." action={<button className="button button-primary" onClick={() => navigate('coverage')}><Database size={16} /> Data coverage</button>} />
    <div className="notice-strip"><span className="notice-icon"><Activity size={16} /></span><span><b>Historical data view</b><small>Dataset: {longDate(summary?.date_start)} – {longDate(summary?.date_end)}. Not live traffic.{data.synthetic?.available ? ` A separate synthetic dataset contains ${n(data.synthetic.record_count)} simulated records.` : ''}</small></span><span className="notice-badge">RESEARCH</span></div>
    <section className="metric-grid">
      <Metric icon={Database} label="Historical observations" value={n(summary?.record_count)} sub="Validated rows from the dataset" tone="blue" />
      <Metric icon={MapPin} label="Roads" value={n(summary?.road_count)} sub={`${n(data.segments.filter(s => s.has_map_location).length)} placed on the map`} tone="violet" />
      <Metric icon={ShieldCheck} label="Roads with verified records" value={n(data.coverage.filter(x => x.verified_coverage).length)} sub="Authority-verified observations" tone="green" />
      <Metric icon={AlertTriangle} label="Open community reports" value={n(data.incidents.filter(x => x.status === 'reported').length)} sub="Awaiting authority verification" tone="orange" />
    </section>
    <section className="overview-grid">
      <div className="card traffic-card"><div className="card-heading"><div><h2>Congestion levels in the dataset</h2><p>Share of labelled historical records</p></div></div>
        <div className="meter-list">{['Low', 'Medium', 'High'].map(name => <div className="meter-row" key={name} title={`${name}: ${n(totals[name])} records`}><span className="meter-label"><LevelBadge value={name} compact /></span><span className="meter-track" style={{ background: level(name).track }}><i style={{ width: `${labeled ? (totals[name] || 0) / labeled * 100 : 0}%`, background: level(name).color }} /></span><b className="meter-value">{labeled ? Math.round((totals[name] || 0) / labeled * 100) : 0}%</b></div>)}</div>
        <div className="chart-footnote"><AlertTriangle size={14} /> {n(labeled)} labelled records. Labels come from the dataset and are not sensor-verified.</div></div>
      <div className="card model-card"><div className="card-heading"><div><h2>Forecasting model</h2><p>Hybrid LSTM → Random Forest</p></div><span className="pill pill-warn">{active ? 'ACTIVE' : 'NOT ACTIVE'}</span></div>
        {latest ? <><div className="model-score"><span className="score-number">{latest.test.macro_f1?.toFixed(3) ?? '—'}</span><span className="score-label">test macro-F1 ({latest.version})</span></div>
          <div className="baseline-row"><span>Best baseline</span><b>{Math.max(...Object.values(latest.baselines_test).map(b => b.macro_f1 || 0)).toFixed(3)}</b></div>
          <div className="model-disclaimer"><AlertTriangle size={16} /><span>{latest.acceptance.passed ? 'Passed the acceptance checks.' : 'Did not pass the acceptance checks, so live forecasts use historical patterns.'}</span></div></> : <div className="empty-state"><b>No model trained yet</b></div>}
        <button className="button button-secondary full-button" onClick={() => navigate('models')}>View model evaluation <ArrowRight size={15} /></button></div>
    </section>
  </>;
}
function Metric({ icon: Icon, label, value, sub, tone }) { return <div className="card metric-card"><div className={`metric-icon ${tone}`}><Icon size={18} /></div><div className="metric-label">{label}</div><div className="metric-value">{value}</div><div className="metric-sub">{sub}</div></div>; }

function TrafficMap({ data }) {
  const containerRef = useRef(null), markerRef = useRef(null);
  const { mapRef, mapError } = useGoogleMap(containerRef, 11);
  const [query, setQuery] = useState(''), [searching, setSearching] = useState(false), [selection, setSelection] = useState(null), [queryError, setQueryError] = useState('');
  async function search(e) {
    e.preventDefault();
    if (query.trim().length < 3) return;
    setSearching(true); setQueryError('');
    try {
      const r = await api(`/api/geocode?q=${encodeURIComponent(query.trim())}`);
      if (!r.results?.length) throw new Error('No matching place found.');
      const place = r.results[0];
      const lat = Number(place.latitude), lng = Number(place.longitude);
      if (!mapRef.current) throw new Error('The Google map is still loading. Try again in a moment.');
      mapRef.current.setCenter({ lat, lng }); mapRef.current.setZoom(16);
      if (markerRef.current) markerRef.current.setMap(null);
      markerRef.current = new window.google.maps.Marker({ map: mapRef.current, position: { lat, lng }, title: place.display_name });
      const content = document.createElement('div');
      content.textContent = place.display_name;
      new window.google.maps.InfoWindow({ content }).open({ map: mapRef.current, anchor: markerRef.current });
      setSelection(place);
    } catch (err) { setQueryError(err.message); } finally { setSearching(false); }
  }
  return <><PageHeading eyebrow="CITY EXPLORER" title="Place search" subtitle="Look up places in Bengaluru. Being on the map does not imply traffic-data coverage." action={<span className="pill pill-neutral">{data.coverage.length} roads with data</span>} />
    <div className="map-toolbar"><form className="search-box" onSubmit={search}><Search size={18} /><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search an address or landmark in Bengaluru" /><button disabled={searching}>{searching ? 'Searching…' : 'Search'}</button></form><button className="button button-secondary" onClick={() => { mapRef.current?.setCenter({ lat: 12.9716, lng: 77.5946 }); mapRef.current?.setZoom(11); }}><LocateFixed size={16} /> Reset city view</button></div>
    {queryError && <div className="inline-error">{queryError}</div>}
    <div className="map-layout"><div className="map-frame"><div id="traffic-map" ref={containerRef} /><MapSetupNotice message={mapError} /></div><aside className="card map-side"><div className="map-side-icon"><MapIcon size={20} /></div><h2>Coverage at a glance</h2><p>{data.segments.filter(s => s.has_map_location).length} roads are placed on the map; {data.coverage.length} of them have traffic records.</p>{selection && <div className="selected-place"><b>Selected location</b><span>{selection.display_name}</span><small>{Number(selection.latitude).toFixed(5)}, {Number(selection.longitude).toFixed(5)}</small></div>}<p className="map-attribution">Map display and place lookup by Google Maps Platform.</p></aside></div></>;
}

// ---------- cameras ----------

function CameraScreen({ data, refresh, setToast, user }) {
  const blank = { camera_id: '', display_name: '', area: '', road: '', latitude: '', longitude: '', source_type: 'authorized_cctv', authorization_status: 'pending', operational_status: 'unknown', stream_url: '', collection_interval_seconds: 300, sample_seconds: 20, line_y: '' };
  const [registry, setRegistry] = useState(blank);
  const [editingCamera, setEditingCamera] = useState('');
  const [cameraId, setCameraId] = useState(''), [file, setFile] = useState(null), [dateValue, setDateValue] = useState(() => localISODate()), [block, setBlock] = useState('Morning'), [busy, setBusy] = useState(false), [analysis, setAnalysis] = useState(null), [collecting, setCollecting] = useState('');
  const isAuthority = user?.role === 'authority';
  const coordinate = value => value === '' || value == null ? null : Number(value);
  async function addCamera(e) {
    e.preventDefault(); setBusy(true); setAnalysis(null);
    try {
      const { line_y: lineY, ...fields } = registry;
      await api('/api/cameras', { method: 'POST', body: JSON.stringify({ ...fields, latitude: coordinate(fields.latitude), longitude: coordinate(fields.longitude), stream_url: fields.stream_url.trim() || null,
        collection_interval_seconds: Number(fields.collection_interval_seconds), sample_seconds: Number(fields.sample_seconds),
        counting_line: lineY === '' ? null : [[0, Number(lineY)], [1, Number(lineY)]] }) });
      setRegistry(blank); setEditingCamera(''); await refresh();
      setToast('Camera details saved. Approved, online cameras can be used for clip review and stream collection.');
    } catch (err) { setAnalysis({ error: err.message }); } finally { setBusy(false); }
  }
  async function analyze(e) {
    e.preventDefault(); setAnalysis(null);
    if (!cameraId) { setAnalysis({ error: 'Choose an approved camera source first.' }); return; }
    if (!file) { setAnalysis({ error: 'Choose a video clip first.' }); return; }
    setBusy(true);
    try {
      const body = new FormData(); body.append('video', file); body.append('observed_date', dateValue); body.append('time_block', block);
      const result = await api(`/api/cameras/${encodeURIComponent(cameraId)}/analyze-video`, { method: 'POST', body });
      setAnalysis(result); await refresh(); setToast('Footage reviewed and saved for authority follow-up.');
    } catch (err) {
      const message = err.message || '';
      setAnalysis({ error: /401|token|sign.?in/i.test(message) ? 'Your authority sign-in may have expired. Sign in again, then retry.' : /YOLO|ultralytics|OpenCV/i.test(message) ? 'Video review is not ready on this computer. Please ask the project administrator for help.' : message });
    } finally { setBusy(false); }
  }
  async function collect(camera) {
    setCollecting(camera.camera_id);
    try { const r = await api(`/api/cameras/${encodeURIComponent(camera.camera_id)}/collect`, { method: 'POST' }); setToast(r.ok ? `Measured ${r.tracked_vehicles} vehicles${r.line_crossings != null ? `, ${r.line_crossings} crossed the line` : ''}.` : `Collection failed: ${r.error}`); await refresh(); }
    catch (err) { setToast(err.message); } finally { setCollecting(''); }
  }
  const eligible = data.cameras.filter(x => x.authorization_status === 'approved' && x.operational_status === 'online' && x.area && x.road);
  const edit = id => {
    const cam = data.cameras.find(x => x.camera_id === id);
    setEditingCamera(id);
    if (cam) setRegistry({ camera_id: cam.camera_id, display_name: cam.display_name, area: cam.area || '', road: cam.road || '', latitude: cam.latitude ?? '', longitude: cam.longitude ?? '', source_type: cam.source_type, authorization_status: cam.authorization_status, operational_status: cam.operational_status, stream_url: '', collection_interval_seconds: cam.collection_interval_seconds || 300, sample_seconds: cam.sample_seconds || 20, line_y: cam.counting_line ? cam.counting_line[0][1] : '' });
    else setRegistry(blank);
  };
  const set = (key, value) => setRegistry({ ...registry, [key]: value });
  return <><PageHeading eyebrow="CAMERA SOURCES" title="CCTV review and live streams" subtitle="Register authorized cameras. Review uploaded clips, or let approved streams be sampled automatically." action={<span className={`pill ${isAuthority ? 'pill-neutral' : 'pill-warn'}`}><ShieldCheck size={14} />{isAuthority ? 'AUTHORITY ACCESS' : 'AUTHORITY SIGN-IN REQUIRED'}</span>} />
    <div className="model-warning"><AlertTriangle size={19} /><span><b>Measurements are estimates.</b> Counts come from vehicle detection and tracking; flow needs a counting line. Stream video is analysed and not stored. Use only feeds your authority is permitted to use.</span></div>
    <div className="camera-grid"><section className="card form-card"><div className="card-heading"><div><h2>Camera registry</h2><p>Location, approval, and optional live stream.</p></div><Camera size={19} className="muted-icon" /></div>
      <form className="incident-form" onSubmit={addCamera}>{data.cameras.length > 0 && <label>Update an existing camera<select value={editingCamera} onChange={e => edit(e.target.value)}><option value="">Add a new camera</option>{data.cameras.map(cam => <option key={cam.camera_id} value={cam.camera_id}>{cam.display_name} · {cam.road || 'road not set'}</option>)}</select></label>}
        <div className="form-pair"><label>Camera ID<input required minLength="2" maxLength="80" value={registry.camera_id} onChange={e => set('camera_id', e.target.value)} placeholder="e.g. cam-jayanagar-01" /></label><label>Display name<input required minLength="2" value={registry.display_name} onChange={e => set('display_name', e.target.value)} placeholder="Jayanagar junction" /></label></div>
        <div className="form-pair"><label>Area<input required value={registry.area} onChange={e => set('area', e.target.value)} /></label><label>Road<input required value={registry.road} onChange={e => set('road', e.target.value)} /></label></div>
        <div className="form-pair"><label>Latitude <small>optional</small><input type="number" step="any" min="-90" max="90" required={registry.longitude !== ''} value={registry.latitude} onChange={e => set('latitude', e.target.value)} placeholder="12.9716" /></label><label>Longitude <small>optional</small><input type="number" step="any" min="-180" max="180" required={registry.latitude !== ''} value={registry.longitude} onChange={e => set('longitude', e.target.value)} placeholder="77.5946" /></label></div>
        <div className="form-pair"><label>Source type<select value={registry.source_type} onChange={e => set('source_type', e.target.value)}>{[['authorized_cctv', 'Authorized CCTV'], ['approved_ip_camera', 'Approved IP camera'], ['permitted_integration', 'Permitted integration']].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label><label>Authorization<select value={registry.authorization_status} onChange={e => set('authorization_status', e.target.value)}><option value="pending">Pending review</option><option value="approved">Approved</option><option value="revoked">Revoked</option></select></label></div>
        <label>Operational status<select value={registry.operational_status} onChange={e => set('operational_status', e.target.value)}><option value="unknown">Unknown</option><option value="online">Online</option><option value="offline">Offline</option></select></label>
        <label>Stream address <small>{editingCamera ? 'leave empty to keep the saved address' : 'optional: rtsp://, http(s)://'}</small><input value={registry.stream_url} onChange={e => set('stream_url', e.target.value)} placeholder="rtsp://camera.example/stream" /></label>
        <div className="form-pair"><label>Sample every<select value={registry.collection_interval_seconds} onChange={e => set('collection_interval_seconds', e.target.value)}><option value={60}>1 minute</option><option value={300}>5 minutes</option><option value={900}>15 minutes</option></select></label><label>Seconds per sample<input type="number" min="5" max="120" value={registry.sample_seconds} onChange={e => set('sample_seconds', e.target.value)} /></label></div>
        <label>Counting line <small>horizontal line height, 0 (top) to 1 (bottom); empty = no flow estimate</small><input type="number" min="0" max="1" step="0.05" value={registry.line_y} onChange={e => set('line_y', e.target.value)} placeholder="0.6" /></label>
        <button className="button button-primary" disabled={busy || !isAuthority}><Plus size={15} />{editingCamera ? 'Save camera changes' : 'Save camera source'}</button></form></section>
      <section className="card form-card"><div className="card-heading"><div><h2>Review a video clip</h2><p>Count vehicles in an authorized recording.</p></div><Video size={19} className="muted-icon" /></div>{eligible.length === 0 && <div className="inline-error">No camera is ready yet. Save a camera with an area and road, set it to Approved and Online, then refresh.</div>}
        <form className="incident-form" onSubmit={analyze}><label>Approved camera source<select value={cameraId} onChange={e => setCameraId(e.target.value)}><option value="">Choose an approved camera</option>{eligible.map(x => <option value={x.camera_id} key={x.camera_id}>{x.display_name} · {x.road}</option>)}</select></label><label>Video clip<input type="file" accept="video/mp4,video/avi,video/quicktime,video/x-matroska,.mp4,.avi,.mov,.mkv" onChange={e => setFile(e.target.files?.[0] || null)} /><small>MP4, AVI, MOV, or MKV. Maximum size: 100 MB.</small></label>
          <div className="form-pair"><label>Observation date<input type="date" required value={dateValue} onChange={e => setDateValue(e.target.value)} /></label><label>Time of day<select value={block} onChange={e => setBlock(e.target.value)}>{BLOCKS.map(x => <option key={x}>{x}</option>)}</select></label></div>
          <button className="button button-primary" disabled={busy || !isAuthority || !eligible.length}><Video size={15} />{busy ? 'Reviewing clip…' : 'Review and save clip'}</button></form>
        {analysis && <div className={analysis.error ? 'inline-error' : 'result-card'}>{analysis.error || <><span className="result-icon"><Camera size={18} /></span><div className="result-main"><small>VIDEO REVIEW · ESTIMATE</small><b>{analysis.analysis.clip_vehicle_count_estimate} vehicles seen</b><p>{analysis.analysis.sampled_frames} frames reviewed{analysis.analysis.clip_fully_analyzed === false ? ` (only the first ${analysis.analysis.analyzed_seconds} s of ${analysis.analysis.clip_seconds ?? '?'} s were analysed)` : ''}. This is an unverified estimate.</p><small>Review reference #{analysis.observation_id}</small></div></>}</div>}</section></div>
    <section className="card report-list-card camera-table"><div className="card-heading"><div><h2>Registered cameras</h2><p>Only approved, online cameras are sampled or used for review.</p></div><span className="pill pill-neutral">{data.cameras.length} ENTRIES</span></div>
      {data.cameras.length ? <div className="table-wrap"><table><thead><tr><th>Name</th><th>Area / road</th><th>Authorization</th><th>Operation</th><th>Stream</th><th>Last measurement</th><th /></tr></thead><tbody>{data.cameras.map(cam => <tr key={cam.camera_id}>
        <td className="road-name">{cam.display_name}<small className="table-sub">{cam.camera_id}</small></td><td>{[cam.area, cam.road].filter(Boolean).join(' · ') || 'Not assigned'}</td>
        <td><span className={`status-chip ${cam.authorization_status === 'approved' ? 'status-green' : cam.authorization_status === 'revoked' ? 'status-orange' : 'status-gray'}`}><i />{cam.authorization_status}</span></td>
        <td>{cam.operational_status}{cam.consecutive_failures ? <small className="table-sub">{cam.consecutive_failures} failed attempts{cam.last_error ? `: ${cam.last_error}` : ''}</small> : null}</td>
        <td>{cam.has_stream ? `every ${Math.round((cam.collection_interval_seconds || 300) / 60)} min${cam.counting_line ? ' · counting line' : ''}` : '—'}</td><td>{dateTime(cam.last_success_at)}</td>
        <td>{cam.has_stream && cam.authorization_status === 'approved' && cam.operational_status === 'online' && <button className="text-action" disabled={collecting === cam.camera_id} onClick={() => collect(cam)}>{collecting === cam.camera_id ? 'Sampling…' : 'Collect now'}</button>}</td></tr>)}</tbody></table></div>
        : <div className="empty-state"><span><Camera size={23} /></span><b>No camera sources registered</b><small>Authority approval is required before camera data can be accepted.</small></div>}</section>
    <section className="card report-list-card camera-table"><div className="card-heading"><div><h2>Saved CCTV footage</h2><p>Uploaded clips are deleted after the retention period; their analysis is kept.</p></div><span className="pill pill-neutral">{data.footage?.length || 0} CLIPS</span></div>{data.footage?.length ? <div className="footage-grid">{data.footage.map(clip => <FootageClip key={clip.footage_id} clip={clip} />)}</div> : <div className="empty-state"><span><Video size={23} /></span><b>No footage uploaded yet</b><small>Analyze an authorized clip above to save it for review.</small></div>}</section>
  </>;
}

function FootageClip({ clip }) {
  const [src, setSrc] = useState(''), [loading, setLoading] = useState(false), [error, setError] = useState('');
  useEffect(() => () => { if (src) URL.revokeObjectURL(src); }, [src]);
  async function play() {
    if (src) return;
    setLoading(true); setError('');
    try {
      const token = sessionStorage.getItem('flowstateToken');
      const response = await fetch(`${API}/api/camera-footage/${encodeURIComponent(clip.footage_id)}/video`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
      if (!response.ok) throw new Error(response.status === 410 ? 'The video was deleted under the retention policy.' : 'Playback is available to an authorized authority account.');
      setSrc(URL.createObjectURL(await response.blob()));
    } catch (e) { setError(e.message); } finally { setLoading(false); }
  }
  let analysis = {};
  try { analysis = typeof clip.analysis_json === 'string' ? JSON.parse(clip.analysis_json) : (clip.analysis_json || {}); } catch { /* keep empty */ }
  return <article className="footage-card"><div className="footage-preview">{src ? <video controls preload="metadata" src={src} /> : <button onClick={play} disabled={loading || Boolean(clip.file_deleted_at)}><Video size={24} /><span>{clip.file_deleted_at ? 'Video deleted (retention)' : loading ? 'Loading clip…' : 'Load and play clip'}</span></button>}</div><div className="footage-details"><b>{clip.original_filename}</b><small>{clip.display_name} · {dateTime(clip.uploaded_at)}</small><span>{analysis.clip_vehicle_count_estimate ?? '—'} vehicles detected · unverified estimate</span>{error && <small className="inline-error">{error}</small>}</div></article>;
}

// ---------- citizen incident report ----------

function Incidents({ setToast }) {
  const [form, setForm] = useState({ category: 'heavy_traffic', description: '', area: '', road: '', latitude: null, longitude: null });
  const [busy, setBusy] = useState(false), [locating, setLocating] = useState(false);
  function captureLocation() {
    if (!navigator.geolocation) { setToast('Location is unavailable in this browser.'); return; }
    setLocating(true);
    navigator.geolocation.getCurrentPosition(p => { setForm(prev => ({ ...prev, latitude: p.coords.latitude, longitude: p.coords.longitude })); setLocating(false); },
      () => { setToast('Location permission was not granted. You can still enter the area and road manually.'); setLocating(false); }, { enableHighAccuracy: false, timeout: 10000 });
  }
  async function submit(e) {
    e.preventDefault(); setBusy(true);
    try {
      await api('/api/auth/me');
      const payload = { ...form, area: form.area.trim() || null, road: form.road.trim() || null };
      if (payload.latitude == null) { delete payload.latitude; delete payload.longitude; }
      const result = await api('/api/incidents', { method: 'POST', body: JSON.stringify(payload) });
      setForm({ ...form, description: '', latitude: null, longitude: null });
      setToast(result.status === 'duplicate' ? result.message : 'Report sent. It will remain unverified until an authority reviews it.');
    } catch (err) {
      setToast(/401|expired|invalid account token|sign.?in/i.test(err.message) ? 'Your sign-in has expired. Please sign in again, then send your report.' : err.message);
    } finally { setBusy(false); }
  }
  return <><PageHeading eyebrow="COMMUNITY ROAD REPORT" title="Report an incident" subtitle="Send a clear, factual report to the authority operations team." action={<span className="pill pill-neutral"><Users size={14} /> Community input</span>} />
    <div className="incident-grid"><section className="card form-card"><div className="card-heading"><div><h2>What is happening?</h2><p>Location helps the authority find and assess the report.</p></div><Plus size={18} className="muted-icon" /></div>
      <form className="incident-form" onSubmit={submit}><label>Incident type<select value={form.category} onChange={e => setForm({ ...form, category: e.target.value })}>{CITIZEN_CATEGORIES.map(v => <option key={v} value={v}>{CATEGORY_LABELS[v]}</option>)}</select></label>
        <div className="form-pair"><label>Area<input value={form.area} onChange={e => setForm({ ...form, area: e.target.value })} placeholder="e.g. Indiranagar" /></label><label>Road or junction<input value={form.road} onChange={e => setForm({ ...form, road: e.target.value })} placeholder="e.g. 100 Feet Road" /></label></div>
        <button type="button" className="button button-secondary location-capture" onClick={captureLocation}><LocateFixed size={15} />{locating ? 'Finding your location…' : form.latitude != null ? 'Location attached' : 'Attach my current location'}</button>{form.latitude != null && <small className="coordinate-note">{form.latitude.toFixed(5)}, {form.longitude.toFixed(5)} · shared with this report</small>}
        <label>Description<textarea required minLength="5" maxLength="1000" rows="4" value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} placeholder="What did you observe? Avoid names or personal details." /></label>
        <div className="form-submit"><button className="button button-primary" disabled={busy}><Plus size={16} />{busy ? 'Sending…' : 'Send report'}</button><span>An authority must verify before it can be broadcast.</span></div></form></section>
      <section className="card report-list-card report-guidance"><span className="map-side-icon"><ShieldCheck size={19} /></span><div className="eyebrow">AFTER YOU REPORT</div><h2>What happens next?</h2><div className="guidance-step"><i>1</i><div><b>Operations receives it</b><small>Matching reports nearby are merged as confirmations.</small></div></div><div className="guidance-step"><i>2</i><div><b>An authority checks the details</b><small>It stays unverified until reviewed, and expires if left unreviewed.</small></div></div><div className="guidance-step"><i>3</i><div><b>Verified reports affect routes</b><small>They are used in route scores and may become a public alert.</small></div></div><div className="user-data-note"><ShieldCheck size={15} /><span>Please do not include personal information or submit while driving.</span></div></section></div></>;
}

function ReportItem({ item }) {
  const Icon = item.category === 'construction' || item.category === 'metro_construction' ? Construction : AlertTriangle;
  return <article className="report-item"><div className="report-icon"><Icon size={17} /></div><div className="report-content"><div className="report-title"><b>{CATEGORY_LABELS[item.category] || item.category}</b><span className={`status-chip ${item.status === 'verified' ? 'status-green' : item.status === 'resolved' ? 'status-blue' : item.status === 'stale' ? 'status-gray' : 'status-orange'}`}><i />{item.status}</span>{item.official ? <span className="status-chip status-blue"><i />official</span> : null}{item.corroboration_count ? <span className="status-chip status-gray"><i />{item.corroboration_count} confirmation{item.corroboration_count === 1 ? '' : 's'}</span> : null}</div>
    <p>{item.description}</p><div className="report-meta"><span><MapPin size={13} />{[item.area, item.road].filter(Boolean).join(' · ') || (item.latitude != null ? `${Number(item.latitude).toFixed(4)}, ${Number(item.longitude).toFixed(4)}` : 'Location not supplied')}</span><span><Clock3 size={13} />{dateTime(item.created_at)}</span>{item.status === 'verified' && item.expires_at && <span>Valid until {dateTime(item.expires_at)}</span>}</div>{item.official_note && <p><b>Official note:</b> {item.official_note}</p>}</div></article>;
}

export default App;
