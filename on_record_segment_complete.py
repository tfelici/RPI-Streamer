#!/usr/bin/env python3

import os
import subprocess
import sys
import re
import shutil
from utils import find_usb_storage, set_mounted_usb_device, register_usb_cleanup

# Redirect all stdout and stderr to a log file for debugging
log_file = open("../encoderData/segment_complete.log", "a")
sys.stdout = log_file
sys.stderr = log_file

def copy_executables_to_usb(usb_mount):
    """Copy executables from the parent directory to USB root"""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        executables_dir = os.path.join(os.path.dirname(script_dir), "executables")
        
        if not os.path.exists(executables_dir):
            print(f"Executables directory not found: {executables_dir}")
            return
        
        print(f"Copying executables from {executables_dir} to {usb_mount}")
        
        # Copy each file in the executables directory to USB root
        for filename in os.listdir(executables_dir):
            src_path = os.path.join(executables_dir, filename)
            dst_path = os.path.join(usb_mount, filename)
            
            if os.path.isfile(src_path):
                shutil.copy2(src_path, dst_path)
                print(f"Copied executable: {filename}")
            elif os.path.isdir(src_path):
                if os.path.exists(dst_path):
                    shutil.rmtree(dst_path)
                shutil.copytree(src_path, dst_path)
                print(f"Copied executable directory: {filename}")
        
        print("Executables copied successfully to USB")
        
    except Exception as e:
        print(f"Warning: Failed to copy executables to USB: {e}")
        # Don't exit on failure - this is not critical for the main function

def main():
    # Register USB cleanup on script exit
    register_usb_cleanup()
    
    # Get parameters from command line
    if len(sys.argv) != 4:
        print("Usage: python3 on_record_segment_complete.py <segment_path> <segment_duration> <path_name>")
        sys.exit(1)
    
    segment_path = sys.argv[1]
    segment_duration = sys.argv[2]
    path_name = sys.argv[3]
    
    if not segment_path or not segment_duration:
        print("Missing segment_path or segment_duration parameters")
        sys.exit(1)

    # Extract the number before .mp4 in the segment path
    m = re.search(r"(\d+)(?=\.mp4$)", segment_path)
    if m:
        base_number = m.group(1)
    else:
        base_number = "unknown"
    
    # Check for USB storage device
    usb_mount = find_usb_storage()
    
    if usb_mount:
        # Use USB storage and register it for cleanup
        output_dir = os.path.join(usb_mount, "encoderData", "recordings", path_name)
        print(f"Using USB storage for output: {usb_mount}")
        set_mounted_usb_device(usb_mount)  # Register for cleanup
    else:
        # Fall back to local storage
        script_dir = os.path.dirname(os.path.abspath(__file__))
        output_dir = os.path.join(os.path.dirname(script_dir), "encoderData", "recordings", path_name)
        print("Using local storage for output")
      # Create output directory
    try:
        os.makedirs(output_dir, exist_ok=True)
        
        # If using USB storage, copy executables to USB root
        if usb_mount:
            copy_executables_to_usb(usb_mount)
            
    except Exception as e:
        print(f"Failed to create output directory {output_dir}: {e}")
        # Fall back to local storage if USB creation fails
        if usb_mount:
            print("Falling back to local storage due to USB directory creation failure")
            script_dir = os.path.dirname(os.path.abspath(__file__))
            output_dir = os.path.join(os.path.dirname(script_dir), "encoderData", "recordings", path_name)
            os.makedirs(output_dir, exist_ok=True)
        else:
            sys.exit(4)
    
    output_filename = f"{base_number}d{segment_duration}.mp4"
    output_path = os.path.join(output_dir, output_filename)

    # Run ffmpeg to move and faststart the segment
    ffmpeg_cmd = [
        "ffmpeg",
        "-i", segment_path,
        "-movflags", "faststart",
        "-c", "copy",
        output_path
    ]
    print("Running:", " ".join(ffmpeg_cmd))
    try:
        subprocess.run(ffmpeg_cmd, check=True)
    except subprocess.CalledProcessError as e:
        print("ffmpeg failed:", e)
        sys.exit(2)

    # Remove the original segment file
    try:
        os.remove(segment_path)
        print(f"Removed original segment: {segment_path}")
    except Exception as e:
        print("Failed to remove original segment:", e)
        sys.exit(3)

    print(f"Segment processed and moved to {output_path}")

if __name__ == "__main__":
    main()
