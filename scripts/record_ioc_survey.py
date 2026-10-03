"""Record visually checked aggregate counts; retain source-table quality flags.

This is an explicit transcription, not a road-network calibration. Original
PDF pages 130 and 133 (printed pages 122 and 125) were visually inspected.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = json.loads((ROOT/'research/references/bsmile_source.json').read_text())
    columns = ['standard_bus', 'mini_bus', 'car_jeep', 'pickup_maxicab', 'two_wheeler',
               'passenger_auto', 'two_axle_truck', 'three_axle_truck', 'multi_axle_vehicle',
               'light_commercial_vehicle', 'goods_auto', 'tractor_trailer', 'bicycle',
               'hand_animal_drawn', 'other']
    counts = [
        [242, 32, 4825, 615, 7135, 1364, 141, 29, 8, 546, 151, 34, 193, 2, 4],
        [273, 103, 4374, 444, 6696, 1615, 247, 32, 8, 373, 304, 41, 270, 3, 1],
    ]
    assert [sum(row) for row in counts] == [15321, 14784]
    directions = ['Maruthi Sevanagar to Banaswadi', 'Banaswadi to Maruthi Sevanagar']
    records = [{'direction': direction, 'pdf_page': page, 'printed_page': page-8,
                'class_counts_14h': dict(zip(columns, row)), 'reported_total_14h': sum(row)}
               for direction, page, row in zip(directions, [130, 133], counts)]
    combined = {c: sum(row[i] for row in counts) for i,c in enumerate(columns)}
    result = {'source': source, 'publisher': 'Bengaluru Smart Infrastructure Limited (B-SMILE)',
        'consultant': 'NEZ Infratech Private Limited', 'report_date': '2025-09',
        'report_title': 'Elevated Rotary Flyover at IOC Junction and additional 2 Lane ROB at Baiyyappanahalli Railway Level Crossing: Detailed Project Report, Volume I',
        'survey_date': '2021-08-24', 'local_time_start': '06:00', 'local_time_end': '20:00',
        'location': 'IOC Junction corridor; Indian Oil Bus Stop and Canera Bank ATM as printed',
        'records': records, 'combined_class_counts_14h': combined, 'combined_total_14h': sum(combined.values()),
        'quality_flags': [
            'On PDF page 133 the first four 15-minute total cells are printed as zero despite nonzero class counts. Their class sums are 62, 75, 73 and 75.',
            'Blank cells and split digits make automated extraction unreliable; only visually checked aggregate rows are transcribed here.',
            'Aggregate class counts sum to each printed direction total; this arithmetic check does not establish survey accuracy.',
            'Survey is from August 2021, not the September 2025 report date; temporal representativeness is unknown.',
            'Single corridor and one day; no individual trajectories, travel times, route intentions, signal timings or current clearance measurements.',
            'Not used to tune or validate the fixed SUMO comparison; no claim of citywide observed demand.'
        ], 'status': 'Aggregate transcription checked; unsuitable as standalone citywide calibration ground truth.'}
    out = ROOT/'data/public/ioc_2021'; out.mkdir(parents=True, exist_ok=True)
    (out/'survey.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'total_14h': result['combined_total_14h'], 'status': result['status']}))


if __name__ == '__main__':
    main()
