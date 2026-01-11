#!/usr/bin/env python3
"""
CO2 Experiment Google Drive Uploader v2.1
Monitors experiment folders and automatically uploads to Google Drive when WiFi is available.

IMPROVEMENTS:
- Filename-agnostic: Works with any CSV filename
- Time-based completion: Uploads folders that haven't been modified in 2 minutes
- Better logging: Shows what it's checking and why
- Unbuffered output: Works properly with systemd journalctl
- LED feedback: Creates flag file during upload for blue LED fast blink
"""

import os
import sys
import time
import json
import socket
from pathlib import Path
from datetime import datetime
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

# Force unbuffered output for systemd
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# ===== CONFIGURATION =====
EXPERIMENTS_DIR = Path("/home/thiselazar/Documents/co2_experiments")
UPLOAD_TRACKER_FILE = EXPERIMENTS_DIR / ".upload_tracker.json"
CREDENTIALS_FILE = Path.home() / ".config" / "co2_uploader" / "credentials.json"
TOKEN_FILE = Path.home() / ".config" / "co2_uploader" / "token.json"

# LED feedback flag - signals GPIO monitor that upload is in progress
UPLOAD_FLAG = Path('/tmp/upload_in_progress')

# Google Drive API scope
SCOPES = ['https://www.googleapis.com/auth/drive.file']

# Upload settings
CHECK_INTERVAL = 60  # seconds between checks for new experiments
WIFI_CHECK_INTERVAL = 300  # seconds between WiFi availability checks
COMPLETION_WAIT = 720  # seconds of no changes before considering experiment complete

# Target folder ID - your existing Google Drive folder
USE_EXISTING_FOLDER_ID = "1176LdK5iW7yMf7wpxuTmtJ_WkAsIxdsd"

# ===== UTILITIES =====
def has_internet():
    """Check if WiFi/internet is available."""
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3)
        return True
    except OSError:
        return False

def load_upload_tracker():
    """Load the list of already-uploaded experiments."""
    if UPLOAD_TRACKER_FILE.exists():
        with open(UPLOAD_TRACKER_FILE, 'r') as f:
            return json.load(f)
    return {"uploaded_folders": [], "drive_folder_id": USE_EXISTING_FOLDER_ID}

def save_upload_tracker(tracker):
    """Save the upload tracker."""
    with open(UPLOAD_TRACKER_FILE, 'w') as f:
        json.dump(tracker, f, indent=2)

def get_folder_age(folder_path):
    """Get seconds since folder was last modified."""
    try:
        # Get most recent modification time of folder or any file in it
        folder_mtime = os.path.getmtime(folder_path)
        
        # Check all files too
        max_mtime = folder_mtime
        for item in folder_path.iterdir():
            if item.is_file():
                item_mtime = os.path.getmtime(item)
                max_mtime = max(max_mtime, item_mtime)
        
        return time.time() - max_mtime
    except Exception as e:
        print(f"  ⚠ Error checking folder age: {e}")
        return 0

def is_experiment_complete(folder_path):
    """
    Check if an experiment is complete and ready to upload.
    
    Criteria:
    - Has at least one .csv file (any name)
    - Hasn't been modified in COMPLETION_WAIT seconds
    """
    # Check for CSV file
    csv_files = list(folder_path.glob("*.csv"))
    if not csv_files:
        return False, "No CSV file found"
    
    # Check age
    age = get_folder_age(folder_path)
    if age < COMPLETION_WAIT:
        mins_old = int(age / 60)
        secs_old = int(age % 60)
        return False, f"Still active ({mins_old}m {secs_old}s old, need {COMPLETION_WAIT//60}m)"
    
    return True, f"Complete ({int(age/60)}m old, has {len(csv_files)} CSV file(s))"

