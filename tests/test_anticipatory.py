import concurrent.futures
from dataclasses import replace
import networkx as nx
import pytest

from src.routing.anticipatory import ArrivalRouter, TrafficLedger, Control
from src.vehicle.profiles import make_vehicle, legal_access


def network():
    g = nx.MultiDiGraph()
    for u, v, free in [(0, 1, 120), (1, 3, 120), (0, 2, 160), (2, 3, 160)]:
        g.add_edge(u, v, length=1000., highway='primary', width=8., maxheight=8., maxweight=50.,
                   free_time_s=free, capacity_pcu_h=600.)
    return g


def route(router, **kwargs):
    return router.plan(0, 3, make_vehicle('hatchback'), 'hatchback', departure=0., background=0., **kwargs)


def test_prearrival_prediction_changes_route_before_congested_link():
    ledger = TrafficLedger(Control(load_model='bpr'), clock=lambda: 0.)
    ledger.set_forecast((1, 3, 0), 120., 120., 1800.)
    router = ArrivalRouter(network(), ledger)
    anticipatory = route(router)
    reactive = route(router, control=replace(ledger.control, use_future=False))
    assert anticipatory['edges'] == ((0, 2, 0), (2, 3, 0))
    assert reactive['edges'] == ((0, 1, 0), (1, 3, 0))
    assert anticipatory['eta_s'] < router.score(reactive['edges'], 'hatchback', 0., 0., ledger.control,
                                               {}, ledger.forecasts, 0, 3)['eta_s']


def test_preview_does_not_book_and_cancel_releases_every_entry():
    ledger = TrafficLedger(clock=lambda: 0.)
    router = ArrivalRouter(network(), ledger)
    route(router)
    assert not ledger.entries and not ledger.bookings
    plan = route(router, commit=True)
    assert len(ledger.entries) == 2
    assert ledger.cancel(plan['reservation_token'])
    assert not ledger.entries and not ledger.bookings
    assert not ledger.cancel(plan['reservation_token'])


def test_expired_booking_releases_capacity():
    now = [0.]
    ledger = TrafficLedger(clock=lambda: now[0])
    router = ArrivalRouter(network(), ledger)
    plan = route(router, commit=True)
    now[0] = plan['arrival']+301.
    entries, _, _ = ledger.snapshot()
    assert entries == {} and ledger.bookings == {}


def test_parallel_legal_edge_key_survives_collapse():
    g = network()
    g[0][1][0]['motorcar'] = 'no'
    g.add_edge(0, 1, key=7, **{**g[0][1][0], 'motorcar': 'yes', 'free_time_s': 121.})
    result = route(ArrivalRouter(g, TrafficLedger(clock=lambda: 0.)))
    assert result['edges'][0] == (0, 1, 7)


def test_live_closure_cannot_be_bypassed_and_alternate_parallel_key_works():
    g = network()
    g.add_edge(0, 1, key=7, **{**g[0][1][0], 'free_time_s': 125.})
    router = ArrivalRouter(g, TrafficLedger(clock=lambda: 0.))
    result = route(router, blocked={(0, 1, 0)})
    assert result['edges'][0] == (0, 1, 7)
    result = route(router, blocked={(0, 1, 0), (0, 1, 7)})
    assert result['edges'][0] == (0, 2, 0)


@pytest.mark.parametrize('kind,road,expected', [('motorcycle', 'cycleway', False),
                                               ('bicycle', 'cycleway', True),
                                               ('bicycle', 'motorway', False),
                                               ('truck', 'primary', True)])
def test_mode_legality(kind, road, expected):
    assert legal_access({'highway': road}, kind) is expected
    assert not legal_access({'highway': 'primary', 'access': 'private'}, kind)


def test_specific_access_overrides_generic_and_destination_is_terminal_only():
    assert legal_access({'access': 'no', 'bicycle': 'yes'}, 'bicycle')
    assert not legal_access({'access': 'destination'}, 'hatchback')
    assert legal_access({'access': 'destination'}, 'hatchback', terminal=True)
    assert not legal_access({'highway': 'footway', 'access': 'yes'}, 'hatchback')
    assert not legal_access({'highway': 'steps', 'bicycle': 'yes'}, 'bicycle')


def test_fit_changes_motorcycle_and_truck_paths():
    g = network()
    g[0][1][0]['width'] = 1.2
    router = ArrivalRouter(g, TrafficLedger(clock=lambda: 0.))
    bike = router.plan(0, 3, make_vehicle('motorcycle'), 'motorcycle', departure=0., background=0.)
    truck = router.plan(0, 3, make_vehicle('truck'), 'truck', departure=0., background=0.)
    assert bike['edges'][0] == (0, 1, 0)
    assert truck['edges'][0] == (0, 2, 0)


