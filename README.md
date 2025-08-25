# RPI Streamer

A Flask-based web app and streaming server for Raspberry Pi, with easy installation and systemd integration.

## Installation

### Standard Installation

Run the following commands in your home directory (do **not** use superuser/root):

```sh
curl -H "Cache-Control: no-cache" -O https://raw.githubusercontent.com/tfelici/RPI-Streamer/main/install_rpi_streamer.sh?$(date +%s)
bash install_rpi_streamer.sh --tailscale
```

**Note**: The installation automatically includes SIM7600G-H 4G dongle support and all necessary drivers/services. This provides plug-and-play functionality when a dongle is connected later.

### Multi-Device Setup with Reverse SSH Tunnels

For managing multiple RPI Streamer devices from a central server, use the reverse SSH tunnel installation:

```sh
curl -sSL https://raw.githubusercontent.com/tfelici/RPI-Streamer/main/install_rpi_streamer.sh | bash -s -- --reverse-ssh
```

This enhanced installation provides:

- **Automatic device registration** with gyropilots.org hardware console
- **Secure reverse SSH tunnels** with AutoSSH reliability
- **Unique port allocation** (no conflicts between devices)
- **SSH key management** (RSA 4096-bit keys auto-generated)
- **Central server access** via SSH port forwarding
- **Hardware ID-based identification** for consistent device tracking

#### Access Your Devices

After installation, access your devices through your central server:

```bash
# SSH to your server with port forwarding
ssh -L 8080:localhost:45001 user@your-server.com -p 45002

# Open browser to: http://localhost:8080
# This gives you direct access to the RPI Streamer web interface
```

**For complete multi-device setup instructions**, see [MULTI_DEVICE_SETUP.md](MULTI_DEVICE_SETUP.md)

### UPS Management (Optional)

If you have a UPS hardware HAT installed, you can optionally install the UPS management system:

```sh
curl -H "Cache-Control: no-cache" -O https://raw.githubusercontent.com/tfelici/RPI-Streamer/main/install_ups_management.sh?$(date +%s)
bash install_ups_management.sh
```

The UPS management system provides:

- **Real-time UPS monitoring** in the system diagnostics panel
- **Automatic safe shutdown** on power loss with configurable grace period
- **Battery status tracking** (voltage, capacity, health status)
- **AC power state detection** (plugged in/unplugged)
- **Systemd service integration** for continuous monitoring

## Features

- Simple web UI for streaming and settings
- Systemd service setup
- MediaMTX streaming server
- Audio/video device selection
- **System diagnostics panel** with real-time hardware monitoring
- **Multi-device management** with reverse SSH tunnels and central server access
- **Automatic device registration** with hardware console integration
- **UPS monitoring and management** (optional, requires UPS HAT)
- **4G cellular internet** (built-in support for Waveshare SIM7600G-H dongle)
- **WiFi hotspot mode** for standalone operation
- **GPS tracking with simulation** for flight recording
- **Automatic USB storage detection and mounting**
- **Recording segments saved to USB when available**
- **Auto-restart webcam service when settings change**
- Easy update and maintenance

## UPS Management (Optional)

The RPI Streamer includes optional UPS (Uninterruptible Power Supply) monitoring and management capabilities for systems with compatible UPS HATs:

### Supported Hardware
- **X1200 UPS HAT** and compatible I2C-based UPS devices
- **GPIO-based power detection** (PLD pin for AC power state)
- **I2C communication** at address 0x36 for battery monitoring

### Features
- **Real-time monitoring**: Battery voltage, capacity, and health status
- **AC power detection**: Automatic detection of power loss/restoration
- **Safe shutdown**: Configurable grace period before shutdown on power loss
- **System integration**: UPS status displayed in the web diagnostics panel
- **Automatic service**: Runs as systemd service for continuous monitoring

### Installation
Install UPS management **before** the main RPI Streamer installation:

```sh
curl -H "Cache-Control: no-cache" -O https://raw.githubusercontent.com/tfelici/RPI-Streamer/main/install_ups_management.sh?$(date +%s)
bash install_ups_management.sh
```

### Configuration
- **Grace period**: Default 60 seconds before shutdown (configurable)
- **Critical thresholds**: Battery < 20% or voltage < 3.2V triggers shutdown
- **Service management**: Use `sudo systemctl status ups-monitor` to check status

### Troubleshooting
```sh
# Check UPS detection
sudo i2cdetect -y 1

# View UPS service logs
sudo journalctl -u ups-monitor -f

# Test UPS integration
python3 -c "from x120x import X120X; with X120X() as ups: print(ups.get_status())"
```

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
- **Directory structure**: Creates `streamerData/recordings/<stream_name>/` on USB devices

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
