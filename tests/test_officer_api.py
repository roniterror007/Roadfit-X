import hashlib
import json
import time
import pytest
from fastapi.testclient import TestClient
from src.api import officer_auth, traffic_api, server
from tests.test_research_correctness import graph
from src.brain.cognitive_core import CognitiveCore


@pytest.fixture
def client(tmp_path, monkeypatch):
    server.app.dependency_overrides.clear()
    auth = officer_auth.OfficerAuth(tmp_path)
    salt = '00'*16
    (tmp_path/'officer.json').write_text(json.dumps({'username': 'officer', 'salt': salt,
                                                   'hash': auth.password_hash('test-password', salt)}))
    monkeypatch.setattr(officer_auth, 'AUTH', auth)
    monkeypatch.setattr(traffic_api, 'AUTH', auth)
    g = graph()
    tree, nodes = server._build_kd_tree(g)
    monkeypatch.setattr(server, 'MASTER_GRAPH', g)
    monkeypatch.setattr(server, 'KD_TREE', tree)
    monkeypatch.setattr(server, 'NODE_IDS', nodes)
    monkeypatch.setattr(server, 'ARRIVAL_ROUTER', None)
    monkeypatch.setattr(server, 'BRAIN', CognitiveCore(':memory:'))
    yield TestClient(server.app)
    server.app.dependency_overrides.clear()


def login(client):
    result = client.post('/officer/login', json={'username': 'officer', 'password': 'test-password'})
    assert result.status_code == 200
    return {'Authorization': 'Bearer '+result.json()['access_token']}


def test_officer_role_is_enforced_and_logout_revokes_session(client):
    assert client.get('/officer/dashboard').status_code == 401
    assert client.post('/officer/forecast', json={'edge_id': '0:1:0', 'flow_pcu_h': 1000}).status_code == 401
    assert client.post('/brain/roadblock', json={'lat': 12.935, 'lon': 77.624}).status_code == 401
    assert client.post('/officer/login', json={'username': 'officer', 'password': 'wrong'}).status_code == 401
    headers = login(client)
    assert client.get('/officer/dashboard', headers=headers).status_code == 200
    assert client.post('/officer/logout', headers=headers).status_code == 200
    assert client.get('/officer/dashboard', headers=headers).status_code == 401


def test_rate_limit_and_expired_session(tmp_path):
    now = [0.]
    auth = officer_auth.OfficerAuth(tmp_path, clock=lambda: now[0])
    credentials = auth.credentials()
    for _ in range(10):
        with pytest.raises(Exception) as failure:
            auth.login('officer', 'wrong', 'local')
        assert failure.value.status_code == 401
    with pytest.raises(Exception) as failure:
        auth.login('officer', 'wrong', 'local')
    assert failure.value.status_code == 429
    auth.sessions['test'] = 100.
    now[0] = 101.
    with pytest.raises(Exception) as failure:
        auth.validate('test')
    assert failure.value.status_code == 401


def test_route_intentions_are_explicit_and_released(client):
    request = dict(orig_lat=12.935, orig_lon=77.624, dest_lat=12.938, dest_lon=77.627,
                   vehicle_class='hatchback', unknown_data_policy='exploratory')
    preview = client.post('/traffic/route', json=request)
    assert preview.status_code == 200, preview.text
    assert preview.json()['selected_route']['reservation_token'] is None
    booked = client.post('/traffic/route', json={**request, 'reserve': True})
    assert booked.status_code == 200, booked.text
    token = booked.json()['selected_route']['reservation_token']
    result = client.get('/officer/dashboard', headers=login(client)).json()
    assert result['active_reservations'] == 1
    assert 'token' not in json.dumps(result)
    assert client.post('/traffic/reservations/cancel', json={'token': token}).status_code == 200
    assert client.post('/traffic/reservations/cancel', json={'token': token}).status_code == 404


def test_manual_forecasts_closures_and_input_validation(client):
    headers = login(client)
    assert client.post('/officer/forecast', headers=headers, json={'edge_id': '0:1:0', 'flow_pcu_h': 2500, 'starts_in_min': 5}).status_code == 200
    result = client.get('/officer/dashboard', headers=headers).json()
    assert result['roads'][0]['basis'] == 'officer_manual_scenario'
    assert result['roads'][0]['forecast_pcu_h'] == 2500
    assert client.post('/officer/closure', headers=headers, json={'edge_id': '0:1:0'}).status_code == 200
    assert (0, 1, 0) in server._blocked_edges(server.MASTER_GRAPH)
    assert client.post('/officer/closure', headers=headers, json={'edge_id': '99:100:0'}).status_code == 404
    assert client.post('/officer/forecast', headers=headers, json={'edge_id': '0:1:0', 'flow_pcu_h': -1}).status_code == 422
    assert client.post('/traffic/route', json={'orig_lat': 12.935, 'orig_lon': 77.624, 'dest_lat': 12.938,
                                            'dest_lon': 77.627, 'vehicle_class': 'helicopter'}).status_code == 422


def test_officer_rebalances_future_intention_and_owner_token_survives(client):
    request = dict(orig_lat=12.935, orig_lon=77.624, dest_lat=12.938, dest_lon=77.627,
                   vehicle_class='hatchback', unknown_data_policy='exploratory', reserve=True, departure_in_min=5.)
    result = client.post('/traffic/route', json=request)
    assert result.status_code == 200, result.text
    token = result.json()['selected_route']['reservation_token']
    before = client.post('/traffic/reservations/status', json={'token': token}).json()
    first_edge = server.ARRIVAL_ROUTER.ledger.bookings[token]['plan']['edges'][0]
    headers = login(client)
    client.post('/officer/closure', headers=headers, json={'edge_id': ':'.join(map(str, first_edge))})
    review = client.post('/officer/rebalance', headers=headers, json={'max_trips': 10})
    assert review.status_code == 200, review.text
    assert review.json()['changed_routes'] == 1
    after = client.post('/traffic/reservations/status', json={'token': token}).json()
    assert after['reviewed'] and after['geometry'] != before['geometry']
    assert abs(after['avg_speed_kmh'] - after['distance_km'] / (after['eta_min'] / 60)) < .2
    assert 'MinClearance_m' in after['academic_metrics']
    assert len(server.ARRIVAL_ROUTER.ledger.bookings) == 1
    assert client.post('/traffic/reservations/cancel', json={'token': token}).status_code == 200
    assert not server.ARRIVAL_ROUTER.ledger.entries


def test_place_search_uses_local_map_and_validates_query(client):
    result = client.get('/traffic/places', params={'q': 'Koramangala'})
    assert result.status_code == 200 and result.json()[0]['place_id'] == 'area-Koramangala'
    assert client.get('/traffic/places', params={'q': 'x'}).status_code == 422