def test_residential_budget_and_no_locality_ablation():
    g = nx.MultiDiGraph()
    for u, v, h in [(0, 1, 'primary'), (1, 2, 'residential'), (2, 3, 'primary')]:
        g.add_edge(u, v, highway=h, length=100., width=5., maxheight=5., maxweight=30.,
                   capacity_pcu_h=100., free_time_s=20.)
    ledger = TrafficLedger(clock=lambda: 0.)
    router = ArrivalRouter(g, ledger)
    # With hard cutoff (graduated=False), all candidates are rejected.
    result = router.plan(0, 3, make_vehicle('hatchback'), 'hatchback', departure=0., background=.9,
                         control=replace(ledger.control, graduated_locality=False))
    assert result is not None and result.get('status') == 'no_feasible_plan'
    # Without locality, routing proceeds.
    assert router.plan(0, 3, make_vehicle('hatchback'), 'hatchback', departure=0., background=.9,
                       control=replace(ledger.control, use_locality=False)) is not None


def test_graduated_locality_allows_congested_residential():
    """Graduated penalty should allow routing through congested residential
    edges instead of rejecting them outright."""
    g = nx.MultiDiGraph()
    for u, v, h in [(0, 1, 'primary'), (1, 2, 'residential'), (2, 3, 'primary')]:
        g.add_edge(u, v, highway=h, length=100., width=5., maxheight=5., maxweight=30.,
                   capacity_pcu_h=100., free_time_s=20.)
    ledger = TrafficLedger(clock=lambda: 0.)
    router = ArrivalRouter(g, ledger)
    # Graduated locality (default) allows the route with a higher cost.
    result = router.plan(0, 3, make_vehicle('hatchback'), 'hatchback', departure=0., background=.9,
                         control=replace(ledger.control, graduated_locality=True))
    assert result is not None
    assert result.get('status') != 'no_feasible_plan'
    assert result['local_distance_km'] > 0


def test_queue_model_initial_backlog():
    """Queue prediction with nonzero initial backlog should produce higher delay."""
    from src.routing.queue_model import predicted_queue as pq_ext
    control = Control()
    # Zero initial backlog.
    backlog0, rate0 = pq_ext(('e1',), 0, 0., 600., 0., control, {}, {}, {},
                              initial_backlog=0.)
    # Positive initial backlog.
    backlog5, rate5 = pq_ext(('e1',), 0, 0., 600., 0., control, {}, {}, {},
                              initial_backlog=5.)
    assert backlog5 >= backlog0 + 4.9  # at least most of the initial backlog remains


def test_downstream_spillback_reduces_service():
    """When downstream edge is full, upstream service rate should drop."""
    from src.routing.queue_model import predicted_queue as pq_ext
    control = Control()
    ds_clear = {'occupancy': 5., 'storage': 100.}
    ds_full = {'occupancy': 100., 'storage': 100.}
    _, rate_clear = pq_ext(('e1',), 0, 0., 600., 0., control, {}, {}, {},
                            downstream_state={('e1',): ds_clear})
    _, rate_full = pq_ext(('e1',), 0, 0., 600., 0., control, {}, {}, {},
                           downstream_state={('e1',): ds_full})
    assert rate_full < rate_clear * 0.1  # should be reduced to ~5%


def test_plan_returns_diagnostics_on_failure():
    """When no plan is feasible, plan() should return diagnostic info instead of None."""
    g = nx.MultiDiGraph()
    # Only route is through a residential edge with hard cutoff.
    for u, v, h in [(0, 1, 'primary'), (1, 2, 'residential'), (2, 3, 'primary')]:
        g.add_edge(u, v, highway=h, length=100., width=5., maxheight=5., maxweight=30.,
                   capacity_pcu_h=100., free_time_s=20.)
    ledger = TrafficLedger(clock=lambda: 0.)
    router = ArrivalRouter(g, ledger)
    result = router.plan(0, 3, make_vehicle('hatchback'), 'hatchback', departure=0., background=.9,
                         control=replace(ledger.control, graduated_locality=False))
    assert result is not None
    assert result['status'] == 'no_feasible_plan'
    assert 'diagnostics' in result
    assert result['diagnostics']['reason'] == 'all_candidates_rejected'


def test_concurrent_bookings_are_atomic_and_respect_candidate_detour_bound():
    ledger = TrafficLedger(clock=lambda: 0.)
    router = ArrivalRouter(network(), ledger)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: route(router, commit=True), range(20)))
    assert len(ledger.bookings) == 20
    assert sum(ledger.entries.values()) == 40.
    assert all(r['eta_s'] <= 1.25*r['fastest_candidate_eta_s']+1e-8 for r in results)


def test_first_thru_node_prevents_using_zone_as_shortcut():
    g = network()
    g.graph['first_thru_node'] = 2
    router = ArrivalRouter(g, TrafficLedger(clock=lambda: 0.))
    assert route(router)['edges'][0] == (0, 2, 0)
