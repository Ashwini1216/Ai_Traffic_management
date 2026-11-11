import { useEffect, useState } from 'react';
import { api, dateTime, n } from './lib.js';

const f3 = value => Number(value).toFixed(3);

export function AccuracyFindings({ kind }) {
  const [report, setReport] = useState(null);
  useEffect(() => { let active = true; api('/api/validation/summary').then(value => { if (active && value.available) setReport(value); }).catch(() => {});
    return () => { active = false; }; }, []);
  const item = report?.[kind];
  if (!item) return null;
  if (kind === 'model_upgrade') return <section className="card report-list-card" aria-label="Restored project model results">
    <div className="card-heading"><div><h2>Restored-project model comparison</h2><p>{n(item.data.block_records)} original block records · {n(item.metrics.test.count)} test outcomes · test from {item.metrics.split.test_from}</p></div></div>
    <div className="table-wrap"><table><thead><tr><th>Method</th><th>Accuracy</th><th>Macro-F1</th></tr></thead><tbody>
      <tr><td>Validation-selected Random Forest</td><td>{f3(item.metrics.test.accuracy)}</td><td>{f3(item.metrics.test.macro_f1)}</td></tr>
      {Object.entries(item.metrics.baselines_test).filter(([name]) => name !== 'previous_random_forest').map(([name, score]) => <tr key={name}><td>{name.replaceAll('_', ' ')}</td><td>{f3(score.accuracy)}</td><td>{f3(score.macro_f1)}</td></tr>)}
    </tbody></table></div>
    <p className="form-hint">{item.acceptance.passed ? 'Passed the existing baseline, majority-accuracy and class-recall checks.' : 'Did not pass acceptance.'} Current activation status: {item.status}. Validation differences were within one date-clustered standard error, so the simpler classifier was selected.</p>
    <p className="form-hint">These are historical re-evaluation results on a source with derived daily time blocks. They do not establish current live traffic accuracy. Current forecasts still need recent compatible observations.</p>
  </section>;
  if (kind === 'public_camera') return <section className="card report-list-card" aria-label="Public Bengaluru camera source">
    <div className="card-heading"><div><h2>Public Bengaluru camera source</h2><p>Banaswadi road view · checked {dateTime(item.checked_at)}</p></div></div>
    <p className="form-hint">A changing {item.width} × {item.height} road view was received at about {item.observed_frame_rate} frames per second. {item.affiliation}</p>
    <p className="form-hint">{item.notice} Measurements are stored as unverified. Use the live view and Collect now controls to inspect the source.</p>
    <p className="form-hint"><a href={item.publication} target="_blank" rel="noreferrer">Published webcam source</a> · location is approximate.</p>
  </section>;
  const previous = item.results.previous.aggregate, selected = item.results.selected.aggregate;
  return <section className="card report-list-card" aria-label="Confirmed clip counting results">
    <div className="card-heading"><div><h2>Confirmed clip-track comparison</h2><p>Four new test scenes · {selected.reference_distinct_vehicles} reference vehicles · {n(selected.frames)} common evaluation frames</p></div></div>
    <div className="table-wrap"><table><thead><tr><th>Measure</th><th>Previous tracking</th><th>Confirmed native tracking</th></tr></thead><tbody>
      <tr><td>Distinct-count absolute error ↓</td><td>{previous.distinct_count_absolute_error}</td><td>{selected.distinct_count_absolute_error}</td></tr>
      <tr><td>Identity F1 ↑</td><td>{f3(previous.identity_f1)}</td><td>{f3(selected.identity_f1)}</td></tr>
      <tr><td>Line-crossing absolute error ↓</td><td>{previous.crossing_absolute_error}</td><td>{selected.crossing_absolute_error}</td></tr>
    </tbody></table></div>
    <p className="form-hint">Distinct-count error fell by {Math.round((1 - selected.distinct_count_absolute_error / previous.distinct_count_absolute_error) * 100)}%. Frame detection/count results were preserved. {item.clip_only_scope}</p>
    <p className="form-hint">{item.scope} Confirmed track IDs remain estimates; fragmentation and missed vehicles still occur. The earlier detector comparison is shown below.</p>
  </section>;
}
