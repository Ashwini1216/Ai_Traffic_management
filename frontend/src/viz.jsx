import { AlertOctagon, AlertTriangle, CheckCircle2, HelpCircle } from 'lucide-react';
import { pct } from './lib.js';

// Congestion levels are status (good -> warning -> critical): fixed status colours, always
// shown with an icon and a text label so meaning never rests on colour alone.
export const LEVELS = {
  Low: { color: '#0ca30c', track: 'rgba(12,163,12,0.14)', icon: CheckCircle2, text: 'Low congestion' },
  Medium: { color: '#fab219', track: 'rgba(250,178,25,0.18)', icon: AlertTriangle, text: 'Medium congestion' },
  High: { color: '#d03b3b', track: 'rgba(208,59,59,0.14)', icon: AlertOctagon, text: 'High congestion' },
};
const UNKNOWN = { color: '#898781', track: 'rgba(137,135,129,0.15)', icon: HelpCircle, text: 'Unknown' };
export const level = name => LEVELS[name] || UNKNOWN;
export const ACCENT = '#087e83';   // the app's single-series accent (brand teal)
export const MUTED = '#c3c2b7';    // de-emphasis for comparison series

export function LevelBadge({ value, compact = false }) {
  const meta = level(value);
  const Icon = meta.icon;
  return (
    <span className="level-badge" title={meta.text}>
      <Icon size={compact ? 13 : 15} color={meta.color} aria-hidden="true" />
      <span>{compact ? (value || 'Unknown') : meta.text}</span>
    </span>
  );
}

// One meter per class: the fill carries the class, the track is a lighter step of the same hue.
export function ProbabilityMeters({ probabilities = {} }) {
  const order = ['Low', 'Medium', 'High'].filter(c => c in probabilities).concat(Object.keys(probabilities).filter(c => !LEVELS[c]));
  return (
    <div className="meter-list" role="table" aria-label="Probability of each congestion level">
      {order.map(name => {
        const meta = level(name);
        const value = probabilities[name] || 0;
        return (
          <div className="meter-row" role="row" key={name} title={`${name}: ${pct(value)}`}>
            <span className="meter-label" role="cell"><LevelBadge value={name} compact /></span>
            <span className="meter-track" role="cell" style={{ background: meta.track }}>
              <i style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%`, background: meta.color }} />
            </span>
            <b className="meter-value" role="cell">{pct(value)}</b>
          </div>
        );
      })}
    </div>
  );
}

// Magnitudes across named components: one colour, value at the bar tip.
export function ComponentBars({ items }) {
  return (
    <div className="bar-list" role="table">
      {items.map(item => (
        <div className="bar-row" role="row" key={item.label} title={`${item.label}: ${item.value == null ? 'not assessed' : pct(item.value)}`}>
          <span className="bar-label" role="cell">{item.label}</span>
          <span className="bar-track" role="cell">
            {item.value != null && <i style={{ width: `${Math.max(0, Math.min(1, item.value)) * 100}%`, background: item.color || ACCENT }} />}
          </span>
          <b className="bar-value" role="cell">{item.value == null ? 'n/a' : pct(item.value)}</b>
        </div>
      ))}
    </div>
  );
}

// Emphasis: the highlighted item in the accent, the rest in grey, values labelled.
export function ComparisonBars({ items, highlight, format = v => (v == null ? '—' : v.toFixed(3)) }) {
  const max = Math.max(1e-9, ...items.map(i => i.value || 0));
  return (
    <div className="bar-list" role="table">
      {items.map(item => (
        <div className="bar-row" role="row" key={item.label} title={`${item.label}: ${format(item.value)}`}>
          <span className={`bar-label ${item.label === highlight ? 'bar-label-strong' : ''}`} role="cell">{item.label}</span>
          <span className="bar-track" role="cell">
            <i style={{ width: `${((item.value || 0) / max) * 100}%`, background: item.label === highlight ? ACCENT : MUTED }} />
          </span>
          <b className="bar-value" role="cell">{format(item.value)}</b>
        </div>
      ))}
    </div>
  );
}

export function StatTile({ label: title, value, sub, tone }) {
  return (
    <div className={`stat-tile ${tone ? `stat-${tone}` : ''}`}>
      <span className="stat-label">{title}</span>
      <b className="stat-value">{value}</b>
      {sub && <small className="stat-sub">{sub}</small>}
    </div>
  );
}

export function ConfidenceTag({ confidence }) {
  if (!confidence) return null;
  return <span className={`confidence-tag confidence-${confidence.level}`} title={`Confidence score ${confidence.score}`}>{confidence.level} confidence</span>;
}
