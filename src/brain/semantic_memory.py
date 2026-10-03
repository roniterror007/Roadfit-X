import sqlite3
import numpy as np
from sklearn.ensemble import RandomForestClassifier
import joblib
import networkx as nx
import os
from typing import Dict, Any, List, Tuple
from src.vehicle.geometry_constraints import _parse_osm_float

class SemanticKnowledgeEngine:
    """
    Semantic Memory: Abstract, generalized spatial knowledge.
    Uses Machine Learning (Random Forest) to learn WHY edges fail from the 
    Episodic Memory, and predicts failure on completely unseen edges.
    """
    def __init__(self, model_path=None):
        self.model_path = model_path
        self.model = None
        self.highway_encoding = {
            'motorway': 1, 'trunk': 2, 'primary': 3, 'secondary': 4,
            'tertiary': 5, 'unclassified': 6, 'residential': 7, 'service': 8,
            'living_street': 9, 'pedestrian': 10
        }
        self.weather_encoding = {'clear': 0, 'rain': 1, 'storm': 2, 'none': 0,
                                 'light': 1, 'moderate': 1, 'heavy': 2, 'extreme': 2}
        self.traffic_encoding = {'low': 0, 'normal': 0, 'peak': 1, 'heavy': 1, 'gridlock': 1}
        
        self.load_model()

    def load_model(self):
        if self.model_path and os.path.exists(self.model_path):
            try:
                self.model = joblib.load(self.model_path)
            except Exception as e:
                print(f"Failed to load semantic model: {e}")
                self.model = None

    def _extract_features(self, data: dict, weather: str, traffic: str) -> List[float]:
        """Converts graph edge data and context into an ML feature vector."""
        width = _parse_osm_float(data.get('width', data.get('_width_mean')), 3.0)
        length = float(data.get('length', 10.0))
        
        speed_raw = data.get('speed_kph', data.get('maxspeed', 25.0))
        if isinstance(speed_raw, list): speed_raw = speed_raw[0]
        try: speed_kph = float(speed_raw)
        except: speed_kph = 25.0
            
        hw = data.get('highway', 'unknown')
        if isinstance(hw, list): hw = hw[0]
        hw_enc = self.highway_encoding.get(hw, 0)
        
        w_enc = self.weather_encoding.get(weather, 0)
        t_enc = self.traffic_encoding.get(traffic, 0)
        
        # Synthetic Multimodal Features (Simulating Satellite CV and Raycasting)
        sim_housing_density, sim_parked_cars, raycast_occlusion = self._simulate_multimodal_features(hw, width, length)
        
        return [width, length, speed_kph, hw_enc, w_enc, t_enc, sim_housing_density, sim_parked_cars, raycast_occlusion]

    def _simulate_multimodal_features(self, highway_type: str, width: float, length: float) -> Tuple[float, float, float]:
        """
        Oracle simulating a Satellite CV and Raycast pipeline.
        In a production environment, this would call Google Earth Engine and Mapbox.
        """
        import random
        import hashlib
        seed = int.from_bytes(hashlib.sha256(f'{highway_type}:{width}:{length}'.encode()).digest()[:8], 'big')
        random = random.Random(seed)
        # Base distributions on highway type
        if highway_type in ['residential', 'living_street']:
            housing_density = random.normalvariate(50.0, 10.0) # Dense housing
            parked_cars = random.normalvariate(15.0, 5.0)      # Many parked cars blocking shoulders
            # Raycast: High occlusion, physical width is tighter than reported graph width
            raycast_occlusion = random.uniform(0.6, 0.95) 
        elif highway_type in ['service', 'pedestrian']:
            housing_density = random.normalvariate(20.0, 5.0)
            parked_cars = random.normalvariate(2.0, 1.0)
            raycast_occlusion = random.uniform(0.4, 0.7)
        elif highway_type in ['primary', 'secondary', 'trunk']:
            housing_density = random.normalvariate(5.0, 2.0)
            parked_cars = random.normalvariate(0.5, 0.5)
            # Raycast: Low occlusion, wide open avenues
            raycast_occlusion = random.uniform(0.0, 0.2)
        else:
            housing_density = random.normalvariate(10.0, 5.0)
            parked_cars = random.normalvariate(5.0, 2.0)
            raycast_occlusion = random.uniform(0.2, 0.5)
            
        # Ensure positive bounds
        return max(0.0, housing_density), max(0.0, parked_cars), max(0.0, min(1.0, raycast_occlusion))

    def consolidate(self, db_path: str, G: nx.MultiDiGraph):
        """
        REM Sleep Consolidation: Extracts all raw experiences from Episodic Memory
        and trains a generalized Random Forest classifier.
        """
        print('Starting semantic memory consolidation from simulated experiences...')
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute('SELECT edge_id, weather, traffic, success FROM experiences')
        rows = c.fetchall()
        conn.close()

        if not rows:
            print("  [!] Episodic memory is empty. Nothing to consolidate.")
            raise ValueError('Episodic memory is empty; no model was trained.')

        X = []
        y = []
        missing = 0
        for edge_id, weather, traffic, success in rows:
            try:
                u, v, k = edge_id.split('_')
                u, v, k = int(u), int(v), int(k)
                
                # Check if edge exists in graph (it might have been pruned)
                if G.has_edge(u, v, key=k):
                    data = G[u][v][k]
                    features = self._extract_features(data, weather, traffic)
                    X.append(features)
                    # 1 = failure, 0 = success (inverse of 'success' flag)
                    y.append(0 if success else 1)
                else:
                    missing += 1
            except Exception:
                missing += 1

        if not X:
            print("  [!] No valid features could be extracted.")
            raise ValueError('No valid training features; no model was trained.')

        print(f"  [+] Extracting {len(X)} experiences for ML training ({missing} skipped).")
        
        # Train a robust ensemble model to generalize failure patterns
        self.model = RandomForestClassifier(n_estimators=50, max_depth=10, random_state=42)
        self.model.fit(X, y)
        
        if self.model_path:
            joblib.dump(self.model, self.model_path)
        print(f"Consolidation complete. Semantic model destination: {self.model_path}.")

    def predict_failure_probability(self, data: dict, weather: str, traffic: str) -> float:
        """
        Imagines the risk of an UNSEEN road based on its topology and context.
        Returns a penalty probability [0.0 - 1.0].
        """
        if self.model is None:
            return 0.0 # No semantic knowledge yet
            
        features = self._extract_features(data, weather, traffic)
        # model.predict_proba returns array of shape (1, 2) where [0, 1] is prob of class 1 (failure)
        try:
            proba = self.model.predict_proba([features])[0]
            if len(proba) > 1:
                return float(proba[1]) # Probability of failure
            else:
                # If model only saw one class during training (e.g. all successes)
                return 1.0 if self.model.classes_[0] == 1 else 0.0
        except Exception:
            return 0.0
