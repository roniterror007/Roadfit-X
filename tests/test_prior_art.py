import math
import numpy as np
import pytest

from src.routing.prior_art import choose_path, greenshields_time, path_entropies


def test_published_pan_figure_one_worked_example():
    paths = [('ab', 'bg', 'gh', 'hi', 'ij'), ('ab', 'bc', 'ch', 'hi', 'ij'),
             ('ab', 'bc', 'cd', 'di', 'ij')]
    counts = dict(zip(['ab', 'bg', 'gh', 'hi', 'ij', 'bc', 'ch', 'cd', 'di'], [1, 1, 2, 2, 2, 0, 1, 0, 0]))
    capacities = {e: 1. for e in counts}
    values = path_entropies(paths, counts, capacities)
    assert [round(x, 2) for x in values] == [1.49, 1.16, .58]
    assert choose_path('ebksp', paths, capacities, counts, capacities, np.random.default_rng(41)) == 2


def test_empty_footprints_and_network_capacity_weights():
    paths = [('a',), ('b',)]
    assert path_entropies(paths, {}, {'a': 1, 'b': 2}) == [0., 0.]
    scores = path_entropies(paths, {'a': 1, 'b': 1}, {'a': 1, 'b': 2})
    assert scores[0] == pytest.approx(2 * scores[1])


def test_greenshields_equation_and_finite_jam_limit():
    assert greenshields_time(100., 10., 0., 20.) == 10.
    assert greenshields_time(100., 10., 10., 20.) == 20.
    assert math.isfinite(greenshields_time(100., 10., 22., 20.))
    with pytest.raises(ValueError):
        greenshields_time(100., 10., -1., 20.)


def test_random_choice_is_uniform_and_shortest_choice_is_deterministic():
    paths = [('a',), ('b',), ('c',)]
    costs = {'a': 3., 'b': 1., 'c': 2.}
    rng = np.random.default_rng(41)
    assert choose_path('dsp', paths, costs, {}, costs, rng) == 1
    draws = np.bincount([choose_path('rksp', paths, costs, {}, costs, rng) for _ in range(6000)], minlength=3)
    assert np.all(abs(draws - 2000) < 150)
