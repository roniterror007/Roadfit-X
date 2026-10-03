"""Evidence tracking and calibration target framework.

Separates what is observed from what is assumed, and defines calibration
targets with clear validation requirements.  This addresses the critique
that Bengaluru evidence is insufficient by making the evidence boundary
explicit and machine-readable.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any
import json
from pathlib import Path


class EvidenceLevel(Enum):
    """Classification of data provenance."""
    OBSERVED = 'observed'           # Direct field measurement or survey
    CALIBRATED = 'calibrated'       # Fitted to observed data
    ASSUMED = 'assumed'             # Engineering assumption, not calibrated
    SYNTHETIC = 'synthetic'         # Generated for testing, not from observations
    UNKNOWN = 'unknown'


@dataclass
class EvidenceItem:
    """A single piece of evidence used in evaluation."""
    name: str
    level: EvidenceLevel
    source: str
    description: str
    validation_status: str = 'unvalidated'  # 'validated', 'partially_validated', 'unvalidated'
    spatial_scope: str = ''                 # e.g., 'Indiranagar', 'Bengaluru', 'general'
    temporal_scope: str = ''                # e.g., '24 Aug 2021 06:00-20:00'
    limitations: List[str] = field(default_factory=list)


@dataclass
class CalibrationTarget:
    """A measurable quantity that should be calibrated against observations."""
    name: str
    current_value: Any
    current_basis: EvidenceLevel
    required_evidence: str
    target_metric: str = ''           # e.g., 'MAE < 5 min', '95% CI excludes zero'
    achieved: bool = False
    notes: str = ''


def build_bengaluru_evidence() -> Dict[str, EvidenceItem]:
    """Document all evidence items for the Bengaluru evaluation."""
    return {
        'road_topology': EvidenceItem(
            'Road network topology',
            EvidenceLevel.OBSERVED,
            'OpenStreetMap (ODbL)',
            'Graph structure, edge lengths, road classifications.',
            validation_status='validated',
            spatial_scope='Indiranagar, Hebbal',
            limitations=['Tag completeness varies', 'No turn restrictions implemented'],
        ),
        'signal_placement': EvidenceItem(
            'Traffic signal locations',
            EvidenceLevel.ASSUMED,
            'Eight high-degree junctions per network',
            'Signal placement at intersections above a degree threshold.',
            validation_status='unvalidated',
            spatial_scope='Indiranagar, Hebbal',
            limitations=['Not verified against Bengaluru BTMC signal inventory',
                         'Number and location are assumed, not observed'],
        ),
        'signal_timing': EvidenceItem(
            'Signal phase timing',
            EvidenceLevel.SYNTHETIC,
            'SUMO defaults: 60s cycle, actuated mode',
            'Neither calibrated from Bengaluru timing plans nor validated.',
            validation_status='unvalidated',
            limitations=['Bengaluru uses SCATS adaptive control at some junctions',
                         'Phase splits unknown'],
        ),
        'demand_matrix': EvidenceItem(
            'Travel demand (OD matrix)',
            EvidenceLevel.SYNTHETIC,
            'Boundary-anchor generation with random vehicle mix',
            'Synthetic directional demand; no observed Bengaluru OD matrix used.',
            validation_status='unvalidated',
            limitations=['Real demand patterns unknown',
                         'No time-of-day variation calibrated'],
        ),
        'vehicle_behaviour': EvidenceItem(
            'Car following and lane change parameters',
            EvidenceLevel.ASSUMED,
            'SUMO Krauss model with default parameters',
            'Standard SUMO parameters; not calibrated to Bengaluru driving.',
            validation_status='unvalidated',
            limitations=['Indian driving behavior differs substantially from defaults',
                         'Two-wheeler filtering not modeled'],
        ),
        'ioc_survey': EvidenceItem(
            'IOC Junction vehicle counts',
            EvidenceLevel.OBSERVED,
            'B-SMILE project report via OpenCity; count date 24 Aug 2021',
            'Classified vehicle counts at one corridor, 06:00-20:00.',
            validation_status='partially_validated',
            spatial_scope='IOC Junction corridor',
            temporal_scope='24 Aug 2021 06:00-20:00',
            limitations=['Single corridor, single day',
                         'Four interval totals printed as zero despite positive counts',
                         'No individual trip times available',
                         'Not used to fit the SUMO experiment'],
        ),
        'background_traffic': EvidenceItem(
            'Background traffic fraction',
            EvidenceLevel.ASSUMED,
            'Planner assumes 15%; SUMO has zero background vehicles',
            'Explicit mismatch between forecast model and execution.',
            validation_status='unvalidated',
            limitations=['No background fleet in SUMO',
                         'Real background unknown'],
        ),
    }


def build_calibration_targets() -> List[CalibrationTarget]:
    """Define what needs calibrating and what evidence would suffice."""
    return [
        CalibrationTarget(
            'Background traffic fraction',
            current_value=0.15,
            current_basis=EvidenceLevel.ASSUMED,
            required_evidence='Observed link flows or OD survey to estimate non-modeled vehicles',
            target_metric='Background fraction within 5% of observed saturation gap',
            notes='Now defaults to 0.0 in code; set >0 only with evidence',
        ),
        CalibrationTarget(
            'Signal timing parameters',
            current_value='60s cycle, SUMO defaults',
            current_basis=EvidenceLevel.SYNTHETIC,
            required_evidence='Bengaluru BTMC signal timing plans or field-measured phase splits',
            target_metric='Green time within 10% of observed at calibrated junctions',
        ),
        CalibrationTarget(
            'Car-following behaviour',
            current_value='Krauss defaults',
            current_basis=EvidenceLevel.ASSUMED,
            required_evidence='Trajectory data or queue discharge rate measurements',
            target_metric='Queue discharge headway within 0.5s of observed mean',
        ),
        CalibrationTarget(
            'Demand pattern',
            current_value='Synthetic boundary anchors',
            current_basis=EvidenceLevel.SYNTHETIC,
            required_evidence='OD survey, ATCC data, or Bluetooth/Wi-Fi re-identification',
            target_metric='Link-flow MAE < 20% against observed counts',
        ),
        CalibrationTarget(
            'Link travel time',
            current_value='Free-flow from OSM speed limits',
            current_basis=EvidenceLevel.ASSUMED,
            required_evidence='Probe vehicle data, Google/TomTom API, or floating car surveys',
            target_metric='MAPE < 15% on validation corridors',
        ),
        CalibrationTarget(
            'ETA prediction (Bengaluru)',
            current_value='5.14 min MAE on Chengdu only',
            current_basis=EvidenceLevel.UNKNOWN,
            required_evidence='Bengaluru trip time observations for training and held-out testing',
            target_metric='10% relative MAE improvement over strong baselines on Bengaluru data',
            notes='Chengdu MAE is not transferable; Bengaluru claims require Bengaluru validation',
        ),
    ]


def save_evidence_report(output_path: str):
    """Write a JSON report of all evidence items and calibration targets."""
    evidence = build_bengaluru_evidence()
    targets = build_calibration_targets()
    report = {
        'evidence_items': {
            k: {
                'name': v.name, 'level': v.level.value, 'source': v.source,
                'description': v.description, 'validation_status': v.validation_status,
                'spatial_scope': v.spatial_scope, 'temporal_scope': v.temporal_scope,
                'limitations': v.limitations,
            } for k, v in evidence.items()
        },
        'calibration_targets': [
            {
                'name': t.name, 'current_value': str(t.current_value),
                'current_basis': t.current_basis.value,
                'required_evidence': t.required_evidence,
                'target_metric': t.target_metric,
                'achieved': t.achieved, 'notes': t.notes,
            } for t in targets
        ],
        'summary': {
            'observed_items': sum(1 for v in evidence.values() if v.level == EvidenceLevel.OBSERVED),
            'assumed_items': sum(1 for v in evidence.values() if v.level in (EvidenceLevel.ASSUMED, EvidenceLevel.SYNTHETIC)),
            'calibrated_targets': sum(1 for t in targets if t.achieved),
            'total_targets': len(targets),
        },
    }
    Path(output_path).write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report
