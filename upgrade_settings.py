#!/usr/bin/env python3
"""
Settings Upgrade Script for RPI Streamer

This script migrates cellular settings from the old settings.json format
to the new cellular.json format for better organization and separation of concerns.

Cellular settings to migrate:
- cellular_apn
- cellular_username  
- cellular_password
- cellular_mcc
- cellular_mnc

Usage: python upgrade_settings.py
"""

import json
import os
import shutil
import sys

# Add current directory to path to import utils
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from utils import STREAMER_DATA_DIR, SETTINGS_FILE
except ImportError:
    # Fallback if utils can't be imported - define constants directly
    STREAMER_DATA_DIR = os.path.expanduser('~/streamerData')
    SETTINGS_FILE = os.path.join(STREAMER_DATA_DIR, 'settings.json')

# Define the cellular settings keys that need to be migrated
CELLULAR_SETTINGS_KEYS = [
    'cellular_apn',
    'cellular_username', 
    'cellular_password',
    'cellular_mcc',
    'cellular_mnc'
]

def backup_file(filepath):
    """Create a backup of the file with .backup extension"""
    if os.path.exists(filepath):
        backup_path = filepath + '.backup'
        shutil.copy2(filepath, backup_path)
        print(f"✓ Created backup: {backup_path}")
        return backup_path
    return None

def load_json_file(filepath):
    """Load JSON file with error handling"""
    if not os.path.exists(filepath):
        return None
    
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"✗ Error reading {filepath}: {e}")
        return None

def save_json_file(filepath, data):
    """Save JSON file with error handling"""
    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
        return True
    except IOError as e:
        print(f"✗ Error writing {filepath}: {e}")
        return False

def main():
    """Main upgrade function"""
    print("RPI Streamer Settings Upgrade")
    print("=" * 40)
    print("Migrating cellular settings from settings.json to cellular.json...")
    print()
    
    # Define file paths
    settings_path = SETTINGS_FILE
    cellular_path = os.path.join(STREAMER_DATA_DIR, 'cellular.json')
    
    # Load existing settings.json
    print(f"📁 Loading settings from: {settings_path}")
    settings_data = load_json_file(settings_path)
    
    if settings_data is None:
        print("✗ Could not load settings.json - file may not exist or be corrupted")
        return False
    
    # Check if any cellular settings exist in settings.json
    cellular_settings_found = {}
    for key in CELLULAR_SETTINGS_KEYS:
        if key in settings_data:
            cellular_settings_found[key] = settings_data[key]
    
    if not cellular_settings_found:
        print("✓ No cellular settings found in settings.json - no migration needed")
        return True
    
    print(f"📱 Found {len(cellular_settings_found)} cellular settings to migrate:")
    for key, value in cellular_settings_found.items():
        # Don't print passwords in full, just show if they exist
        if 'password' in key.lower() and value:
            display_value = "****** (hidden)"
        else:
            display_value = repr(value)
        print(f"   • {key}: {display_value}")
    print()
    
    # Load existing cellular.json if it exists
    print(f"📱 Checking existing cellular settings: {cellular_path}")
    existing_cellular = load_json_file(cellular_path)
    
    if existing_cellular is None:
        print("✓ No existing cellular.json found - creating new file")
        cellular_data = {}
    else:
        print("⚠ Found existing cellular.json - will merge settings")
        cellular_data = existing_cellular.copy()
    
    # Create backups before making changes
    print("\n📋 Creating backups...")
    settings_backup = backup_file(settings_path)
    if existing_cellular:
        cellular_backup = backup_file(cellular_path)
    
    # Merge cellular settings (settings.json takes precedence)
    print("\n🔄 Migrating cellular settings...")
    migration_count = 0
    for key, value in cellular_settings_found.items():
        if key in cellular_data and cellular_data[key] != value:
            print(f"   ⚠ Overwriting {key}: {repr(cellular_data[key])} -> {repr(value)}")
        elif key not in cellular_data:
            print(f"   + Adding {key}: {repr(value)}")
        else:
            print(f"   ✓ Keeping {key}: {repr(value)}")
        
        cellular_data[key] = value
        migration_count += 1
    
    # Add default values for any missing cellular settings
    cellular_defaults = {
        "cellular_apn": "internet",
        "cellular_username": "",
        "cellular_password": "",
        "cellular_mcc": "",
        "cellular_mnc": ""
    }
    
    for key, default_value in cellular_defaults.items():
        if key not in cellular_data:
            cellular_data[key] = default_value
            print(f"   + Adding default {key}: {repr(default_value)}")
    
    # Save updated cellular.json
    print(f"\n💾 Saving cellular settings to: {cellular_path}")
    if not save_json_file(cellular_path, cellular_data):
        print("✗ Failed to save cellular.json")
        return False
    print("✓ Successfully saved cellular.json")
    
    # Remove cellular settings from settings.json
    print(f"\n🧹 Removing cellular settings from: {settings_path}")
    removal_count = 0
    for key in CELLULAR_SETTINGS_KEYS:
        if key in settings_data:
            del settings_data[key]
            print(f"   - Removed {key}")
            removal_count += 1
    
    # Save updated settings.json
    print(f"\n💾 Saving updated settings to: {settings_path}")
    if not save_json_file(settings_path, settings_data):
        print("✗ Failed to save settings.json")
        return False
    print("✓ Successfully saved settings.json")
    
    # Summary
    print("\n" + "=" * 40)
    print("📊 Migration Summary:")
    print(f"   • Migrated {migration_count} cellular settings")
    print(f"   • Removed {removal_count} settings from settings.json")
    print(f"   • Created backup files: {settings_backup is not None}")
    
    if settings_backup:
        print(f"   • Settings backup: {os.path.basename(settings_backup)}")
    
    print("\n✅ Migration completed successfully!")
    print("\nThe cellular settings have been moved to cellular.json and")
    print("removed from settings.json. The application will now use the")
    print("separate cellular configuration file.")
    
    return True

if __name__ == '__main__':
    try:
        success = main()
        exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n⚠ Migration interrupted by user")
        exit(1)
    except Exception as e:
        print(f"\n✗ Unexpected error during migration: {e}")
        import traceback
        traceback.print_exc()
        exit(1)