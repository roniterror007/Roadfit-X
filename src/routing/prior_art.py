"""Auditable routing primitives from Pan et al., DCOSS 2012.

Equation 1 (Greenshields), Definition 2 (weighted path entropy), and the
RkSP/DSP selection rules. Experiment adapters must disclose different vehicle
selection, candidate-generation and observation protocols; these primitives
alone are not a reproduction of the complete 2012 experiment.
"""
import math


def greenshields_time(length, speed, count, storage, speed_floor=.05):
    """Equation 1 with an explicit numerical floor at/above jam density."""
    if length <= 0 or speed <= 0 or storage <= 0 or count < 0:
        raise ValueError('Positive geometry/storage and nonnegative occupancy required.')
    return length / (speed * max(speed_floor, 1. - count / storage))


def path_entropies(paths, footprints, capacities):
    """Definition 2, interpreted using the paper's Figure 1 worked example.

    The normalizer sums counts over the UNION of candidates; each entropy sums
    only that candidate's segments. The printed equation's index is ambiguous,
    but the worked values 1.49, 1.16, 0.58 determine this interpretation.
    Zero-count terms are zero; with no footprints all candidates tie at zero.
    Exp(entropy) has the same ordering, so exponentiation is unnecessary.
    """
    union = set(e for path in paths for e in path)
    if any(footprints.get(e, 0.) < 0 for e in union):
        raise ValueError('Footprints cannot be negative.')
    if not capacities or any(v <= 0 for v in capacities.values()):
        raise ValueError('Storage capacities must be positive.')
    average = sum(capacities.values()) / len(capacities)
    total = sum(footprints.get(e, 0.) for e in union)
    if total == 0:
        return [0.] * len(paths)
    return [-sum((average / capacities[e]) * (footprints.get(e, 0.) / total)
                 * math.log(footprints[e] / total)
                 for e in path if footprints.get(e, 0.) > 0) for path in paths]


def choose_path(method, paths, travel_times, footprints, capacities, rng):
    """Choose among ordered, already filtered candidate paths."""
    if not paths:
        return None
    times = [sum(travel_times[e] for e in path) for path in paths]
    if method == 'dsp':
        return min(range(len(paths)), key=lambda i: (times[i], i))
    if method == 'rksp':
        return int(rng.integers(len(paths)))
    if method == 'ebksp':
        entropies = path_entropies(paths, footprints, capacities)
        return min(range(len(paths)), key=lambda i: (entropies[i], times[i], i))
    raise ValueError(f'Unknown prior-art selector: {method}')
