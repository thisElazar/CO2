#!/usr/bin/env python3
"""
CO2 Experiment Analyzer v2.0
Downloads experiment data from WandR server and generates analysis plots.
Replaces the Google Drive-based analyzer.
"""

import sys
import json
import argparse
import urllib.request
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

WANDR_URL = "https://wandr.hatchworkshop.org"
LOCAL_CACHE = Path.home() / ".cache" / "co2_analyzer"

plt.style.use('seaborn-v0_8-darkgrid')
COLORS = {
    'treatment': '#00bfff',
    'control': '#ff6b6b',
    'delta': '#4caf50',
    'rate': '#ff9800',
}
SMOOTH_WINDOW = 20


def api_get(path):
    req = urllib.request.Request(
        f"{WANDR_URL}{path}",
        headers={"User-Agent": "co2-analyzer/2.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode()


def list_experiments():
    data = json.loads(api_get("/api/science/experiments"))
    return data.get("experiments", [])


def get_csv(experiment_id):
    return api_get(f"/api/science/experiments/{experiment_id}/data")


def classify(name):
    n = name.lower()
    if 'throwaway' in n:
        return 'throwaway'
    if 'control' in n:
        return 'control'
    if any(x in n for x in ['test', 'photosynthesis', 'breath', 'varied']):
        return 'test'
    return 'unknown'


def load_and_parse(csv_text):
    from io import StringIO
    df = pd.read_csv(StringIO(csv_text) if isinstance(csv_text, str) else csv_text)
    df.columns = df.columns.str.lower().str.strip()

    if 'elapsed_seconds' in df.columns:
        df['minutes'] = df['elapsed_seconds'] / 60
    elif 'elapsed' in df.columns:
        df['minutes'] = df['elapsed'] / 60
    else:
        df['minutes'] = np.arange(len(df)) / 60

    co2_t = df.get('co2_treatment', df.get('treatment', None))
    co2_c = df.get('co2_control', df.get('control', None))
    if co2_t is not None:
        df['co2_treatment'] = co2_t
    if co2_c is not None:
        df['co2_control'] = co2_c

    if 'delta_raw' in df.columns:
        df['delta'] = df['delta_raw']
    elif 'delta' not in df.columns and 'co2_treatment' in df.columns and 'co2_control' in df.columns:
        df['delta'] = df['co2_treatment'] - df['co2_control']

    return df


def compute_rate(series, window=SMOOTH_WINDOW):
    smoothed = series.rolling(window, center=True, min_periods=1).mean()
    return (smoothed.shift(-1) - smoothed.shift(1)) / 2


def compute_stats(df):
    n = len(df)
    first, last = 60, max(0, n - 60)
    stats = {
        'samples': n,
        'duration_mins': df['minutes'].iloc[-1] if n > 0 else 0,
    }
    if 'co2_treatment' in df.columns:
        stats['treatment_start'] = df['co2_treatment'].iloc[:first].mean()
        stats['treatment_end'] = df['co2_treatment'].iloc[last:].mean()
    if 'co2_control' in df.columns:
        stats['control_start'] = df['co2_control'].iloc[:first].mean()
        stats['control_end'] = df['co2_control'].iloc[last:].mean()
    if 'delta' in df.columns:
        stats['delta_mean'] = df['delta'].mean()
        stats['delta_std'] = df['delta'].std()
    if 'co2_treatment' in df.columns and 'co2_control' in df.columns:
        stats['correlation'] = df['co2_treatment'].corr(df['co2_control'])
    return stats


def generate_plot(df, stats, title, output_path):
    fig, axes = plt.subplots(3, 1, figsize=(14, 10))
    minutes = df['minutes']
    has_treatment = 'co2_treatment' in df.columns
    has_control = 'co2_control' in df.columns and df['co2_control'].notna().any()

    if has_treatment:
        rate_treatment = compute_rate(df['co2_treatment'])
    if has_control:
        rate_control = compute_rate(df['co2_control'])

    ax = axes[0]
    if has_treatment:
        ax.plot(minutes, df['co2_treatment'], color=COLORS['treatment'],
                alpha=0.7, linewidth=0.8, label='Treatment')
    if has_control:
        ax.plot(minutes, df['co2_control'], color=COLORS['control'],
                alpha=0.7, linewidth=0.8, label='Control')
    ax.set_ylabel('CO2 (ppm)')
    ax.set_title(title, fontsize=12, fontweight='bold')
    ax.legend(loc='upper right', fontsize=9)

    ax = axes[1]
    if has_treatment:
        ax.plot(minutes, rate_treatment, color=COLORS['treatment'],
                alpha=0.7, linewidth=0.8, label='Treatment dCO2/dt')
    if has_control:
        ax.plot(minutes, rate_control, color=COLORS['control'],
                alpha=0.7, linewidth=0.8, label='Control dCO2/dt')
    ax.axhline(0, color='gray', linestyle='--', alpha=0.5)
    ax.set_ylabel('Rate (ppm/sec)')
    ax.set_title(f'Rate of Change (dCO2/dt) - {SMOOTH_WINDOW}s smoothing', fontsize=10)
    ax.legend(loc='upper right', fontsize=9)

    ax = axes[2]
    step = max(1, len(df) // 5000)
    if has_treatment:
        ax.scatter(df['co2_treatment'].iloc[::step], rate_treatment.iloc[::step],
                   c=COLORS['treatment'], alpha=0.3, s=8, label='Treatment')
    if has_control:
        ax.scatter(df['co2_control'].iloc[::step], rate_control.iloc[::step],
                   c=COLORS['control'], alpha=0.3, s=8, label='Control')
    ax.axhline(0, color='gray', linestyle='--', alpha=0.5)
    ax.set_xlabel('CO2 Concentration (ppm)')
    ax.set_ylabel('Rate (ppm/sec)')
    ax.set_title('Rate vs Concentration', fontsize=10)
    ax.legend(loc='upper right', fontsize=9)

    for a in axes:
        a.set_xlabel('Minutes')

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()
    return output_path


def main():
    parser = argparse.ArgumentParser(description='Analyze CO2 experiments from WandR')
    parser.add_argument('--list', '-l', action='store_true', help='List experiments')
    parser.add_argument('--analyze', '-a', type=str, help='Analyze experiment (name, "latest", or "all")')
    parser.add_argument('--local', type=str, help='Analyze a local CSV file')
    parser.add_argument('--output', '-o', type=str, help='Output directory')
    parser.add_argument('--filter', '-f', type=str, choices=['test', 'control', 'throwaway', 'all'],
                        default='all', help='Filter by type')
    args = parser.parse_args()

    LOCAL_CACHE.mkdir(parents=True, exist_ok=True)

    if args.local:
        output_dir = Path(args.output) if args.output else Path.cwd()
        output_dir.mkdir(parents=True, exist_ok=True)
        df = load_and_parse(args.local)
        stats = compute_stats(df)
        name = Path(args.local).stem
        out_path = output_dir / f"{name}_analysis.png"
        generate_plot(df, stats, name, out_path)
        print(f"Generated: {out_path}")
        print(f"  {stats['samples']:,} samples, {stats['duration_mins']:.1f}m")
        if 'correlation' in stats:
            print(f"  Correlation: {stats['correlation']:.4f}")
        if 'delta_mean' in stats:
            print(f"  Delta: {stats['delta_mean']:.2f} +/- {stats['delta_std']:.2f} ppm")
        return

    print(f"Fetching experiments from {WANDR_URL}...")
    experiments = list_experiments()

    if args.filter != 'all':
        experiments = [e for e in experiments if classify(e['id']) == args.filter]

    if args.list or not args.analyze:
        print(f"\n{len(experiments)} experiments:")
        for i, exp in enumerate(experiments[:30], 1):
            q = exp.get('quality') or {}
            score = q.get('score', '?')
            typ = classify(exp['id'])
            name = exp.get('annotations', {}).get('name') if exp.get('annotations') else None
            label = name or exp['id']
            print(f"  {i:3}. [{score}/5] {label}  ({typ})")
        if len(experiments) > 30:
            print(f"  ... and {len(experiments) - 30} more")
        if not args.analyze:
            print(f"\nUsage: --analyze <name|latest|all>")
        return

    targets = []
    if args.analyze == 'latest':
        targets = [experiments[0]] if experiments else []
    elif args.analyze == 'all':
        targets = experiments
    else:
        targets = [e for e in experiments if args.analyze.lower() in e['id'].lower()]

    if not targets:
        print(f"No experiments matching '{args.analyze}'")
        return

    output_dir = Path(args.output) if args.output else LOCAL_CACHE
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Analyzing {len(targets)} experiment(s)...\n")
    for exp in targets:
        print(f"  {exp['id']}", end='', flush=True)
        try:
            csv_text = get_csv(exp['id'])
            df = load_and_parse(csv_text)
            stats = compute_stats(df)
            out_path = output_dir / f"{exp['id']}_analysis.png"
            generate_plot(df, stats, exp['id'], out_path)
            print(f"  {stats['samples']:,} samples, {stats['duration_mins']:.1f}m", end='')
            if 'correlation' in stats:
                print(f", R={stats['correlation']:.3f}", end='')
            print(f" -> {out_path}")
        except Exception as e:
            print(f"  ERROR: {e}")

    print(f"\nDone.")


if __name__ == "__main__":
    main()
