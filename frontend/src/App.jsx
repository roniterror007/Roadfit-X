import React, { useState, useEffect, useRef } from 'react';
import Map, { NavigationControl, Source, Layer, Marker } from 'react-map-gl/maplibre';
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import 'maplibre-gl/dist/maplibre-gl.css';
import axios from 'axios';
import { Search, MapPin, Navigation, Loader2, AlertTriangle, CheckCircle, Zap, Brain } from 'lucide-react';
import TrafficConsole from './TrafficConsole';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
const formatMetric = (value, suffix = '') => value == null ? 'Unknown' : `${Number(value).toFixed(2)}${suffix}`;

const INITIAL_VIEW_STATE = {
  longitude: 77.6245,
  latitude: 12.9352,
  zoom: 11,
  pitch: 0,
};

const VEHICLES = [
  { id: 'bicycle',   label: '🚲 Bicycle',       width: 0.65, height: 1.7, weight: 0.1,  icon: '🚲' },
  { id: 'motorcycle',label: '🏍️ Motorcycle',    width: 0.9,  height: 1.4, weight: 0.2,  icon: '🏍️' },
  { id: 'hatchback', label: '🚗 Hatchback',      width: 1.8,  height: 1.6, weight: 1.2,  icon: '🚗' },
  { id: 'suv',       label: '🚙 SUV',            width: 2.1,  height: 2.0, weight: 2.5,  icon: '🚙' },
  { id: 'van',       label: '🚛 Delivery Van',   width: 2.4,  height: 2.8, weight: 5.0,  icon: '🚛' },
  { id: 'truck',     label: '🚚 Truck',          width: 2.5,  height: 3.2, weight: 12.0, icon: '🚚' },
];

const RAIN_LEVELS = [
  { id: 'none',     label: '☀️ Clear',    color: '#10b981' },
  { id: 'light',    label: '🌦️ Light',   color: '#60a5fa' },
  { id: 'moderate', label: '🌧️ Moderate', color: '#3b82f6' },
  { id: 'heavy',    label: '⛈️ Heavy',   color: '#8b5cf6' },
];

const TRAFFIC_LEVELS = [
  { id: 'normal',  label: '🟢 Light',   color: '#10b981' },
  { id: 'heavy',   label: '🟡 Heavy',   color: '#f59e0b' },
  { id: 'gridlock',label: '🔴 Gridlock',color: '#ef4444' },
];

