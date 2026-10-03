import time
from typing import Dict, Tuple

class WorkingMemory:
    """
    Working Memory: Volatile, short-term cache for dynamic hazards.
    Simulates immediate sensory input (e.g., a live roadblock) that 
    decays over time and doesn't permanently scar the Episodic DB.
    """
    def __init__(self):
        # Maps edge_id -> (penalty_probability, expiry_timestamp)
        self._cache: Dict[str, Tuple[float, float]] = {}

    def report_live_hazard(self, edge_id: str, severity: float, ttl_seconds: int = 900):
        """
        Reports a live hazard (e.g., roadblock, accident) on an edge.
        severity: 0.0 to 1.0
        ttl_seconds: Time to live in seconds (default 15 mins)
        """
        expiry = time.time() + ttl_seconds
        self._cache[edge_id] = (severity, expiry)
        print(f"Working memory: hazard on {edge_id} (severity {severity:.2f}), TTL {ttl_seconds}s.")

    def get_live_penalty(self, edge_id: str) -> float:
        """
        Retrieves the immediate hazard penalty if it exists and hasn't expired.
        """
        if edge_id in self._cache:
            severity, expiry = self._cache[edge_id]
            if time.time() < expiry:
                return severity
            else:
                # Expired, clean it up
                del self._cache[edge_id]
        return 0.0
