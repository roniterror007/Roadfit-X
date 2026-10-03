import osmnx as ox
import time
import os
from src.brain.cognitive_core import CognitiveCore

def run_stress_test():
    db_name = "brain_memory_100k.db"
    # Clean previous db if exists BEFORE initializing CognitiveCore
    if os.path.exists(db_name):
        os.remove(db_name)

    print("Loading graph for 100k stress test...")
    G = ox.load_graphml("data/koramangala_enriched_v2.graphml")
    
    brain = CognitiveCore(db_name)
    
    print("Starting 10,000 simulations on CPU cores...")
    start = time.time()
    brain.train_brain(G, iterations=10000)
    print(f"10k Episodic Simulations finished in {time.time() - start:.2f} seconds.")
    
    print("Consolidating Semantic Memory...")
    brain.semantic.consolidate(db_name, G)
    print("Done!")

if __name__ == "__main__":
    run_stress_test()