# ===== GOOGLE DRIVE =====
class DriveUploader:
    def __init__(self):
        self.service = None
        self.tracker = load_upload_tracker()
    
    def authenticate(self):
        """Authenticate with Google Drive API."""
        creds = None
        
        # Token file stores user's access and refresh tokens
        if TOKEN_FILE.exists():
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        
        # If there are no valid credentials, let the user log in
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not CREDENTIALS_FILE.exists():
                    print(f"❌ Credentials file not found: {CREDENTIALS_FILE}")
                    print("   Please set up Google Drive API credentials first.")
                    return False
                
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(CREDENTIALS_FILE), SCOPES
                )
                creds = flow.run_local_server(port=0)
            
            # Save credentials for next run
            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(TOKEN_FILE, 'w') as token:
                token.write(creds.to_json())
        
        try:
            self.service = build('drive', 'v3', credentials=creds)
            print("✓ Authenticated with Google Drive")
            return True
        except Exception as e:
            print(f"❌ Authentication failed: {e}")
            return False
    
    def upload_experiment(self, experiment_path):
        """Upload a complete experiment folder to Google Drive."""
        folder_name = experiment_path.name
        
        if folder_name in self.tracker['uploaded_folders']:
            return True  # Already uploaded
        
        print(f"\n📤 Uploading: {folder_name}")
        
        # Create flag file for LED feedback (blue LED fast blink)
        UPLOAD_FLAG.touch()
        
        try:
            parent_folder_id = USE_EXISTING_FOLDER_ID
            
            # Create subfolder for this experiment
            folder_metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [parent_folder_id]
            }
            
            experiment_folder = self.service.files().create(
                body=folder_metadata,
                supportsAllDrives=True,
                fields='id'
            ).execute()
            experiment_folder_id = experiment_folder.get('id')
            
            # Upload all files in the experiment folder
            uploaded_count = 0
            for file_path in experiment_path.iterdir():
                if file_path.is_file():
                    file_metadata = {
                        'name': file_path.name,
                        'parents': [experiment_folder_id]
                    }
                    
                    media = MediaFileUpload(
                        str(file_path),
                        resumable=True
                    )
                    
                    self.service.files().create(
                        body=file_metadata,
                        media_body=media,
                        supportsAllDrives=True,
                        fields='id'
                    ).execute()
                    
                    uploaded_count += 1
                    print(f"  ✓ {file_path.name}")
            
            # Mark as uploaded
            self.tracker['uploaded_folders'].append(folder_name)
            save_upload_tracker(self.tracker)
            
            print(f"✓ Uploaded {uploaded_count} files from {folder_name}")
            return True
        
        except HttpError as e:
            print(f"❌ Upload failed for {folder_name}: {e}")
            return False
        
        finally:
            # Always remove flag file when upload completes (success or failure)
            if UPLOAD_FLAG.exists():
                UPLOAD_FLAG.unlink()
    
    def scan_and_upload(self):
        """Scan for new experiments and upload them."""
        if not EXPERIMENTS_DIR.exists():
            print(f"⚠ Experiments directory not found: {EXPERIMENTS_DIR}")
            return
        
        # Get all experiment folders (skip hidden folders)
        experiment_folders = [
            d for d in EXPERIMENTS_DIR.iterdir()
            if d.is_dir() and not d.name.startswith('.')
        ]
        
        if not experiment_folders:
            print(f"📂 No experiment folders found in {EXPERIMENTS_DIR}")
            return
        
        print(f"\n📂 Checking {len(experiment_folders)} folder(s)...")
        
        # Check each folder
        pending = []
        for folder in experiment_folders:
            # Skip already uploaded
            if folder.name in self.tracker['uploaded_folders']:
                print(f"  ✓ {folder.name} - Already uploaded")
                continue
            
            # Check if complete
            complete, reason = is_experiment_complete(folder)
            if complete:
                print(f"  📤 {folder.name} - {reason}")
                pending.append(folder)
            else:
                print(f"  ⏳ {folder.name} - {reason}")
        
        # Upload pending experiments
        if pending:
            print(f"\n📤 Uploading {len(pending)} experiment(s)...")
            for folder in pending:
                self.upload_experiment(folder)
                time.sleep(1)  # Brief pause between uploads
        else:
            print("  No experiments ready for upload")

# ===== MAIN LOOP =====
def main():
    print("=" * 60)
    print("CO2 Experiment Google Drive Uploader v2.1")
    print("=" * 60)
    print()
    print(f"Monitoring: {EXPERIMENTS_DIR}")
    print(f"Upload to: Google Drive folder {USE_EXISTING_FOLDER_ID}")
    print(f"Completion wait: {COMPLETION_WAIT} seconds")
    print(f"LED feedback: {UPLOAD_FLAG}")
    print()
    
    uploader = DriveUploader()
    authenticated = False
    last_wifi_check = 0
    last_scan = 0
    
    print("🔄 Starting monitoring loop...")
    print("   (Press Ctrl+C to stop)")
    print()
    
    try:
        while True:
            now = time.time()
            
            # Check WiFi periodically
            if now - last_wifi_check >= WIFI_CHECK_INTERVAL:
                last_wifi_check = now
                
                if has_internet():
                    if not authenticated:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] 📶 WiFi connected, authenticating...")
                        authenticated = uploader.authenticate()
                    
                    # Scan for new experiments
                    if authenticated and (now - last_scan >= CHECK_INTERVAL):
                        last_scan = now
                        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Scanning for experiments...")
                        uploader.scan_and_upload()
                else:
                    if authenticated:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] 📵 WiFi disconnected, waiting...")
                    authenticated = False
            
            time.sleep(5)  # Poll every 5 seconds
    
    except KeyboardInterrupt:
        print("\n\n⚠️  Stopped by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
