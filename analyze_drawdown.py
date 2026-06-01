#!/usr/bin/env python3
"""Quick script to download all experiment CSVs from Drive and rank by CO2 drawdown."""

import sys
sys.path.insert(0, '.')
from co2_drive_analyzer import DriveAnalyzer, load_and_parse, compute_stats, LOCAL_CACHE
import json

analyzer = DriveAnalyzer()
if not analyzer.authenticate():
    sys.exit(1)

# List all experiments recursively
print("\nScanning Drive for experiments...")
experiments = analyzer.list_experiments(recursive=True)
print(f"Found {len(experiments)} experiments\n")

results = []

for exp in experiments:
    name = exp['name']
    folder_path = exp.get('folder_path', '(root)')
    csvs = analyzer.get_csv_in_folder(exp['id'])
    if not csvs:
        continue

    csv_file = csvs[0]
    cache_name = f"{name}_{csv_file['name']}"

    try:
        csv_path = analyzer.download_file(csv_file['id'], cache_name)
        df = load_and_parse(csv_path)
        stats = compute_stats(df)

        # Compute drawdown metrics
        has_treatment = 'co2_treatment' in df.columns
        has_control = 'co2_control' in df.columns and df['co2_control'].notna().any()

        entry = {
            'name': name,
            'folder': folder_path,
            'samples': stats['samples'],
            'duration_hrs': round(stats.get('duration_hrs', 0), 2),
        }

        if has_treatment:
            t_start = stats.get('treatment_start', 0)
            t_end = stats.get('treatment_end', 0)
            t_max = df['co2_treatment'].max()
            t_min = df['co2_treatment'].min()
            entry['treatment_start'] = round(t_start, 1)
            entry['treatment_end'] = round(t_end, 1)
            entry['treatment_max'] = round(t_max, 1)
            entry['treatment_min'] = round(t_min, 1)
            entry['treatment_drawdown'] = round(t_start - t_end, 1)
            entry['treatment_range'] = round(t_max - t_min, 1)

        if has_control:
            c_start = stats.get('control_start', 0)
            c_end = stats.get('control_end', 0)
            entry['control_start'] = round(c_start, 1)
            entry['control_end'] = round(c_end, 1)
            entry['control_drawdown'] = round(c_start - c_end, 1)

        if has_treatment and has_control:
            # Net drawdown = treatment drawdown minus control drawdown
            entry['net_drawdown'] = round(
                entry['treatment_drawdown'] - entry.get('control_drawdown', 0), 1
            )
            entry['correlation'] = round(stats.get('correlation', 0), 3)

        results.append(entry)
        print(f"  {folder_path}/{name}: treatment {entry.get('treatment_start','?')} -> {entry.get('treatment_end','?')} ppm  (drawdown: {entry.get('treatment_drawdown','?')} ppm)")

    except Exception as e:
        print(f"  {name}: ERROR - {e}")

# Sort by treatment drawdown (largest first)
results.sort(key=lambda x: x.get('treatment_drawdown', 0), reverse=True)

print("\n" + "="*80)
print("RANKING BY CO2 DRAWDOWN (treatment sensor, start-to-end)")
print("="*80)
for i, r in enumerate(results, 1):
    net = f"  (net vs control: {r['net_drawdown']} ppm)" if 'net_drawdown' in r else ""
    print(f"  {i}. {r['folder']}/{r['name']}: {r.get('treatment_drawdown','?')} ppm over {r['duration_hrs']}h{net}")

# Save results
output = LOCAL_CACHE / "drawdown_ranking.json"
with open(output, 'w') as f:
    json.dump(results, f, indent=2)
print(f"\nFull results saved to {output}")
