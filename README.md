# RPI Streamer

A Flask-based web app and streaming server for Raspberry Pi, with easy installation and systemd integration.

## Installation

Run the following commands in your home directory (do **not** use superuser/root):

```sh
curl -H "Cache-Control: no-cache" -O https://raw.githubusercontent.com/tfelici/RPI-Streamer/main/install_rpi_streamer.sh?$(date +%s)
bash install_rpi_streamer.sh --tailscale
```

## Features

- Simple web UI for streaming and settings
- Systemd service setup
- MediaMTX streaming server
- Audio/video device selection
- **Automatic USB storage detection and mounting**
- **Recording segments saved to USB when available**
- **Auto-restart webcam service when settings change**
- Easy update and maintenance

## USB Storage Support

The RPI Streamer automatically detects and uses USB storage devices for saving recording segments:

### Automatic Detection
- **Multiple detection methods**: Uses `lsblk`, `/dev` scanning, and `udevadm` for reliable USB device detection
- **Auto-mounting**: Automatically mounts detected USB storage with proper permissions
- **Filesystem support**: Supports FAT32, exFAT, NTFS, ext4, ext3, and ext2 filesystems
- **Fallback options**: If auto-detection fails, tries common filesystem types

### Recording Storage
- **USB priority**: Recording segments are automatically saved to USB storage when available
- **Local fallback**: Falls back to local storage if no USB device is detected
- **Auto-cleanup**: USB devices are safely unmounted when the script exits
- **Directory structure**: Creates `encoderData/recordings/<stream_name>/` on USB devices

### Testing USB Detection
Test the USB detection functionality without running recordings:

```sh
cd ~/RPI-streamer
python3 test_usb_detection.py
```

This will show:
- Detected USB devices and their filesystems
- Currently mounted USB devices
- Mount points and write permissions

### Manual USB Management
If needed, you can manually mount/unmount USB devices:

```sh
# List all block devices
lsblk

# Manually mount a USB device
sudo mkdir -p /mnt/usb_sdb1
sudo mount -t auto -o uid=1000,gid=1000,umask=022 /dev/sdb1 /mnt/usb_sdb1

# Unmount when done
sudo umount /mnt/usb_sdb1
```

## Requirements

- Raspberry Pi OS Lite (or compatible Linux)
- Python 3
- ffmpeg

## Usage

After installation, access the web UI at `http://<your-pi-ip>/` in your browser.

## Support

For issues or questions, open an issue on the [GitHub repository](https://github.com/tfelici/RPI-Streamer).
