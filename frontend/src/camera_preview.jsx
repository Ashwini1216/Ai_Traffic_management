import { useEffect, useRef, useState } from 'react';
import { Camera } from 'lucide-react';
import { API } from './lib.js';

export function CameraPreview({ cameras, isAuthority }) {
  const eligible = cameras.filter(camera => camera.has_stream && camera.authorization_status === 'approved' && camera.operational_status === 'online');
  const [cameraId, setCameraId] = useState('');
  const [running, setRunning] = useState(false);
  const [image, setImage] = useState('');
  const [fetchedAt, setFetchedAt] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const currentImage = useRef('');
  const availableIds = eligible.map(camera => camera.camera_id).join('|');
  useEffect(() => {
    if (!eligible.some(camera => camera.camera_id === cameraId)) {
      setRunning(false); setCameraId(eligible[0]?.camera_id || '');
    }
  }, [availableIds]);
  useEffect(() => {
    if (!running || !cameraId) return undefined;
    const controller = new AbortController();
    let timer;
    async function refresh() {
      setBusy(true); setError('');
      try {
        const token = sessionStorage.getItem('flowstateToken');
        const response = await fetch(`${API}/api/cameras/${encodeURIComponent(cameraId)}/snapshot`, {
          headers: { Authorization: `Bearer ${token}` }, signal: controller.signal,
        });
        if (!response.ok) throw new Error(response.status === 403 ? 'This camera is no longer approved and online.' : 'A usable image is unavailable. The view will retry shortly.');
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        if (currentImage.current) URL.revokeObjectURL(currentImage.current);
        currentImage.current = URL.createObjectURL(blob); setImage(currentImage.current);
        setFetchedAt(response.headers.get('X-Frame-Fetched-At') || new Date().toISOString());
      } catch (err) { if (!controller.signal.aborted) setError(err.message); }
      finally {
        if (!controller.signal.aborted) { setBusy(false); timer = setTimeout(refresh, 10000); }
      }
    }
    refresh();
    return () => { controller.abort(); clearTimeout(timer); setBusy(false); };
  }, [running, cameraId]);
  useEffect(() => () => { if (currentImage.current) URL.revokeObjectURL(currentImage.current); }, []);
  return <section className="card form-card" aria-label="Live camera preview">
    <div className="card-heading"><div><h2>Live camera view</h2><p>Recent images from an approved stream, refreshed every 10 seconds while this view is open.</p></div><Camera size={19} className="muted-icon" /></div>
    <div className="trip-actions camera-preview-controls"><label>Camera to view<select value={cameraId} disabled={running} onChange={event => { setCameraId(event.target.value); setImage(''); setFetchedAt(''); }}>
      {eligible.length === 0 && <option value="">No approved stream available</option>}
      {eligible.map(camera => <option key={camera.camera_id} value={camera.camera_id}>{camera.display_name}</option>)}
    </select></label><button className="button button-secondary" disabled={!isAuthority || !cameraId} onClick={() => setRunning(!running)}>{running ? 'Stop live view' : 'Start live view'}</button></div>
    {busy && <p className="form-hint" role="status">Fetching a usable frame; some cameras need a few seconds to warm up…</p>}
    {error && <p className="inline-error">{error}</p>}
    {image && <figure className="video-preview"><img src={image} alt={`Latest fetched frame from ${eligible.find(camera => camera.camera_id === cameraId)?.display_name || 'camera'}`} />
      <figcaption>Fetched at {new Date(fetchedAt).toLocaleTimeString('en-IN')}. {running ? 'Refreshing every 10 seconds.' : 'View stopped; last fetched image shown.'}</figcaption></figure>}
    <p className="form-hint">Use Collect now below to record an interval measurement. Low-refresh sources support visible-frame estimates; they do not establish reliable distinct-vehicle totals or hourly flow.</p>
  </section>;
}
