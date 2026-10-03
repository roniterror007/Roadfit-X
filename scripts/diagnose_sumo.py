import pandas as pd
import json
from pathlib import Path
from src.evaluation.failure_diagnostics import diagnose_scenario

def main():
    base = Path('experiments/sumo_v2')
    episodes = pd.read_csv(base / 'episodes.csv')
    trips = pd.read_csv(base / 'trips.csv')
    
    # Filter to one problematic scenario where queue_aware was worse than static
    # e.g., high load, high adoption
    scenario = episodes[(episodes.method == 'roadfit_graduated') & (episodes.trips == 900) & (episodes.adoption == 1.0) & (episodes.region == 'Indiranagar')].iloc[0]
    region = scenario.region
    signal = scenario.signal
    seed = scenario.seed
    
    print(f"Diagnosing region={region}, signal={signal}, seed={seed} for roadfit_queue_aware")
    
    scenario_trips = trips[(trips.region == region) & (trips.signal == signal) & (trips.seed == seed) & (trips.method == 'roadfit_graduated')]
    
    # We don't have the decisions dict saved by default in sumo_benchmark, but we can pass an empty dict
    # since diagnose_trip handles missing decisions gracefully (it just omits route explanation for unfinished).
    diagnostics = diagnose_scenario(scenario_trips, {})
    
    # Print a summary
    print(json.dumps(diagnostics['summary'], indent=2))
    print("\nCompletion rate: ", diagnostics['completion_rate_pct'])
    print("\nWorst trips:")
    for trip in diagnostics['worst_trips']:
        print(f"Vehicle: {trip['vehicle']}, Time: {trip['penalized_s']}s, Reason: {trip['reason']}")
        print(f"  Recommendation: {trip['recommendation']}")

if __name__ == '__main__':
    main()
