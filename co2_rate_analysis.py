#!/usr/bin/env python3
"""
CO2 Rate Analysis Prototype
Generates 3-panel plot:
  1. Raw CO2 vs time (treatment + control)
  2. dCO2/dt vs time (rate of change)
  3. dCO2/dt vs CO2 concentration (scatter)

Usage: python co2_rate_analysis.py <csv_file> [--output <png_file>]
"""

import argparse
from pathlib import Path
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# Styling
plt.style.use('seaborn-v0_8-darkgrid')
COLORS = {
    'treatment': '#00bfff',
    'control': '#ff6b6b',
}
SMOOTH_WINDOW = 20  # seconds for derivative smoothing


def load_data(csv_path):
    """Load and prepare CSV data."""
    df = pd.read_csv(csv_path)
    df.columns = df.columns.str.lower().str.strip()

    # Time in hours for x-axis
    if 'elapsed_seconds' in df.columns:
        df['hours'] = df['elapsed_seconds'] / 3600
        df['seconds'] = df['elapsed_seconds']
    else:
        df['seconds'] = np.arange(len(df))
        df['hours'] = df['seconds'] / 3600

    return df


def compute_rate(series, window=SMOOTH_WINDOW):
    """
    Compute rate of change (dCO2/dt) with smoothing.

    Method:
    1. Apply rolling mean to smooth the signal
    2. Compute derivative using central difference

    Returns rate in ppm/second.
    """
    # Smooth first to reduce noise
    smoothed = series.rolling(window, center=True, min_periods=1).mean()

    # Central difference for derivative (more accurate than forward diff)
    # rate[i] = (smoothed[i+1] - smoothed[i-1]) / 2
    rate = (smoothed.shift(-1) - smoothed.shift(1)) / 2

    return rate


def generate_plot(df, title, output_path):
    """Generate 3-panel analysis figure."""

    fig, axes = plt.subplots(3, 1, figsize=(14, 10))

    hours = df['hours']

    # Check which columns we have
    has_treatment = 'co2_treatment' in df.columns
    has_control = 'co2_control' in df.columns

    # Compute rates
    if has_treatment:
        rate_treatment = compute_rate(df['co2_treatment'])
    if has_control:
        rate_control = compute_rate(df['co2_control'])

    # ===== Panel 1: Raw CO2 vs Time =====
    ax = axes[0]
    if has_treatment:
        ax.plot(hours, df['co2_treatment'], color=COLORS['treatment'],
                alpha=0.7, linewidth=0.8, label='Treatment')
    if has_control:
        ax.plot(hours, df['co2_control'], color=COLORS['control'],
                alpha=0.7, linewidth=0.8, label='Control')

    ax.set_ylabel('CO2 (ppm)')
    ax.set_title(title, fontsize=12, fontweight='bold')
    ax.legend(loc='upper right', fontsize=9)
    ax.set_xlabel('')

    # ===== Panel 2: Rate vs Time =====
    ax = axes[1]
    if has_treatment:
        ax.plot(hours, rate_treatment, color=COLORS['treatment'],
                alpha=0.7, linewidth=0.8, label='Treatment dCO2/dt')
    if has_control:
        ax.plot(hours, rate_control, color=COLORS['control'],
                alpha=0.7, linewidth=0.8, label='Control dCO2/dt')

    ax.axhline(0, color='gray', linestyle='--', alpha=0.5)
    ax.set_ylabel('Rate (ppm/sec)')
    ax.set_title(f'Rate of Change (dCO2/dt) - {SMOOTH_WINDOW}s smoothing', fontsize=10)
    ax.legend(loc='upper right', fontsize=9)
    ax.set_xlabel('')

    # ===== Panel 3: Rate vs Concentration (Scatter) =====
    ax = axes[2]

    # Downsample for scatter if dataset is large (every 10th point)
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

    # Layout
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()

    print(f"Saved: {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(description='CO2 Rate Analysis')
    parser.add_argument('csv_file', help='Input CSV file')
    parser.add_argument('--output', '-o', help='Output PNG file (default: <input>_rate_analysis.png)')
    args = parser.parse_args()

    csv_path = Path(args.csv_file)
    if not csv_path.exists():
        print(f"Error: File not found: {csv_path}")
        return

    # Default output name
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = csv_path.with_name(f"{csv_path.stem}_rate_analysis.png")

    print(f"Loading: {csv_path}")
    df = load_data(csv_path)
    print(f"  {len(df):,} samples, {df['hours'].iloc[-1]:.2f} hours")

    title = csv_path.stem
    generate_plot(df, title, output_path)


if __name__ == "__main__":
    main()
