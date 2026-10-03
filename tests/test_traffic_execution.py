import pytest
from src.evaluation.traffic_benchmark import service_finish, execute_fifo, read_tntp
from tests.test_anticipatory import network


def test_service_integrates_across_capacity_change():
    assert service_finish(0., 1., 3600., 0., 0., 2., 4.) == 1.
    # Starting at 1s, two PCU require 1s before shock and 2s at half capacity.
    assert service_finish(1., 2., 3600., 0., .5, 2., 4.) == 4.
    assert service_finish(2., 2., 3600., 0., .5, 2., 4.) == 5.


def test_execution_serves_concurrent_entries_in_fifo_order():
    g = network()
    trips = [{'departure': 0., 'kind': 'hatchback'}]*3
    paths = [((0, 1, 0),)]*3
    arrivals = execute_fifo(g, trips, paths, set(), background=0., seed=41)
    assert arrivals[0] < arrivals[1] < arrivals[2]
    assert arrivals[0] > 120.  # Free time plus a capacity-service headway.
