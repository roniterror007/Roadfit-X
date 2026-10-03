import React, { useEffect, useState } from 'react';
import axios from 'axios';

export default function TrafficConsole({ api, onRoads }) {
  const [username, setUsername] = useState('officer');
  const [password, setPassword] = useState('');
  const [token, setToken] = useState(null);
  const [dashboard, setDashboard] = useState(null);
  const [selected, setSelected] = useState('');
  const [flow, setFlow] = useState(1800);
  const [start, setStart] = useState(5);
  const [duration, setDuration] = useState(10);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const headers = token ? { Authorization: `Bearer ${token}` } : {};

  const refresh = async () => {
    const res = await axios.get(`${api}/officer/dashboard`, { headers });
    setDashboard(res.data);
    setSelected(prev => res.data.roads.some(r => r.edge_id === prev) ? prev : (res.data.roads[0]?.edge_id || ''));
    onRoads({ type: 'FeatureCollection', features: res.data.roads.map(r => ({
      type: 'Feature', properties: { load: r.load_ratio }, geometry: r.geometry,
    })) });
  };

  useEffect(() => {
    if (!token) return;
    let active = true;
    const load = async () => {
      try {
        const res = await axios.get(`${api}/officer/dashboard`, { headers: { Authorization: `Bearer ${token}` } });
        if (!active) return;
        setDashboard(res.data);
        setSelected(prev => res.data.roads.some(r => r.edge_id === prev) ? prev : (res.data.roads[0]?.edge_id || ''));
        onRoads({ type: 'FeatureCollection', features: res.data.roads.map(r => ({
          type: 'Feature', properties: { load: r.load_ratio }, geometry: r.geometry,
        })) });
      } catch (error) {
        if (!active) return;
        setMessage(error?.response?.data?.detail || 'Could not refresh forecasts.');
        if (error?.response?.status === 401) { setToken(null); onRoads(null); }
      }
    };
    load();
    const timer = setInterval(load, 10000);
    return () => { active = false; clearInterval(timer); };
  }, [api, token, onRoads]);

  const login = async e => {
    e.preventDefault(); setBusy(true); setMessage('');
    try {
      const res = await axios.post(`${api}/officer/login`, { username, password });
      setToken(res.data.access_token); setPassword('');
    } catch (error) { setMessage(error?.response?.data?.detail || 'Login unavailable.'); }
    finally { setBusy(false); }
  };

  const act = async endpoint => {
    setBusy(true); setMessage('');
    try {
      const payload = endpoint === 'forecast' ? { edge_id: selected, flow_pcu_h: Number(flow),
        starts_in_min: Number(start), duration_min: Number(duration) } : endpoint === 'rebalance' ? { max_trips: 10 } : { edge_id: selected, minutes: 15 };
      const res = await axios.post(`${api}/officer/${endpoint}`, payload, { headers, timeout: 180000 });
      setMessage(res.data.message); await refresh();
    } catch (error) { setMessage(error?.response?.data?.detail || 'Could not apply scenario.'); }
    finally { setBusy(false); }
  };

  const road = dashboard?.roads.find(r => r.edge_id === selected);
  return <section className="traffic-console">
    <h2>Traffic officer</h2>
    <p className="muted">Balance future arrivals with manual load scenarios and voluntary route reservations.</p>
    {!token ? <form onSubmit={login}>
      <div className="form-group"><label htmlFor="officer-user">Officer username</label>
        <input id="officer-user" className="form-control" autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} /></div>
      <div className="form-group"><label htmlFor="officer-password">Password</label>
        <input id="officer-password" className="form-control" type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} /></div>
      <button className="primary-btn" disabled={busy || !password}>Sign in as officer</button>
    </form> : <>
      <div className="officer-summary"><strong>{dashboard?.active_reservations ?? 0}</strong> active route intentions
        <span> · {dashboard?.bin_seconds ?? 120}s forecast bins</span></div>
      <p className="scenario-label">Local research scenario · no live traffic feed</p>
      <div className="form-group"><label htmlFor="officer-road">Directed road segment</label>
        <select id="officer-road" className="form-control" value={selected} onChange={e => setSelected(e.target.value)}>
          {dashboard?.roads.map(r => <option key={`${r.edge_id}-${r.starts_utc}`} value={r.edge_id}>{r.name} · {r.load_ratio.toFixed(2)}× load</option>)}
        </select></div>
      {road && <div className="road-evidence">
        <p>{road.highway} · assumed capacity {road.capacity_pcu_h.toFixed(0)} PCU/hour</p>
        <p>{road.booked_pcu.toFixed(2)} PCU reserved · forecast {road.forecast_pcu_h.toFixed(0)} PCU/hour</p>
        <p>Mapped building footprints within 50m: {road.building_footprints_50m ?? 'unavailable'}</p>
        <p>Bin starts {new Date(road.starts_utc).toLocaleTimeString()}</p>
      </div>}
      <div className="form-group"><label htmlFor="officer-flow">Forecast incoming flow (PCU/hour)</label>
        <input id="officer-flow" className="form-control" type="number" min="0" max="20000" value={flow} onChange={e => setFlow(e.target.value)} /></div>
      <div className="forecast-times">
        <div className="form-group"><label htmlFor="officer-start">Starts in minutes</label><input id="officer-start" className="form-control" type="number" min="0" max="120" value={start} onChange={e => setStart(e.target.value)} /></div>
        <div className="form-group"><label htmlFor="officer-duration">Lasts minutes</label><input id="officer-duration" className="form-control" type="number" min="2" max="60" value={duration} onChange={e => setDuration(e.target.value)} /></div>
      </div>
      <button className="primary-btn" disabled={busy || !selected} onClick={() => act('forecast')}>Apply future load scenario</button>
      <button className="secondary-btn" disabled={busy || !selected} onClick={() => act('closure')}>Close selected segment for 15 min</button>
      <button className="secondary-btn" disabled={busy || !dashboard?.active_reservations} onClick={() => act('rebalance')}>Rebalance future departures</button>
      <button className="secondary-btn" onClick={async () => {
        try { await axios.post(`${api}/officer/logout`, {}, { headers }); }
        finally { setToken(null); setDashboard(null); onRoads(null); }
      }}>Sign out</button>
    </>}
    {message && <p role="status" className="console-message">{message}</p>}
  </section>;
}
