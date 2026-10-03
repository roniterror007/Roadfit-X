import pandas as pd
from pathlib import Path

def main():
    base = Path('experiments/sumo_v2')
    trips = pd.read_csv(base / 'trips.csv')
    
    region = 'Indiranagar'
    signal = 'actuated'
    seed = 61
    
    # Get trips for both methods
    static_trips = trips[(trips.region == region) & (trips.signal == signal) & (trips.seed == seed) & (trips.method == 'static') & (trips.adoption == 1.0) & (trips.trips == 900)]
    queue_trips = trips[(trips.region == region) & (trips.signal == signal) & (trips.seed == seed) & (trips.method == 'roadfit_queue_aware') & (trips.adoption == 1.0) & (trips.trips == 900)]
    
    if static_trips.empty or queue_trips.empty:
        print("Could not find matching trips.")
        return

    # Merge on vehicle to compare side-by-side
    comparison = pd.merge(
        static_trips[['vehicle', 'actual_time_s', 'local_distance_m']], 
        queue_trips[['vehicle', 'actual_time_s', 'local_distance_m']], 
        on='vehicle', 
        suffixes=('_static', '_queue')
    )
    
    comparison['diff_s'] = comparison['actual_time_s_queue'] - comparison['actual_time_s_static']
    
    print("Top 10 trips where queue_aware was WORSE than static (diff > 0):")
    worst = comparison.sort_values(by='diff_s', ascending=False).head(10)
    for _, row in worst.iterrows():
        print(f"Vehicle: {row['vehicle']}")
        print(f"  Static: {row['actual_time_s_static']}s (local dist: {row['local_distance_m_static']}m)")
        print(f"  Queue:  {row['actual_time_s_queue']}s (local dist: {row['local_distance_m_queue']}m)")
        print(f"  Diff:   +{row['diff_s']}s")

    print("\nSummary metrics:")
    print(f"Mean Static: {comparison['actual_time_s_static'].mean()}s")
    print(f"Mean Queue:  {comparison['actual_time_s_queue'].mean()}s")
    print(f"Mean Diff:   {comparison['diff_s'].mean()}s")
    
if __name__ == '__main__':
    main()
