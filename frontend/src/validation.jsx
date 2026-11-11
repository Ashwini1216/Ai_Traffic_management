import { useEffect, useState } from 'react';
import { API, api, dateTime, label, n } from './lib.js';

const f3 = value => Number(value).toFixed(3);

// Dated public benchmarks are separate from verified operational observations.
export function PublicValidation({ kind }) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState('');
  const [downloading, setDownloading] = useState('');
  useEffect(() => { let mounted = true; api('/api/validation/summary').then(data => {
    if (mounted && data.available) setReport(data);
  }).catch(() => {}); return () => { mounted = false; }; }, []);
  async function download(sequence) {
    setError(''); setDownloading(sequence);
    try {
      const token = sessionStorage.getItem('flowstateToken');
      const response = await fetch(`${API}/api/validation/samples/${encodeURIComponent(sequence)}`, { headers: { Authorization: `Bearer ${token}` } });
      if (!response.ok) throw new Error('The sample could not be downloaded. Sign in again or check the saved validation clips.');
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a'); link.href = url; link.download = `UA-DETRAC-${sequence}-15s.avi`;
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    } catch (err) { setError(err.message); } finally { setDownloading(''); }
  }
  const result = report?.[kind];
  if (!result) return null;
  if (kind === 'forecast') return <section className="card report-list-card" aria-label="Public forecast validation">
    <div className="card-heading"><div><h2>Public daily-data validation</h2><p>{n(result.source.rows)} daily records · {result.source.roads} roads · test from {result.split.test_from}</p></div></div>
    <p className="form-hint">{result.notice} These dated results do not measure current live traffic.</p>
    <div className="table-wrap"><table><thead><tr><th>Model / baseline</th><th>Accuracy</th><th>Macro-F1</th><th>Acceptance</th></tr></thead><tbody>
      {result.candidates.map(item => <tr key={item.version}><td>{item.kind.endsWith('random_forest') ? 'Random Forest' : 'Hybrid LSTM + RF'}<small className="table-sub">{item.version}</small></td><td>{f3(item.test.accuracy)}</td><td>{f3(item.test.macro_f1)}</td><td>{item.acceptance_passed ? 'Passed' : 'Rejected'}</td></tr>)}
      {Object.entries(result.baselines).map(([name, item]) => <tr key={name}><td>{label(name)}</td><td>{f3(item.accuracy)}</td><td>{f3(item.macro_f1)}</td><td>Baseline</td></tr>)}
    </tbody></table></div>
    <p className="form-hint">{result.candidates.every(item => !item.acceptance_passed) ? 'Neither daily model passed these checks. Low and Medium test-class recall was zero for both daily candidates. Restored block-data models are evaluated separately above.' : 'Acceptance is based on the held-out comparison shown here; active versions are listed below.'}</p>
    <p className="form-hint"><a href={result.source.source} target="_blank" rel="noreferrer">Publisher dataset and licence</a> · source dates {result.source.date_range.join(' – ')}</p>
  </section>;
  if (kind === 'vehicles') {
    const baseline = result.results.baseline.aggregate, selected = result.results.selected.aggregate;
    const settings = result.results.selected.settings;
    return <section className="card report-list-card" aria-label="Public vehicle validation">
      <div className="card-heading"><div><h2>Annotated video validation</h2><p>{n(selected.frames)} test frames · {n(selected.reference_vehicle_instances)} reference vehicle instances · {dateTime(result.evaluated_at)}</p></div></div>
      <p className="form-hint">{result.scope} {result.classes}</p>
      <div className="table-wrap"><table><thead><tr><th>Held-out measure</th><th>Original detector</th><th>Selected detector</th></tr></thead><tbody>
        <tr><td>Mean count error per frame ↓</td><td>{f3(baseline.count_mae)}</td><td>{f3(selected.count_mae)}</td></tr>
        <tr><td>Detection F1, IoU50 ↑</td><td>{f3(baseline.f1_iou50)}</td><td>{f3(selected.f1_iou50)}</td></tr>
        <tr><td>Line-crossing absolute error ↓<small className="table-sub">{selected.reference_crossings} reference crossings</small></td><td>{baseline.crossing_absolute_error}</td><td>{selected.crossing_absolute_error}</td></tr>
        <tr><td>Distinct-vehicle absolute error ↓<small className="table-sub">{selected.reference_distinct_vehicles} reference vehicles</small></td><td>{baseline.distinct_count_absolute_error}</td><td>{selected.distinct_count_absolute_error}</td></tr>
      </tbody></table></div>
      <p className="form-hint">Selected on separate validation scenes: {settings.weights}, image size {settings.imgsz}, confidence {settings.conf}. Frame counts and line crossings improved. Distinct-vehicle totals failed the comparison: tracker IDs can split and count the same vehicle again. Detection F1 is not vehicle-count accuracy.</p>
      <p className="form-hint"><a href={result.source} target="_blank" rel="noreferrer">UA-DETRAC source and annotations</a>. Six 15-second reference clips are saved locally; their source hashes and split assignments are retained.</p>
      <div className="trip-actions">{report.samples.map(item => <button key={item.sequence} className="button button-secondary" disabled={Boolean(downloading)} onClick={() => download(item.sequence)}>{downloading === item.sequence ? 'Downloading…' : `${item.weather} · ${item.sequence} · ${item.split === 'test' ? 'test' : 'validation'}`}</button>)}</div>
      {error && <p className="inline-error">{error}</p>}
    </section>;
  }
  const times = result.cases.map(item => item.seconds), repairs = result.cases.map(item => item.repair_seconds);
  return <section className="card report-list-card" aria-label="Public routing validation">
    <div className="card-heading"><div><h2>Bengaluru routing validation</h2><p>{result.cases.filter(item => item.passed).length}/{result.cases.length} city-sector routes passed · {dateTime(result.checked_at)}</p></div></div>
    <p className="form-hint">The installed graph contains {n(result.graph.directed_edges)} directed road edges. All tested D* Lite costs matched A*. Closure repair avoided the blocked edge, and reopening restored the original cost.</p>
    <div className="table-wrap"><table><thead><tr><th>Route</th><th>Initial route</th><th>Closure repair</th><th>Checks</th></tr></thead><tbody>
      {result.cases.map(item => <tr key={item.name}><td>{item.name}</td><td>{item.seconds.toFixed(2)} s</td><td>{item.repair_seconds.toFixed(2)} s</td><td>{item.passed ? 'Passed' : 'Failed'}</td></tr>)}
    </tbody></table></div>
    <p className="form-hint">Measured on this laptop: {Math.min(...times).toFixed(1)}–{Math.max(...times).toFixed(1)} s for initial routes, {Math.min(...repairs).toFixed(1)}–{Math.max(...repairs).toFixed(1)} s for closure repair; {result.load_seconds.toFixed(1)} s map load and {result.peak_rss_gib.toFixed(2)} GiB peak benchmark memory. Roads still depend on map availability and access restrictions. Traffic observations cover fewer roads than the map.</p>
    <p className="form-hint"><a href="https://download.geofabrik.de/asia/india/southern-zone.html" target="_blank" rel="noreferrer">Geofabrik / OpenStreetMap extract</a> · map date {result.graph.coverage.osm_timestamp.slice(0, 10)} · © OpenStreetMap contributors, ODbL</p>
  </section>;
}