const mapStyle = {
  version: 8,
  sources: {
    osm: {
      type: 'raster',
      tiles: ['https://a.tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: '&copy; OpenStreetMap Contributors',
    },
  },
  layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
};

const satelliteStyle = {
  version: 8,
  sources: { imagery: { type: 'raster', tileSize: 256,
    tiles: ['https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
    attribution: 'Imagery © Esri, Maxar, Earthstar Geographics and the GIS User Community',
  } },
  layers: [{ id: 'imagery', type: 'raster', source: 'imagery' }],
};

function useDebounce(value, delay) {
  const [debouncedValue, setDebouncedValue] = useState(value);
  useEffect(() => {
    const h = setTimeout(() => setDebouncedValue(value), delay);
    return () => clearTimeout(h);
  }, [value, delay]);
  return debouncedValue;
}

// Route probability to color
function probColor(p) {
  if (p >= 0.90) return '#10b981';
  if (p >= 0.75) return '#f59e0b';
  if (p >= 0.50) return '#ef4444';
  return '#7f1d1d';
}

// Animated dot along route
function AnimatedDot({ coords, isAnimating }) {
  const [pos, setPos] = useState(null);
  const tRef = useRef(0);

  useEffect(() => {
    if (!isAnimating || !coords || coords.length < 2) {
      return;
    }
    let frame;
    const speed = 0.0008;
    const animate = () => {
      tRef.current = (tRef.current + speed) % 1;
      const t = tRef.current;
      const segIdx = Math.floor(t * (coords.length - 1));
      const segT = (t * (coords.length - 1)) - segIdx;
      const a = coords[Math.min(segIdx, coords.length - 1)];
      const b = coords[Math.min(segIdx + 1, coords.length - 1)];
      const lon = a[0] + (b[0] - a[0]) * segT;
      const lat = a[1] + (b[1] - a[1]) * segT;
      setPos({ lon, lat });
      frame = requestAnimationFrame(animate);
    };
    frame = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(frame);
  }, [isAnimating, coords]);

  if (!pos || !isAnimating || !coords || coords.length < 2) return null;
  return (
    <Marker longitude={pos.lon} latitude={pos.lat}>
      <div style={{
        width: 18, height: 18, borderRadius: '50%',
        background: '#6366f1',
        border: '3px solid white',
        boxShadow: '0 0 12px rgba(99,102,241,0.9)',
        animation: 'pulse 1s ease-in-out infinite'
      }} />
    </Marker>
  );
}

function StatRow({ label, value, sub, color }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
      <span style={{ color: '#94a3b8', fontSize: 13 }}>{label}</span>
      <span style={{ color: color || 'white', fontWeight: 700, fontSize: 15 }}>
        {value}
        {sub && <span style={{ color: '#64748b', fontSize: 11, marginLeft: 4 }}>{sub}</span>}
      </span>
    </div>
  );
}

export default function App() {
  const [panelOpen, setPanelOpen] = useState(true);
  const [vehicle, setVehicle] = useState(VEHICLES[2]);
  const [viewMode, setViewMode] = useState('driver');
  const [objective, setObjective] = useState('eta');
  const [departure, setDeparture] = useState(0);
  const [reserve, setReserve] = useState(false);
  const [reservation, setReservation] = useState(null);
  const [hotspots, setHotspots] = useState(null);
  const [satellite, setSatellite] = useState(false);
  const [rainLevel, setRainLevel] = useState('none');
  const [trafficLevel, setTrafficLevel] = useState('normal');
  const [simulateCongestion, setSimulateCongestion] = useState(false);
  const [loading, setLoading] = useState(false);
  const [animating, setAnimating] = useState(false);
  const [routeStats, setRouteStats] = useState(null);
  const [routeDetails, setRouteDetails] = useState(null);
  const [routeGeojson, setRouteGeojson] = useState(null);
  const [origin, setOrigin] = useState(null);
  const [destination, setDestination] = useState(null);
  const [origQuery, setOrigQuery] = useState('');
  const [destQuery, setDestQuery] = useState('');
  const [origResults, setOrigResults] = useState([]);
  const [destResults, setDestResults] = useState([]);
  const [showOrigDrop, setShowOrigDrop] = useState(false);
  const [showDestDrop, setShowDestDrop] = useState(false);

  const [dataPolicy, setDataPolicy] = useState('conservative');
  const [baselineGeojson, setBaselineGeojson] = useState(null);
  const [errorMsg, setErrorMsg] = useState(null);
  
  const [trainingBrain, setTrainingBrain] = useState(false);
  const [brainMsg, setBrainMsg] = useState('');

  const debouncedOrig = useDebounce(origQuery, 450);
  const debouncedDest = useDebounce(destQuery, 450);

  useEffect(() => {
    if (!reservation) return;
    let active = true;
    const timer = setInterval(async () => {
      try {
        const res = await axios.post(`${API_BASE_URL}/traffic/reservations/status`, { token: reservation });
        if (!active) return;
        if (res.data.status === 'needs_route') {
          setErrorMsg(res.data.message); setRouteGeojson(null); setRouteStats(null);
        } else if (res.data.reviewed) {
          setRouteGeojson(res.data.geometry);
          setRouteStats(previous => previous && { ...previous, eta: res.data.eta_min,
            distance: res.data.distance_km, localDistance: res.data.local_distance_km,
            avg_speed: res.data.avg_speed_kmh, metrics: res.data.academic_metrics });
        }
      } catch (e) {
        if (active && e?.response?.status === 404) setReservation(null);
      }
    }, 10000);
    return () => { active = false; clearInterval(timer); };
  }, [reservation]);

  useEffect(() => {
    if (debouncedOrig.length > 2 && !debouncedOrig.startsWith('📍')) {
      axios.get(`${API_BASE_URL}/traffic/places?q=${encodeURIComponent(debouncedOrig)}`)
        .then(r => { setOrigResults(r.data); setShowOrigDrop(true); }).catch(() => {});
    }
  }, [debouncedOrig]);

  useEffect(() => {
    if (debouncedDest.length > 2 && !debouncedDest.startsWith('📍')) {
      axios.get(`${API_BASE_URL}/traffic/places?q=${encodeURIComponent(debouncedDest)}`)
        .then(r => { setDestResults(r.data); setShowDestDrop(true); }).catch(() => {});
    }
  }, [debouncedDest]);

  const selectOrigin = loc => {
    setOrigin({ lat: parseFloat(loc.lat), lon: parseFloat(loc.lon) });
    setOrigQuery(loc.display_name.split(',')[0]);
    setShowOrigDrop(false);
  };
  const selectDest = loc => {
    setDestination({ lat: parseFloat(loc.lat), lon: parseFloat(loc.lon) });
    setDestQuery(loc.display_name.split(',')[0]);
    setShowDestDrop(false);
  };

  const handleMapClick = async e => {
    const c = { lon: e.lngLat.lng, lat: e.lngLat.lat };
    if (!origin) { setOrigin(c); setOrigQuery('📍 Map pin (origin)'); }
    else if (!destination) { setDestination(c); setDestQuery('📍 Map pin (destination)'); }
    else { setDestination(c); setDestQuery('📍 Map pin (destination)'); }
  };

  const runBrainJob = async (endpoint, payload) => {
    setTrainingBrain(true);
    setBrainMsg('Starting simulated training…');
    try {
      const res = await axios.post(`${API_BASE_URL}${endpoint}`, payload);
      const jobId = res.data.job_id;
      let status = 'queued';
      while (status === 'queued' || status === 'running') {
        await new Promise(resolve => setTimeout(resolve, 2000));
        const progress = await axios.get(`${API_BASE_URL}/brain/jobs/${jobId}`);
        status = progress.data.status;
        setBrainMsg(progress.data.message);
      }
    } catch (err) {
      setBrainMsg(err?.response?.data?.detail || 'Could not follow the training job.');
    } finally {
      setTrainingBrain(false);
    }
  };

  const handleTrainBrain = () => runBrainJob('/brain/train?iterations=1000', {
    vehicle_class: vehicle.id,
    vehicle_width: vehicle.width, vehicle_height: vehicle.height, vehicle_weight: vehicle.weight,
  });
  const handleConsolidateBrain = () => runBrainJob('/brain/consolidate');

  const calculateRoute = async () => {
    if (!origin || !destination) return;
    setLoading(true); setErrorMsg(null); setAnimating(false);
    setRouteGeojson(null); setBaselineGeojson(null); setRouteStats(null); setRouteDetails(null);
    try {
      if (reservation) {
        try { await axios.post(`${API_BASE_URL}/traffic/reservations/cancel`, { token: reservation }); }
        catch (e) { if (e?.response?.status !== 404) throw e; }
        setReservation(null);
      }
      const res = await axios.post(`${API_BASE_URL}${objective === 'eta' ? '/traffic/route' : '/route/plan'}`, {
        orig_lat: origin.lat, orig_lon: origin.lon,
        dest_lat: destination.lat, dest_lon: destination.lon,
        vehicle_width: vehicle.width,
        vehicle_height: vehicle.height,
        vehicle_weight: vehicle.weight,
        unknown_data_policy: dataPolicy,
        simulate_congestion: simulateCongestion,
        rain_level: rainLevel,
        traffic_level: trafficLevel,
        vehicle_class: vehicle.id,
        departure_in_min: Number(departure), reserve,
      }, { timeout: 120000 });

      const sr = res.data.selected_route;
      const br = res.data.baseline_route;
      setReservation(sr.reservation_token || null);
      
      setRouteGeojson(sr.geometry);
      if (br && br.geometry) {
        setBaselineGeojson(br.geometry);
      }
      
      setRouteStats({
        distance: sr.distance_km,
        eta: objective === 'eta' ? sr.eta_nominal_min : sr.eta_p50_min,
        etaKind: objective,
        localDistance: sr.local_distance_km,
        eta_p90: sr.eta_p90_min,
        prob: sr.completion_probability,
        cvar: sr.cvar_risk,
        avg_speed: sr.avg_speed_kmh,
        rain: sr.rain_level,
        traffic: sr.traffic_level,
        ensembleWinner: sr.ensemble_winner,
        metrics: sr.academic_metrics || {}
      });
      setRouteDetails(res.data);
      setTimeout(() => setAnimating(true), 400);
    } catch (err) {
      // SAFETY INVARIANT: Never generate synthetic or unconstrained routes.
      // If the backend fails, show an explicit error — no fake polylines.
      const detail = err?.response?.data?.detail;
      setErrorMsg(typeof detail === 'string' ? detail : 'Route unavailable. Check the inputs and routing backend.');
      setRouteGeojson(null);
      setBaselineGeojson(null);
      setRouteStats(null);
      setRouteDetails(null);
    } finally { setLoading(false); }
  };

  return (
    <div className="app-container">
      <style>{`
        @keyframes pulse { 0%,100%{transform:scale(1);opacity:1} 50%{transform:scale(1.4);opacity:0.7} }
        @keyframes fadeSlide { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:translateY(0)} }
        .fade-in { animation: fadeSlide 0.3s ease forwards; }
      `}</style>
      <Map
        workerUrl={maplibreWorkerUrl}
        initialViewState={INITIAL_VIEW_STATE}
        mapStyle={satellite ? satelliteStyle : mapStyle}
        style={{ width: '100%', height: '100%', position: 'absolute' }}
        onClick={handleMapClick}
      >
        <NavigationControl position="bottom-right" />
        {hotspots && <Source id="future-load" type="geojson" data={hotspots}>
          <Layer id="future-load-line" type="line" paint={{ 'line-width': 7,
            'line-color': ['case', ['>', ['get', 'load'], 1], '#ef4444', '#f59e0b'], 'line-opacity': 0.8 }} />
        </Source>}

        {origin && (
          <Marker longitude={origin.lon} latitude={origin.lat} color="#10b981" draggable
            onDragEnd={e => { setOrigin({ lon: e.lngLat.lng, lat: e.lngLat.lat }); setOrigQuery('📍 Map pin'); }} />
        )}
        {destination && (
          <Marker longitude={destination.lon} latitude={destination.lat} color="#ef4444" draggable
            onDragEnd={e => { setDestination({ lon: e.lngLat.lng, lat: e.lngLat.lat }); setDestQuery('📍 Map pin'); }} />
        )}

        {/* Baseline outline (red dashed) */}
        {baselineGeojson && (
          <Source id="baseline" type="geojson" data={{ type: 'Feature', properties: {}, geometry: baselineGeojson }}>
            <Layer id="baseline-line" type="line"
              layout={{ 'line-join': 'round', 'line-cap': 'round' }}
              paint={{ 'line-color': '#ef4444', 'line-width': 4, 'line-dasharray': [2, 2], 'line-opacity': 0.8 }} />
          </Source>
        )}

        {/* Route outline (glow effect) */}
        {routeGeojson && (
          <Source id="route-glow" type="geojson" data={{ type: 'Feature', properties: {}, geometry: routeGeojson }}>
            <Layer id="route-glow-line" type="line"
              paint={{ 'line-color': '#10b981', 'line-width': 14, 'line-opacity': 0.25, 'line-blur': 8 }} />
          </Source>
        )}

        {/* Route line */}
        {routeGeojson && (
          <Source id="route" type="geojson" data={{ type: 'Feature', properties: {}, geometry: routeGeojson }}>
            <Layer id="route-line" type="line"
              layout={{ 'line-join': 'round', 'line-cap': 'round' }}
              paint={{ 'line-color': '#059669', 'line-width': 6, 'line-opacity': 0.95 }} />
          </Source>
        )}

        {/* Animated vehicle dot */}
        {routeGeojson && animating && (
          <AnimatedDot coords={routeGeojson.coordinates} isAnimating={animating} />
        )}
      </Map>

      {/* Sidebar */}
      {!panelOpen && (
        <div className="map-summary" style={{ position: 'absolute', top: 20, left: 20, zIndex: 10, padding: 14, borderRadius: 12, background: '#0f172a', maxWidth: 260 }}>
          <button className="primary-btn" onClick={() => setPanelOpen(true)}>Show controls and report</button>
          <p style={{ fontSize: 12, marginTop: 8 }}>Research prototype · simulated constraints</p>
          {routeStats && <p style={{ fontSize: 12, marginTop: 8 }}>{routeStats.distance} km · ETA {routeStats.eta} min · uncalibrated estimates</p>}
        </div>
      )}
      <div className="sidebar" style={{ display: panelOpen ? 'flex' : 'none' }}>
        <div className="header">
          <h1>RoadFit‑X</h1>
          <p>Vehicle routing research prototype</p>
          <p style={{ fontSize: 11 }}>Bengaluru · forecasts and vehicle fit</p>
          <button className="icon-btn" style={{ marginTop: 8, fontSize: 12 }} onClick={() => setPanelOpen(false)}>Show map</button>
        </div>

        <div className="view-tabs">
          <button className={viewMode === 'driver' ? 'active' : ''} onClick={() => setViewMode('driver')}>Driver routes</button>
          <button className={viewMode === 'officer' ? 'active' : ''} onClick={() => setViewMode('officer')}>Officer login</button>
        </div>
        <label className="imagery-toggle"><input type="checkbox" checked={satellite} onChange={e => setSatellite(e.target.checked)} /> Satellite basemap</label>
        {viewMode === 'officer' ? <TrafficConsole api={API_BASE_URL} onRoads={setHotspots} /> : <>
        {/* Origin */}
        <div className="form-group">
          <label htmlFor="route-origin">Origin</label>
          <div className="input-with-icon">
            <Search className="input-icon" size={16} />
            <input id="route-origin" className="form-control" placeholder="Search or click map…" value={origQuery}
              onChange={e => setOrigQuery(e.target.value)}
              onFocus={() => origResults.length && setShowOrigDrop(true)} />
            <button className="icon-btn locate-btn" title="Use my location"
              onClick={() => navigator.geolocation?.getCurrentPosition(p => {
                setOrigin({ lat: p.coords.latitude, lon: p.coords.longitude });
                setOrigQuery('📍 My location');
              })}>
              <Navigation size={16} />
            </button>
          </div>
          {showOrigDrop && origResults.length > 0 && (
            <div className="dropdown">
              {origResults.map(l => (
                <div key={l.place_id} className="dropdown-item" onClick={() => selectOrigin(l)}>
                  <MapPin size={13} />{l.display_name.split(',').slice(0, 2).join(', ')}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Destination */}
        <div className="form-group">
          <label htmlFor="route-destination">Destination</label>
          <div className="input-with-icon">
            <Search className="input-icon" size={16} />
            <input id="route-destination" className="form-control" placeholder="Search or click map…" value={destQuery}
              onChange={e => setDestQuery(e.target.value)}
              onFocus={() => destResults.length && setShowDestDrop(true)} />
          </div>
          {showDestDrop && destResults.length > 0 && (
            <div className="dropdown">
              {destResults.map(l => (
                <div key={l.place_id} className="dropdown-item" onClick={() => selectDest(l)}>
                  <MapPin size={13} />{l.display_name.split(',').slice(0, 2).join(', ')}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Vehicle */}
        <div className="form-group">
          <label>Vehicle Profile</label>
          <div className="vehicle-selector">
            {VEHICLES.map(v => (
              <button key={v.id} className={`vehicle-btn ${vehicle.id === v.id ? 'active' : ''}`}
                onClick={() => setVehicle(v)}>
                {v.label}
              </button>
            ))}
          </div>
          <div style={{ fontSize: 11, color: '#64748b', marginTop: 4 }}>
            W:{vehicle.width}m · H:{vehicle.height}m · {vehicle.weight}t
          </div>
        </div>

        <div className="form-group"><label htmlFor="route-objective">Routing goal</label>
          <select id="route-objective" className="form-control" value={objective} onChange={e => setObjective(e.target.value)}>
            <option value="eta">Reduce ETA and balance future arrivals</option>
            <option value="risk">Study reliability constraints</option>
          </select>
        </div>
        {objective === 'eta' && <div className="form-group">
          <label htmlFor="route-departure">Leave in minutes</label>
          <input id="route-departure" className="form-control" type="number" min="0" max="120" value={departure} onChange={e => setDeparture(e.target.value)} />
          <label className="imagery-toggle"><input type="checkbox" checked={reserve} onChange={e => setReserve(e.target.checked)} /> Reserve this route intention</label>
          <p className="muted">Other route requests can account for your expected arrivals. Recalculating releases your previous intention.</p>
        </div>}
        {/* Data Policy */}
        <div className="form-group">
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, color: '#94a3b8' }}>
            <CheckCircle size={14} /> Missing Data Policy
          </label>
          <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
            {['strict', 'conservative', 'exploratory'].map(p => (
              <button key={p} onClick={() => setDataPolicy(p)}
                style={{
                  flex: 1, padding: '8px 4px', borderRadius: 8, fontSize: 11, fontWeight: 600, cursor: 'pointer', border: 'none',
                  background: dataPolicy === p ? '#4f46e5' : 'rgba(255,255,255,0.05)',
                  color: dataPolicy === p ? 'white' : '#94a3b8',
                  transition: 'all 0.2s', textTransform: 'capitalize'
                }}>{p}</button>
            ))}
          </div>
        </div>

        {/* Cognitive Core (The Brain) */}
        {objective === 'risk' && <div className="form-group" style={{ background: 'rgba(236,72,153,0.05)', padding: '12px', borderRadius: '12px', border: '1px solid rgba(236,72,153,0.2)' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, color: '#f472b6' }}>
            <Brain size={14} /> Cognitive Routing Core (Episodic Memory)
          </label>
          <p style={{ fontSize: 11, color: '#94a3b8', marginTop: 4, marginBottom: 10 }}>
            Learn from 1,000 simulated routes for the selected vehicle using up to eight CPU workers. These are synthetic experiences.
          </p>
          <p style={{ fontSize: 11, color: '#94a3b8', marginTop: 4, marginBottom: 10 }}>
            <span style={{color: '#f9a8d4'}}>Working Memory:</span> An officer can close a directed segment for 15 minutes using the officer console.
          </p>
          <div style={{ display: 'flex', gap: '8px' }}>
            <button 
              onClick={handleTrainBrain} 
              disabled={trainingBrain}
              style={{
                flex: 1, padding: '8px', borderRadius: '8px', border: 'none', cursor: 'pointer',
                background: trainingBrain ? '#475569' : 'linear-gradient(135deg, #ec4899 0%, #db2777 100%)',
                color: 'white', fontWeight: 'bold', fontSize: 11, display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 4
              }}>
              {trainingBrain ? <><Loader2 size={12} className="spinner"/> Training...</> : '🧠 Train Simulation (1k)'}
            </button>
            <button 
              onClick={handleConsolidateBrain} 
              disabled={trainingBrain}
              style={{
                flex: 1, padding: '8px', borderRadius: '8px', border: 'none', cursor: 'pointer',
                background: 'linear-gradient(135deg, #8b5cf6 0%, #6d28d9 100%)',
                color: 'white', fontWeight: 'bold', fontSize: 11, display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 4
              }}>
              🌌 ML Consolidation
            </button>
          </div>
          {brainMsg && (
            <div className="fade-in" style={{ fontSize: 11, color: '#f9a8d4', marginTop: 8, textAlign: 'center' }}>
              {brainMsg}
            </div>
          )}
        </div>

        }
        {/* Simulation Controls */}
        <div className="form-group">
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, color: '#94a3b8' }}>
            <Zap size={14} /> Simulation Environment
          </label>

          {/* Rain */}
          <div style={{ marginBottom: 8 }}>
            <div style={{ fontSize: 11, color: '#64748b', marginBottom: 4 }}>🌧️ Rain Intensity</div>
            <div style={{ display: 'flex', gap: 6 }}>
              {RAIN_LEVELS.map(r => (
                <button key={r.id} onClick={() => setRainLevel(r.id)}
                  style={{
                    flex: 1, padding: '6px 2px', borderRadius: 8, fontSize: 10, fontWeight: 600, cursor: 'pointer', border: 'none',
                    background: rainLevel === r.id ? r.color : 'rgba(255,255,255,0.05)',
                    color: rainLevel === r.id ? 'white' : '#94a3b8',
                    transition: 'all 0.2s'
                  }}>{r.label}</button>
              ))}
            </div>
          </div>

          {/* Traffic */}
          <div>
            <div style={{ fontSize: 11, color: '#64748b', marginBottom: 4 }}>🚗 Traffic Level</div>
            <div style={{ display: 'flex', gap: 6 }}>
              {TRAFFIC_LEVELS.map(t => (
                <button key={t.id} onClick={() => setTrafficLevel(t.id)}
                  style={{
                    flex: 1, padding: '6px 2px', borderRadius: 8, fontSize: 10, fontWeight: 600, cursor: 'pointer', border: 'none',
                    background: trafficLevel === t.id ? t.color : 'rgba(255,255,255,0.05)',
                    color: trafficLevel === t.id ? 'white' : '#94a3b8',
                    transition: 'all 0.2s'
                  }}>{t.label}</button>
              ))}
            </div>
          </div>

          {/* CVaR toggle */}
          {objective === 'risk' && <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', marginTop: 8, padding: '8px 10px', background: 'rgba(99,102,241,0.08)', borderRadius: 8, border: '1px solid rgba(99,102,241,0.2)' }}>
            <input type="checkbox" checked={simulateCongestion} onChange={e => setSimulateCongestion(e.target.checked)}
              style={{ width: 14, height: 14, cursor: 'pointer' }} />
            <span style={{ fontSize: 12, color: '#a5b4fc' }}>Select candidates by simulated tail loss (CVaR)</span>
          </label>}
        </div>

        <button className="primary-btn" onClick={calculateRoute} disabled={loading || !origin || !destination}>
          {loading ? (
            <span style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
              <Loader2 className="spinner" size={18} /> Computing route…
            </span>
          ) : '⚡ Calculate Route'}
        </button>

        {errorMsg && (
          <div className="fade-in" style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 10, padding: '10px 14px', fontSize: 13, color: '#fca5a5', display: 'flex', gap: 8 }}>
            <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: 1 }} /> {errorMsg}
          </div>
        )}



        {routeStats && (
          <div className="fade-in results-container">
            <div className="result-card roadfit">
              <div className="result-header">RoadFit‑X Decision Report</div>

              <StatRow label="Distance" value={`${routeStats.distance} km`} />
              <StatRow label={routeStats.etaKind === 'eta' ? 'Forecast ETA' : 'ETA (p50)'} value={`${routeStats.eta} min`} />
              <StatRow label="Avg Speed" value={`${routeStats.avg_speed} km/h`} />
              {routeStats.prob != null && <StatRow label="Modeled survival (uncalibrated)" value={`${(routeStats.prob * 100).toFixed(1)}%`} color={probColor(routeStats.prob)} />}
              {routeStats.cvar != null && <StatRow label="Simulated tail loss" value={`${(routeStats.cvar / 60).toFixed(1)} min`} />}
              {routeStats.localDistance != null && <StatRow label="Local street distance" value={`${routeStats.localDistance} km`} />}
              {reservation && <button className="secondary-btn" onClick={async () => {
                try { await axios.post(`${API_BASE_URL}/traffic/reservations/cancel`, { token: reservation }); setReservation(null); }
                catch (e) { if (e?.response?.status === 404) setReservation(null); else setErrorMsg('Could not release route intention.'); }
              }}>Release route intention</button>}
              
              <div style={{ margin: '16px 0', padding: '12px', background: 'rgba(255,255,255,0.05)', borderRadius: '8px', borderLeft: '4px solid #10b981' }}>
                <div style={{ fontSize: 13, fontWeight: 'bold', color: '#fff', marginBottom: 8 }}>Metrics from available observations</div>
                {routeStats.etaKind === 'risk' && <StatRow label="Tail-Risk Ratio (TRR)" value={formatMetric(routeStats.metrics.TRR)} color={routeStats.metrics.TRR > 30 ? '#ef4444' : '#10b981'} />}
                {routeStats.etaKind === 'risk' && <StatRow label="ETTP (Time Penalty)" value={formatMetric(routeStats.metrics['ETTP_%'], '%')} color={routeStats.metrics['ETTP_%'] > 10 ? '#f59e0b' : '#10b981'} />}
                <StatRow label="Min observed clearance" value={formatMetric(routeStats.metrics.MinClearance_m, ' m')} />
                <StatRow label="Constraint coverage" value={formatMetric(routeStats.metrics['ConstraintCoverage_%'], '%')} />
                <StatRow label="MDEF (Missing Data)" value={formatMetric(routeStats.metrics['MDEF_%'], '%')} />
                <StatRow label="Known constraint violations" value={formatMetric(routeStats.metrics['ISER_%'], '%')} />
              </div>

              {routeStats.ensembleWinner && (
                <div style={{ marginTop: 12, padding: '8px', borderRadius: '8px', background: 'rgba(236,72,153,0.1)', border: '1px solid rgba(236,72,153,0.3)' }}>
                  <div style={{ fontSize: 11, color: '#f9a8d4', marginBottom: 2 }}>Routing method</div>
                  <div style={{ fontSize: 13, fontWeight: 'bold', color: '#ec4899' }}>{routeStats.ensembleWinner}</div>
                </div>
              )}
              
              {baselineGeojson && <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: '#94a3b8', marginTop: 12 }}>
                <div style={{ width: 12, height: 12, borderRadius: 2, background: '#ef4444' }} /> Baseline (B0)
                <div style={{ width: 12, height: 12, borderRadius: 2, background: '#10b981', marginLeft: 10 }} /> RoadFit-X
              </div>}

              <div style={{ marginTop: 10, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 10, padding: '3px 8px', borderRadius: 20, background: 'rgba(99,102,241,0.15)', color: '#a5b4fc' }}>
                  🌧️ {routeStats.rain}
                </span>
                <span style={{ fontSize: 10, padding: '3px 8px', borderRadius: 20, background: 'rgba(99,102,241,0.15)', color: '#a5b4fc' }}>
                  🚦 {routeStats.traffic}
                </span>
                {simulateCongestion && (
                  <span style={{ fontSize: 10, padding: '3px 8px', borderRadius: 20, background: 'rgba(139,92,246,0.2)', color: '#c4b5fd' }}>
                    CVaR Optimised
                  </span>
                )}
              </div>
            </div>

            {routeDetails?.warnings?.length > 0 && (
              <div className="result-card" style={{ borderColor: '#f59e0b', background: 'rgba(251,191,36,0.05)' }}>
                <div className="result-header" style={{ color: '#fbbf24' }}>
                  <AlertTriangle size={12} style={{ display: 'inline', marginRight: 4 }} />
                  Decision Rationale
                </div>
                {routeDetails.warnings.map((w, i) => (
                  <div key={i} style={{ fontSize: 12, color: '#fde68a', marginBottom: 4, display: 'flex', gap: 6 }}>
                    <span style={{ color: '#f59e0b', flexShrink: 0 }}>›</span>{w}
                  </div>
                ))}
              </div>
            )}

            {/* Stop animation button */}
            <button onClick={() => setAnimating(a => !a)}
              style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)', color: '#94a3b8', borderRadius: 8, padding: '8px 14px', cursor: 'pointer', fontSize: 12 }}>
              {animating ? '⏸ Pause Animation' : '▶ Play Animation'}
            </button>
          </div>
        )}
        </>}
      </div>
    </div>
  );
}
