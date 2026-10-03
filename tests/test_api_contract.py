"""API validation, live-roadblock, geometry, and status regressions."""
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString
from src.api import server
from src.brain.cognitive_core import CognitiveCore
from tests.test_research_correctness import graph

@pytest.fixture
def client(monkeypatch):
    G = graph()
    tree, nodes = server._build_kd_tree(G)
    monkeypatch.setattr(server, 'MASTER_GRAPH', G)
    monkeypatch.setattr(server, 'KD_TREE', tree)
    monkeypatch.setattr(server, 'NODE_IDS', nodes)
    monkeypatch.setattr(server, 'BRAIN', CognitiveCore(':memory:'))
    monkeypatch.setattr(server, 'JOBS', {})
    monkeypatch.delenv('ROADFIT_ALLOW_DOWNLOAD', raising=False)
    server.app.dependency_overrides[server.require_officer] = lambda: 'test-officer'
    yield TestClient(server.app)
    server.app.dependency_overrides.clear()

def request():
    return dict(orig_lat=12.935, orig_lon=77.624, dest_lat=12.938, dest_lon=77.627,
                vehicle_width=2.4, vehicle_height=2.8, vehicle_weight=5.,
                unknown_data_policy='exploratory')

@pytest.mark.parametrize('field,value', [
    ('vehicle_width', -1), ('orig_lat', 91), ('unknown_data_policy', 'made_up'),
    ('rain_level', 'storm'), ('traffic_level', 'peak'),
])
def test_validation(client, field, value):
    data = request()
    data[field] = value
    assert client.post('/route/plan', json=data).status_code == 422

def test_map_health_and_both_endpoint_coverage(client):
    assert client.get('/health').json()['nodes'] == 4
    for field in ['orig_lat', 'dest_lat']:
        data = request()
        data[field] = 22.
        assert client.post('/route/plan', json=data).status_code == 422

def test_live_roadblock_cannot_be_bypassed_by_unbiased_selection(client):
    before = client.post('/route/plan', json=request())
    assert before.status_code == 200, before.text
    assert [77.625, 12.936] in before.json()['selected_route']['geometry']['coordinates']
    assert client.post('/brain/roadblock', json=dict(lat=12.936, lon=77.625)).status_code == 200
    after = client.post('/route/plan', json=request())
    assert after.status_code == 200, after.text
    coords = after.json()['selected_route']['geometry']['coordinates']
    assert [77.625, 12.936] not in coords
    assert any(point == pytest.approx([77.626, 12.937]) for point in coords)
    assert after.json()['selected_route']['probability_basis'] == 'uncalibrated_engineering_model'

def test_cvar_response_has_computed_quantiles_and_tail_loss(client):
    data = {**request(), 'simulate_congestion': True, 'rain_level': 'heavy', 'traffic_level': 'heavy'}
    response = client.post('/route/plan', json=data)
    assert response.status_code == 200, response.text
    route = response.json()['selected_route']
    assert route['eta_p90_min'] >= route['eta_p50_min']
    assert route['cvar_risk'] == round(route['simulation']['cvar_90_sec'], 2)
    assert route['search']['status'] in ('ok', 'candidate_limit')

def test_geometry_is_oriented_to_selected_directed_edge():
    G = graph()
    G[0][1][0]['geometry'] = LineString([(77.625, 12.936), (77.624, 12.935)])
    coords = server._extract_route_coords_from_edges(G, [0, 1], [(0, 1, 0, G[0][1][0])])
    assert coords == [[77.624, 12.935], [77.625, 12.936]]

def test_invalid_training_counts_do_not_start_jobs(client):
    assert client.post('/brain/train?iterations=0').status_code == 422
    assert client.post('/brain/train?iterations=100001').status_code == 422
    assert client.get('/brain/jobs/no-such-job').status_code == 404

def test_failed_job_is_reported(client, monkeypatch):
    class ImmediateExecutor:
        def submit(self, fn):
            fn()
    monkeypatch.setattr(server, 'WORKERS', ImmediateExecutor())
    def fail():
        raise RuntimeError('Expected test failure')
    result = server._submit_job(fail)
    status = client.get('/brain/jobs/'+result['job_id']).json()
    assert status['status'] == 'failed'
    assert status['message'] == 'Expected test failure'


def test_training_profile_matches_routing_and_separates_vehicle_sizes(client, monkeypatch):
    class ImmediateExecutor:
        def submit(self, fn):
            fn()
    captured = {}
    monkeypatch.setattr(server, 'WORKERS', ImmediateExecutor())
    def train(G, iterations, vehicle=None):
        captured['vehicle_type'] = vehicle.vehicle_type
        return {'edge_experiences': 10}
    monkeypatch.setattr(server.BRAIN, 'train_brain', train)
    data = request()
    response = client.post('/brain/train?iterations=1', json={
        'vehicle_width': data['vehicle_width'], 'vehicle_height': data['vehicle_height'],
        'vehicle_weight': data['vehicle_weight']})
    assert response.status_code == 200
    assert captured['vehicle_type'] == server._vehicle_profile(server.CoordinateRequest(**data)).vehicle_type
    other = {**data, 'vehicle_width': 1.8}
    assert captured['vehicle_type'] != server._vehicle_profile(server.CoordinateRequest(**other)).vehicle_type
    status = client.get('/brain/jobs/'+response.json()['job_id']).json()
    assert status['result']['edge_experiences'] == 10

