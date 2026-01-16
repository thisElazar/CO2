#!/usr/bin/env python3
"""
CO2 Experiment Drive Analyzer v1.0
Downloads experiment data from Google Drive, generates analysis graphs,
and optionally uploads PNGs back to Drive.

Uses same OAuth credentials as co2_drive_uploader.py
"""

import os
import sys
import io
import argparse
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

# ===== CONFIGURATION =====
TOKEN_FILE = Path.home() / ".config" / "co2_uploader" / "token.json"
# drive.file: only files created by app; drive: full access to see all files
SCOPES = ['https://www.googleapis.com/auth/drive']
DRIVE_FOLDER_ID = "1176LdK5iW7yMf7wpxuTmtJ_WkAsIxdsd"
LOCAL_CACHE = Path.home() / ".cache" / "co2_analyzer"

# Plot styling
plt.style.use('seaborn-v0_8-darkgrid')
COLORS = {
    'treatment': '#00bfff',
    'control': '#ff6b6b', 
    'delta': '#4caf50',
    'correlation': '#9c27b0'
}


class DriveAnalyzer:
    def __init__(self):
        self.service = None
        LOCAL_CACHE.mkdir(parents=True, exist_ok=True)
    
    def authenticate(self):
        """Authenticate using existing token from uploader."""
        if not TOKEN_FILE.exists():
            print(f"✗ Token not found: {TOKEN_FILE}")
            print("  Run co2_drive_uploader.py first to authenticate.")
            return False
        
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(TOKEN_FILE, 'w') as f:
                    f.write(creds.to_json())
            else:
                print("✗ Token expired. Re-run co2_drive_uploader.py to refresh.")
                return False
        
        self.service = build('drive', 'v3', credentials=creds)
        print("✓ Authenticated with Google Drive")
        return True
    
    def list_subfolders(self, parent_id=None):
        """List subfolders in a folder (non-experiment organizational folders)."""
        if parent_id is None:
            parent_id = DRIVE_FOLDER_ID

        results = self.service.files().list(
            q=f"'{parent_id}' in parents and mimeType='application/vnd.google-apps.folder'",
            fields="files(id, name, createdTime)",
            orderBy="name",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()

        return results.get('files', [])

    def is_experiment_folder(self, folder):
        """Check if a folder is an experiment (has CSV) vs organizational subfolder."""
        csvs = self.get_csv_in_folder(folder['id'])
        return len(csvs) > 0

    def list_experiments(self, recursive=False, subfolder=None):
        """List experiment folders in Drive.

        Args:
            recursive: If True, include experiments in subfolders
            subfolder: If specified, only list experiments in this subfolder

        Returns:
            List of experiment dicts with 'id', 'name', 'createdTime', 'folder_path'
        """
        experiments = []

        # Determine starting folder
        if subfolder:
            # Find the subfolder by name
            subfolders = self.list_subfolders(DRIVE_FOLDER_ID)
            target = next((f for f in subfolders if f['name'].lower() == subfolder.lower()), None)
            if not target:
                print(f"  Subfolder '{subfolder}' not found")
                return []
            folders_to_scan = [(target['id'], target['name'])]
        else:
            folders_to_scan = [(DRIVE_FOLDER_ID, '')]

        while folders_to_scan:
            parent_id, parent_path = folders_to_scan.pop(0)

            # Get all folders in this parent
            results = self.service.files().list(
                q=f"'{parent_id}' in parents and mimeType='application/vnd.google-apps.folder'",
                fields="files(id, name, createdTime)",
                orderBy="createdTime desc",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()

            folders = results.get('files', [])

            for folder in folders:
                folder_path = f"{parent_path}/{folder['name']}" if parent_path else folder['name']

                # Check if this is an experiment (has CSV) or organizational folder
                if self.is_experiment_folder(folder):
                    folder['folder_path'] = parent_path or '(root)'
                    experiments.append(folder)
                elif recursive or subfolder:
                    # It's an organizational folder, scan it if recursive
                    if recursive and not subfolder:
                        folders_to_scan.append((folder['id'], folder['name']))

        # Sort by creation time (newest first)
        experiments.sort(key=lambda x: x.get('createdTime', ''), reverse=True)
        return experiments
    
    def get_png_in_folder(self, folder_id):
        """Check if analysis PNG already exists in folder."""
        results = self.service.files().list(
            q=f"'{folder_id}' in parents and name contains '.png'",
            fields="files(id, name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        return results.get('files', [])
    
    def get_csv_in_folder(self, folder_id):
        """Get CSV files in an experiment folder."""
        results = self.service.files().list(
            q=f"'{folder_id}' in parents and name contains '.csv'",
            fields="files(id, name, size)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        return results.get('files', [])
    
    def download_file(self, file_id, filename):
        """Download a file from Drive."""
        cache_path = LOCAL_CACHE / filename
        
        request = self.service.files().get_media(fileId=file_id, supportsAllDrives=True)
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)
        
        done = False
        while not done:
            _, done = downloader.next_chunk()
        
        buffer.seek(0)
        with open(cache_path, 'wb') as f:
            f.write(buffer.read())
        
        return cache_path
    
    def make_public(self, file_id):
        """Make a file publicly accessible and return direct download URL."""
        try:
            self.service.permissions().create(
                fileId=file_id,
                body={'type': 'anyone', 'role': 'reader'},
                supportsAllDrives=True
            ).execute()
        except Exception as e:
            # Permission may already exist
            pass
        return f"https://drive.google.com/uc?id={file_id}&export=download"
    
    def get_file_in_folder(self, folder_id, extension):
        """Get file with specific extension in folder."""
        results = self.service.files().list(
            q=f"'{folder_id}' in parents and name contains '{extension}'",
            fields="files(id, name, size)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        files = results.get('files', [])
        return files[0] if files else None
    
    def upload_json(self, data, filename):
        """Upload/update JSON index to Drive root folder."""
        import json
        
        # Check if index already exists
        results = self.service.files().list(
            q=f"'{DRIVE_FOLDER_ID}' in parents and name='{filename}'",
            fields="files(id)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        existing = results.get('files', [])
        
        # Write to temp file
        temp_path = LOCAL_CACHE / filename
        with open(temp_path, 'w') as f:
            json.dump(data, f, indent=2)
        
        media = MediaFileUpload(str(temp_path), mimetype='application/json')
        
        if existing:
            # Update existing
            file_id = existing[0]['id']
            self.service.files().update(
                fileId=file_id,
                media_body=media,
                supportsAllDrives=True
            ).execute()
        else:
            # Create new
            metadata = {'name': filename, 'parents': [DRIVE_FOLDER_ID]}
            result = self.service.files().create(
                body=metadata,
                media_body=media,
                supportsAllDrives=True,
                fields='id'
            ).execute()
            file_id = result['id']
        
        # Make public
        url = self.make_public(file_id)
        temp_path.unlink(missing_ok=True)
        return url
    
    def upload_png(self, local_path, folder_id, filename):
        """Upload a PNG to a Drive folder."""
        file_metadata = {
            'name': filename,
            'parents': [folder_id]
        }
        media = MediaFileUpload(str(local_path), mimetype='image/png')
        
        self.service.files().create(
            body=file_metadata,
            media_body=media,
            supportsAllDrives=True,
            fields='id'
        ).execute()
        print(f"  ✓ Uploaded {filename}")


def load_and_parse(csv_path):
    """Load CSV and compute derived fields."""
    df = pd.read_csv(csv_path)
    
    # Standardize column access
    df.columns = df.columns.str.lower().str.strip()
    
    # Time in hours
    if 'elapsed_seconds' in df.columns:
        df['hours'] = df['elapsed_seconds'] / 3600
    elif 'elapsed' in df.columns:
        df['hours'] = df['elapsed'] / 3600
    else:
        df['hours'] = np.arange(len(df)) / 3600
    
    # Ensure we have the key columns
    co2_t = df.get('co2_treatment', df.get('treatment', None))
    co2_c = df.get('co2_control', df.get('control', None))
    
    if co2_t is not None:
        df['co2_treatment'] = co2_t
    if co2_c is not None:
        df['co2_control'] = co2_c
    
    # Delta
    if 'delta_raw' in df.columns:
        df['delta'] = df['delta_raw']
    elif 'delta' in df.columns:
        pass
    elif 'co2_treatment' in df.columns and 'co2_control' in df.columns:
        df['delta'] = df['co2_treatment'] - df['co2_control']
    
    return df


def rolling_correlation(x, y, window):
    """Calculate rolling Pearson correlation."""
    result = []
    for i in range(len(x)):
        if i < window:
            result.append(np.nan)
        else:
            x_win = x.iloc[i-window:i]
            y_win = y.iloc[i-window:i]
            if x_win.std() > 0 and y_win.std() > 0:
                result.append(x_win.corr(y_win))
            else:
                result.append(np.nan)
    return pd.Series(result, index=x.index)


def compute_stats(df):
    """Compute summary statistics."""
    n = len(df)
    first, last = 60, max(0, n - 60)
    
    stats = {
        'samples': n,
        'duration_hrs': df['hours'].iloc[-1] if len(df) > 0 else 0,
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
        stats['delta_min'] = df['delta'].min()
        stats['delta_max'] = df['delta'].max()
    
    if 'co2_treatment' in df.columns and 'co2_control' in df.columns:
        stats['correlation'] = df['co2_treatment'].corr(df['co2_control'])
    
    return stats


def generate_analysis_plot(df, stats, title, output_path):
    """Generate multi-panel analysis figure."""
    has_dual = 'co2_control' in df.columns and df['co2_control'].notna().any()
    
    n_panels = 3 if has_dual else 1
    
    fig, axes = plt.subplots(n_panels, 1, figsize=(14, 3 * n_panels), sharex=True)
    if n_panels == 1:
        axes = [axes]
    
    hours = df['hours']
    ax_idx = 0
    
    # Panel 1: CO2 Overview
    ax = axes[ax_idx]
    window = 300  # 5-min smoothing
    
    if 'co2_treatment' in df.columns:
        ax.plot(hours, df['co2_treatment'], color=COLORS['treatment'], 
                alpha=0.3, linewidth=0.5, label='Treatment (raw)')
        ax.plot(hours, df['co2_treatment'].rolling(window, min_periods=1).mean(),
                color=COLORS['treatment'], linewidth=2, label='Treatment (5-min)')
    
    if has_dual:
        ax.plot(hours, df['co2_control'], color=COLORS['control'],
                alpha=0.3, linewidth=0.5, label='Control (raw)')
        ax.plot(hours, df['co2_control'].rolling(window, min_periods=1).mean(),
                color=COLORS['control'], linewidth=2, label='Control (5-min)')
    
    ax.set_ylabel('CO2 (ppm)')
    ax.legend(loc='upper left', fontsize=8)
    ax.set_title(title, fontsize=12, fontweight='bold')
    ax_idx += 1
    
    # Panel 2: Delta (if dual sensor)
    if has_dual and 'delta' in df.columns:
        ax = axes[ax_idx]
        ax.plot(hours, df['delta'], color=COLORS['delta'], alpha=0.3, linewidth=0.5)
        ax.plot(hours, df['delta'].rolling(window, min_periods=1).mean(),
                color=COLORS['delta'], linewidth=2, label='Delta (5-min)')
        ax.axhline(0, color='gray', linestyle='--', alpha=0.5)
        ax.axhline(stats.get('delta_mean', 0), color='orange', linewidth=1.5,
                   label=f"Mean: {stats.get('delta_mean', 0):.1f}")
        ax.set_ylabel('Delta CO2 (ppm)')
        ax.legend(loc='upper left', fontsize=8)
        ax_idx += 1
    
    # Panel 3: Rolling Correlation (if dual sensor)
    if has_dual:
        ax = axes[ax_idx]
        corr_window = 900  # 15-min
        roll_corr = rolling_correlation(df['co2_treatment'], df['co2_control'], corr_window)
        ax.plot(hours, roll_corr, color=COLORS['correlation'], linewidth=1.5)
        ax.axhline(0.8, color='green', linestyle='--', alpha=0.5, label='Good (0.8)')
        ax.axhline(0, color='red', linestyle='--', alpha=0.5)
        ax.set_ylabel('15-min Rolling R')
        ax.set_ylim(-0.5, 1.05)
        ax.legend(loc='lower left', fontsize=8)
        ax_idx += 1
    
    axes[-1].set_xlabel('Hours')
    
    # Add stats text box
    stats_text = f"Duration: {stats['duration_hrs']:.1f}h | Samples: {stats['samples']:,}"
    if 'correlation' in stats:
        stats_text += f" | R: {stats['correlation']:.3f}"
    if 'delta_mean' in stats:
        stats_text += f" | Delta: {stats['delta_mean']:.1f}+/-{stats['delta_std']:.1f}"
    
    fig.text(0.5, 0.02, stats_text, ha='center', fontsize=9, 
             bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
    
    plt.tight_layout(rect=[0, 0.04, 1, 1])
    plt.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()
    
    return output_path


def generate_dashboard_html(experiments_data, output_path):
    """Generate self-contained HTML dashboard with embedded data."""
    import json
    
    html_template = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CO2 Experiment Dashboard</title>
    <script src="https://cdn.plot.ly/plotly-2.26.0.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0e27;
            color: #e0e0e0;
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        header {
            text-align: center;
            margin-bottom: 25px;
            padding: 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border-radius: 10px;
        }
        h1 { font-size: 2em; margin-bottom: 5px; color: white; }
        .subtitle { font-size: 0.95em; color: #f0f0f0; opacity: 0.9; }
        .controls {
            background: #1a1f3a;
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 20px;
        }
        .control-group label {
            display: block;
            margin-bottom: 6px;
            color: #a0a0a0;
            font-weight: 600;
            text-transform: uppercase;
            font-size: 0.75em;
        }
        select {
            width: 100%;
            max-width: 500px;
            padding: 10px 12px;
            background: #0a0e27;
            border: 2px solid #667eea;
            border-radius: 5px;
            color: #e0e0e0;
            font-size: 0.95em;
            cursor: pointer;
        }
        select:hover { border-color: #764ba2; }
        .stats-row {
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
            padding: 15px 0;
        }
        .stat-badge {
            background: #0a0e27;
            padding: 8px 14px;
            border-radius: 5px;
            font-size: 0.85em;
            border-left: 3px solid #667eea;
        }
        .stat-badge.good { border-left-color: #4caf50; }
        .stat-badge.warn { border-left-color: #ff9800; }
        .stat-label { color: #888; margin-right: 6px; }
        .stat-value { color: #e0e0e0; font-weight: 600; }
        .plot-section {
            background: #1a1f3a;
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 20px;
            display: none;
        }
        .plot-section.visible { display: block; }
        .section-header {
            color: #667eea;
            font-size: 1em;
            margin-bottom: 12px;
            padding-bottom: 8px;
            border-bottom: 2px solid #667eea;
        }
        .plot-container {
            background: #0a0e27;
            border-radius: 8px;
            padding: 10px;
        }
        footer {
            text-align: center;
            margin-top: 30px;
            padding: 15px;
            color: #666;
            font-size: 0.8em;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>CO2 Experiment Dashboard</h1>
            <p class="subtitle">Mormon Slough Restoration Research</p>
        </header>
        <div class="controls">
            <div class="control-group">
                <label>Select Experiment</label>
                <select id="experimentSelect">
                    <option value="">-- Select an experiment --</option>
                </select>
            </div>
            <div id="statsPanel" class="stats-row"></div>
        </div>
        <div class="plot-section" id="overviewSection">
            <h2 class="section-header">CO2 Overview</h2>
            <div class="plot-container" id="overviewPlot"></div>
        </div>
        <div class="plot-section" id="deltaSection">
            <h2 class="section-header">Delta (Treatment - Control)</h2>
            <div class="plot-container" id="deltaPlot"></div>
        </div>
        <div class="plot-section" id="correlationSection">
            <h2 class="section-header">Sensor Correlation</h2>
            <div class="plot-container" id="correlationPlot"></div>
        </div>
        <footer>
            <p>Generated: ''' + datetime.now().strftime('%Y-%m-%d %H:%M') + ''' | ''' + str(len(experiments_data)) + ''' experiments</p>
        </footer>
    </div>
    <script>
        const EXPERIMENTS = ''' + json.dumps(experiments_data) + ''';
        
        const COLORS = { treatment: '#00bfff', control: '#ff6b6b', delta: '#4caf50', correlation: '#9c27b0' };
        const PLOT_LAYOUT = {
            paper_bgcolor: '#0a0e27', plot_bgcolor: '#12182e',
            font: { color: '#e0e0e0', size: 11 },
            xaxis: { gridcolor: '#2a2f4a', title: 'Hours' },
            yaxis: { gridcolor: '#2a2f4a' },
            margin: { l: 55, r: 30, t: 30, b: 50 },
            legend: { orientation: 'h', y: 1.1, x: 0.5, xanchor: 'center' },
            hovermode: 'x unified'
        };

        // Populate dropdown
        const select = document.getElementById('experimentSelect');
        EXPERIMENTS.forEach((exp, i) => {
            const opt = document.createElement('option');
            opt.value = i;
            const stats = [];
            if (exp.duration_hrs) stats.push(exp.duration_hrs + 'h');
            if (exp.correlation) stats.push('R=' + exp.correlation);
            opt.textContent = exp.name + ' (' + (stats.join(', ') || exp.date) + ')';
            select.appendChild(opt);
        });

        select.onchange = () => {
            const idx = select.value;
            if (idx !== '') loadExperiment(EXPERIMENTS[idx]);
        };

        function loadExperiment(exp) {
            showStats(exp);
            const data = parseCSV(exp.csv);
            renderPlots(data, exp);
        }

        function parseCSV(text) {
            const lines = text.trim().split('\\n');
            const headers = lines[0].split(',').map(h => h.trim().toLowerCase());
            const data = { hours: [], co2_treatment: [], co2_control: [], delta: [] };
            const cols = {
                time: headers.findIndex(h => h.includes('elapsed') || h.includes('second')),
                treatment: headers.findIndex(h => h.includes('co2') && h.includes('treatment')),
                control: headers.findIndex(h => h.includes('co2') && h.includes('control')),
                delta: headers.findIndex(h => h.includes('delta'))
            };
            for (let i = 1; i < lines.length; i++) {
                const vals = lines[i].split(',').map(v => parseFloat(v.trim()));
                if (vals.length < 3) continue;
                data.hours.push((cols.time >= 0 ? vals[cols.time] : (i - 1)) / 3600);
                data.co2_treatment.push(cols.treatment >= 0 ? vals[cols.treatment] : null);
                data.co2_control.push(cols.control >= 0 ? vals[cols.control] : null);
                data.delta.push(cols.delta >= 0 ? vals[cols.delta] : null);
            }
            return data;
        }

        function showStats(exp) {
            const panel = document.getElementById('statsPanel');
            panel.innerHTML = '';
            const addStat = (label, value, cls = '') => {
                const badge = document.createElement('div');
                badge.className = 'stat-badge ' + cls;
                badge.innerHTML = '<span class="stat-label">' + label + ':</span><span class="stat-value">' + value + '</span>';
                panel.appendChild(badge);
            };
            addStat('Duration', (exp.duration_hrs || '?') + 'h');
            addStat('Samples', (exp.samples || 0).toLocaleString());
            if (exp.correlation) {
                const cls = exp.correlation > 0.9 ? 'good' : exp.correlation > 0.7 ? 'warn' : '';
                addStat('Correlation', exp.correlation.toFixed(3), cls);
            }
            if (exp.delta_mean !== null) {
                addStat('Delta', (exp.delta_mean > 0 ? '+' : '') + exp.delta_mean + ' +/- ' + exp.delta_std + ' ppm');
            }
        }

        function renderPlots(data, exp) {
            const hours = data.hours;
            const window = 300;

            // Overview
            const overviewTraces = [];
            if (data.co2_treatment[0] !== null) {
                overviewTraces.push({ x: hours, y: data.co2_treatment, mode: 'lines', name: 'Treatment (raw)', line: { color: COLORS.treatment, width: 0.5 }, opacity: 0.3 });
                overviewTraces.push({ x: hours, y: movingAvg(data.co2_treatment, window), mode: 'lines', name: 'Treatment (5-min)', line: { color: COLORS.treatment, width: 2 } });
            }
            if (data.co2_control[0] !== null) {
                overviewTraces.push({ x: hours, y: data.co2_control, mode: 'lines', name: 'Control (raw)', line: { color: COLORS.control, width: 0.5 }, opacity: 0.3 });
                overviewTraces.push({ x: hours, y: movingAvg(data.co2_control, window), mode: 'lines', name: 'Control (5-min)', line: { color: COLORS.control, width: 2 } });
            }
            Plotly.newPlot('overviewPlot', overviewTraces, { ...PLOT_LAYOUT, yaxis: { ...PLOT_LAYOUT.yaxis, title: 'CO2 (ppm)' }, height: 350 });
            document.getElementById('overviewSection').classList.add('visible');

            // Delta
            if (data.delta[0] !== null) {
                const deltaMean = exp.delta_mean || 0;
                Plotly.newPlot('deltaPlot', [
                    { x: hours, y: data.delta, mode: 'lines', name: 'Delta (raw)', line: { color: COLORS.delta, width: 0.5 }, opacity: 0.4 },
                    { x: hours, y: movingAvg(data.delta, window), mode: 'lines', name: 'Delta (5-min)', line: { color: COLORS.delta, width: 2 } },
                    { x: [0, hours[hours.length-1]], y: [0, 0], mode: 'lines', name: 'Zero', line: { color: '#888', dash: 'dash', width: 1 } },
                    { x: [0, hours[hours.length-1]], y: [deltaMean, deltaMean], mode: 'lines', name: 'Mean (' + deltaMean.toFixed(1) + ')', line: { color: '#ff9800', width: 1.5 } }
                ], { ...PLOT_LAYOUT, yaxis: { ...PLOT_LAYOUT.yaxis, title: 'Delta CO2 (ppm)' }, height: 300 });
                document.getElementById('deltaSection').classList.add('visible');
            }

            // Correlation
            if (data.co2_treatment[0] !== null && data.co2_control[0] !== null) {
                const rollCorr = rollingCorrelation(data.co2_treatment, data.co2_control, 900);
                Plotly.newPlot('correlationPlot', [
                    { x: hours, y: rollCorr, mode: 'lines', name: '15-min Rolling R', line: { color: COLORS.correlation, width: 1.5 } },
                    { x: [0, hours[hours.length-1]], y: [0.8, 0.8], mode: 'lines', name: 'Good (0.8)', line: { color: '#4caf50', dash: 'dash', width: 1 } },
                    { x: [0, hours[hours.length-1]], y: [0, 0], mode: 'lines', name: 'Zero', line: { color: '#f44336', dash: 'dash', width: 1 } }
                ], { ...PLOT_LAYOUT, yaxis: { ...PLOT_LAYOUT.yaxis, title: 'Correlation (R)', range: [-0.5, 1.05] }, height: 280 });
                document.getElementById('correlationSection').classList.add('visible');
            }
        }

        function movingAvg(arr, window) {
            const result = [];
            let sum = 0, count = 0;
            for (let i = 0; i < arr.length; i++) {
                if (arr[i] !== null) { sum += arr[i]; count++; }
                if (i >= window && arr[i - window] !== null) { sum -= arr[i - window]; count--; }
                result.push(count > 0 ? sum / count : null);
            }
            return result;
        }

        function rollingCorrelation(x, y, window) {
            const result = [];
            for (let i = 0; i < x.length; i++) {
                if (i < window) { result.push(null); continue; }
                result.push(pearsonR(x.slice(i - window, i), y.slice(i - window, i)));
            }
            return result;
        }

        function pearsonR(x, y) {
            const n = x.length;
            let sumX = 0, sumY = 0, sumXY = 0, sumX2 = 0, sumY2 = 0;
            for (let i = 0; i < n; i++) {
                if (x[i] === null || y[i] === null) continue;
                sumX += x[i]; sumY += y[i]; sumXY += x[i] * y[i];
                sumX2 += x[i] * x[i]; sumY2 += y[i] * y[i];
            }
            const num = n * sumXY - sumX * sumY;
            const den = Math.sqrt((n * sumX2 - sumX * sumX) * (n * sumY2 - sumY * sumY));
            return den === 0 ? 0 : num / den;
        }
    </script>
</body>
</html>'''
    
    with open(output_path, 'w') as f:
        f.write(html_template)


def main():
    parser = argparse.ArgumentParser(description='Analyze CO2 experiment data from Google Drive')
    parser.add_argument('--list', '-l', action='store_true', help='List available experiments')
    parser.add_argument('--analyze', '-a', type=str, help='Analyze specific experiment (name or "latest" or "all")')
    parser.add_argument('--index', '-i', action='store_true', help='Generate public index JSON for web viewer')
    parser.add_argument('--dashboard', '-d', action='store_true', help='Generate self-contained HTML dashboard')
    parser.add_argument('--local', type=str, help='Analyze local CSV file (saves PNG locally)')
    parser.add_argument('--output', '-o', type=str, help='Output directory for --local and --dashboard')
    parser.add_argument('--recursive', '-r', action='store_true', help='Include experiments in subfolders')
    parser.add_argument('--folder', '-f', type=str, help='Target specific subfolder (e.g., "setup", "archive")')
    args = parser.parse_args()
    
    # Local file analysis (no Drive needed)
    if args.local:
        output_dir = Path(args.output) if args.output else Path.cwd()
        output_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"Analyzing local file: {args.local}")
        df = load_and_parse(args.local)
        stats = compute_stats(df)
        
        name = Path(args.local).stem
        out_path = output_dir / f"{name}_analysis.png"
        generate_analysis_plot(df, stats, name, out_path)
        print(f"✓ Generated: {out_path}")
        
        print(f"\nStats: {stats['samples']:,} samples, {stats['duration_hrs']:.1f} hours")
        if 'correlation' in stats:
            print(f"  Correlation: {stats['correlation']:.4f}")
        if 'delta_mean' in stats:
            print(f"  Delta: {stats['delta_mean']:.2f} ± {stats['delta_std']:.2f} ppm")
        return
    
    # Drive operations
    analyzer = DriveAnalyzer()
    if not analyzer.authenticate():
        sys.exit(1)

    # Get experiments with folder filtering
    recursive = getattr(args, 'recursive', False)
    subfolder = getattr(args, 'folder', None)
    experiments = analyzer.list_experiments(recursive=recursive, subfolder=subfolder)

    if args.list or (not args.analyze and not args.index and not args.dashboard):
        # Build header based on mode
        if subfolder:
            header = f"📂 Experiments in '{subfolder}' ({len(experiments)} total):"
        elif recursive:
            header = f"📂 All experiments including subfolders ({len(experiments)} total):"
        else:
            header = f"📂 Experiments in Drive root ({len(experiments)} total):"
        print(f"\n{header}")

        # Group by folder if recursive
        if recursive and not subfolder:
            from collections import defaultdict
            by_folder = defaultdict(list)
            for exp in experiments:
                by_folder[exp.get('folder_path', '(root)')].append(exp)

            for folder_path in sorted(by_folder.keys()):
                folder_exps = by_folder[folder_path]
                print(f"\n  [{folder_path}] ({len(folder_exps)} experiments)")
                for exp in folder_exps[:10]:
                    created = exp.get('createdTime', '')[:10]
                    print(f"    • {exp['name']}  ({created})")
                if len(folder_exps) > 10:
                    print(f"    ... and {len(folder_exps) - 10} more")
        else:
            for i, exp in enumerate(experiments[:20], 1):
                created = exp.get('createdTime', '')[:10]
                folder_info = f" [{exp.get('folder_path', '')}]" if subfolder else ""
                print(f"  {i:2}. {exp['name']}{folder_info}  ({created})")
            if len(experiments) > 20:
                print(f"  ... and {len(experiments) - 20} more")

        # Show available subfolders if not already filtered
        if not subfolder and not recursive:
            subfolders = analyzer.list_subfolders(DRIVE_FOLDER_ID)
            # Filter to only show organizational folders (those without CSVs)
            org_folders = [f for f in subfolders if not analyzer.is_experiment_folder(f)]
            if org_folders:
                print(f"\n  Subfolders: {', '.join(f['name'] for f in org_folders)}")
                print("  Use --folder <name> to view, or --recursive to include all")

        if not args.analyze and not args.index and not args.dashboard:
            print("\nUsage: --analyze <name|latest|all> to generate graphs")
            print("       --dashboard to generate self-contained HTML viewer")
            print("       --recursive to include experiments in subfolders")
        return
    
    # Generate public index
    if args.index:
        print(f"\n📋 Generating public index for {len(experiments)} experiments...")
        
        index_data = {
            'updated': datetime.now().isoformat(),
            'experiments': []
        }
        
        for exp in experiments:
            print(f"  → {exp['name']}", end='', flush=True)
            
            # Get CSV and PNG files
            csv_file = analyzer.get_file_in_folder(exp['id'], '.csv')
            png_file = analyzer.get_file_in_folder(exp['id'], '.png')
            
            if not csv_file:
                print(" (no CSV, skipping)")
                continue
            
            # Make files public and get URLs
            csv_url = analyzer.make_public(csv_file['id'])
            png_url = analyzer.make_public(png_file['id']) if png_file else None
            
            # Quick stats from CSV (download, parse, delete)
            try:
                local_csv = analyzer.download_file(csv_file['id'], f"temp_{exp['name']}.csv")
                df = load_and_parse(local_csv)
                stats = compute_stats(df)
                local_csv.unlink(missing_ok=True)
                
                exp_entry = {
                    'name': exp['name'],
                    'date': exp.get('createdTime', '')[:10],
                    'csv_url': csv_url,
                    'png_url': png_url,
                    'samples': stats['samples'],
                    'duration_hrs': round(stats['duration_hrs'], 1),
                    'correlation': round(stats.get('correlation', 0), 3) if stats.get('correlation') else None,
                    'delta_mean': round(stats.get('delta_mean', 0), 1) if stats.get('delta_mean') is not None else None
                }
                index_data['experiments'].append(exp_entry)
                print(f" ✓ ({stats['duration_hrs']:.1f}h)")
            except Exception as e:
                print(f" ✗ ({e})")
                continue
        
        # Upload index
        print(f"\n📤 Uploading index ({len(index_data['experiments'])} experiments)...")
        index_url = analyzer.upload_json(index_data, 'experiments_index.json')
        print(f"✓ Index URL: {index_url}")
        print("\nUse this URL in your HTML viewer.")
        return
    
    # Generate self-contained dashboard
    if args.dashboard:
        print(f"\n📊 Generating dashboard for {len(experiments)} experiments...")
        
        output_dir = Path(args.output) if args.output else Path.cwd()
        output_dir.mkdir(parents=True, exist_ok=True)
        
        experiments_data = []
        
        for exp in experiments:
            print(f"  → {exp['name']}", end='', flush=True)
            
            csv_file = analyzer.get_file_in_folder(exp['id'], '.csv')
            if not csv_file:
                print(" (no CSV, skipping)")
                continue
            
            try:
                local_csv = analyzer.download_file(csv_file['id'], f"temp_{exp['name']}.csv")
                
                # Read raw CSV content
                with open(local_csv, 'r') as f:
                    csv_content = f.read()
                
                df = load_and_parse(local_csv)
                stats = compute_stats(df)
                local_csv.unlink(missing_ok=True)
                
                experiments_data.append({
                    'name': exp['name'],
                    'date': exp.get('createdTime', '')[:10],
                    'csv': csv_content,
                    'samples': stats['samples'],
                    'duration_hrs': round(stats['duration_hrs'], 1),
                    'correlation': round(stats.get('correlation', 0), 3) if stats.get('correlation') else None,
                    'delta_mean': round(stats.get('delta_mean', 0), 1) if stats.get('delta_mean') is not None else None,
                    'delta_std': round(stats.get('delta_std', 0), 1) if stats.get('delta_std') is not None else None
                })
                print(f" ✓ ({stats['duration_hrs']:.1f}h)")
            except Exception as e:
                print(f" ✗ ({e})")
                continue
        
        # Generate HTML
        html_path = output_dir / 'co2_dashboard.html'
        generate_dashboard_html(experiments_data, html_path)
        print(f"\n✓ Dashboard saved: {html_path}")
        return
    
    # Determine which experiments to analyze
    targets = []
    if args.analyze == 'latest':
        targets = [experiments[0]] if experiments else []
    elif args.analyze == 'all':
        targets = experiments
    else:
        # Find by name (partial match)
        targets = [e for e in experiments if args.analyze.lower() in e['name'].lower()]
    
    if not targets:
        print(f"✗ No experiments found matching '{args.analyze}'")
        return
    
    print(f"\n📊 Analyzing {len(targets)} experiment(s)...")
    
    for exp in targets:
        print(f"\n→ {exp['name']}")
        
        # Check if PNG already exists
        existing_pngs = analyzer.get_png_in_folder(exp['id'])
        if existing_pngs:
            print(f"  ⏭ Skipping (PNG already exists: {existing_pngs[0]['name']})")
            continue
        
        # Find CSV in experiment folder
        csvs = analyzer.get_csv_in_folder(exp['id'])
        if not csvs:
            print("  ⚠ No CSV found, skipping")
            continue
        
        csv_file = csvs[0]  # Take first CSV
        print(f"  Downloading {csv_file['name']}...")
        
        local_csv = analyzer.download_file(csv_file['id'], f"{exp['name']}.csv")
        
        # Analyze
        df = load_and_parse(local_csv)
        stats = compute_stats(df)
        
        # Generate to temp location
        out_path = LOCAL_CACHE / f"{exp['name']}_analysis.png"
        generate_analysis_plot(df, stats, exp['name'], out_path)
        
        # Print key stats
        print(f"    {stats['samples']:,} samples, {stats['duration_hrs']:.1f}h", end='')
        if 'correlation' in stats:
            print(f", R={stats['correlation']:.3f}", end='')
        if 'delta_mean' in stats:
            print(f", Δ={stats['delta_mean']:.1f}±{stats['delta_std']:.1f}", end='')
        print()
        
        # Upload PNG to Drive
        analyzer.upload_png(out_path, exp['id'], f"{exp['name']}_analysis.png")
        
        # Clean up temp files
        local_csv.unlink(missing_ok=True)
        out_path.unlink(missing_ok=True)
    
    print(f"\n✓ Done.")


if __name__ == "__main__":
    main()
