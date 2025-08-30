# RPI Streamer

A comprehensive Flask-based web application and streaming server for Raspberry Pi, featuring automatic 4G connectivity, GPS tracking, multi-device management, and professional streaming capabilities.

## Quick Start

Run the following commands in your home directory (do **not** use superuser/root):

```sh
curl -H "Cache-Control: no-cache" -O https://raw.githubusercontent.com/tfelici/RPI-Streamer/main/install_rpi_streamer.sh?$(date +%s)
bash install_rpi_streamer.sh --remote
```

## Installation Options

The installation script provides several configuration options:

```sh
# Basic installation with all features
bash install_rpi_streamer.sh

# Installation with Tailscale VPN for secure remote access
bash install_rpi_streamer.sh --tailscale

# Installation with reverse SSH tunnel to your server
bash install_rpi_streamer.sh --reverse-ssh

# Interactive remote access configuration menu
bash install_rpi_streamer.sh --remote

# Use existing local files without updating from GitHub
bash install_rpi_streamer.sh --skip-update
```

### Installation Features

Every installation automatically includes:

- **SIM7600G-H 4G Dongle Support**: Plug-and-play 4G internet connectivity
- **GPS Tracking System**: Real-time location tracking with Flight Settings configuration
- **MediaMTX Streaming Server**: Professional RTMP/WebRTC streaming capabilities
- **Device Registration**: Automatic hardware console integration
- **System Diagnostics**: Real-time hardware monitoring and status reporting
- **USB Storage Detection**: Automatic recording storage to USB devices
- **WiFi Hotspot Mode**: Standalone operation capability

## Core Features

### 🌐 Connectivity & Remote Access
- **4G Cellular Internet**: Built-in SIM7600G-H dongle support with automatic configuration
- **WiFi Hotspot Mode**: Create standalone access point for isolated operation
- **Reverse SSH Tunnels**: Secure remote access through central server with AutoSSH reliability
- **Tailscale VPN**: Mesh networking for seamless device access anywhere
- **Multi-Device Management**: Centralized hardware console with automatic device registration

### 📡 Streaming & Recording
- **MediaMTX Streaming Server**: Professional RTMP, WebRTC, and HLS streaming
- **Audio/Video Device Control**: Web-based selection and configuration of capture devices
- **USB Storage Integration**: Automatic detection and recording to USB drives
- **Recording Management**: Organized storage with automatic directory structure

### 🛰️ GPS & Flight Tracking
- **Real-time GPS Tracking**: Integration with Gyropilots/Gapilots flight tracking platforms
- **Flight Settings Configuration**: Web-based username, vehicle registration, and tracking mode setup
- **Multiple Start Modes**: Manual, auto-start on boot, or auto-start on motion detection
- **GPS Simulation**: Built-in simulation mode for testing without hardware
- **SIM7600 Integration**: Uses cellular dongle for both internet and GPS functionality

### 🔧 System Management
- **Real-time Diagnostics**: System health monitoring with hardware status display
- **Service Management**: Systemd integration for all components with automatic startup
- **Automatic Updates**: Optional GitHub repository synchronization
- **Hardware Registration**: Unique device identification and server registration
- **SSH Key Management**: Automatic RSA key generation and server deployment

### ⚡ Power & Hardware
- **UPS Monitoring**: Optional UPS HAT support with battery status and safe shutdown
- **Power Management**: AC power detection and graceful shutdown capabilities
- **Hardware Detection**: Automatic recognition of connected devices and dongles
- **GPIO Integration**: Raspberry Pi GPIO support for hardware interfacing

## UPS Management (Optional)

The RPI Streamer includes optional UPS (Uninterruptible Power Supply) monitoring and management capabilities for systems with compatible UPS HATs.

### Installation

Install UPS management **before** the main RPI Streamer installation:

```sh
curl -H "Cache-Control: no-cache" -O https://raw.githubusercontent.com/tfelici/RPI-Streamer/main/install_ups_management.sh?$(date +%s)
bash install_ups_management.sh
```

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
cd ~/flask_app
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

## System Services

The RPI Streamer installation creates and manages several systemd services:

### Core Services
- **`flask_app.service`**: Main web application server (HTTP on port 80)
- **`mediamtx.service`**: Professional streaming server for RTMP/WebRTC
- **`sim7600-internet.service`**: 4G dongle internet connectivity management
- **`sim7600-daemon.service`**: SIM7600 communication daemon (port 7600)

### Optional Services
- **`gps-startup.service`**: GPS tracking startup manager (configured via Flight Settings)
- **`reverse-ssh-tunnel.service`**: AutoSSH tunnel for remote access (with --reverse-ssh)
- **`install_rpi_streamer.service`**: Automatic update service for maintenance

### Service Management
```bash
# Check service status
sudo systemctl status flask_app.service
sudo systemctl status sim7600-internet.service

# View service logs
sudo journalctl -u flask_app.service -f
sudo journalctl -u sim7600-internet.service -f

# Restart services if needed
sudo systemctl restart flask_app.service
sudo systemctl restart mediamtx.service
```

## Remote Access Setup

### Option 1: Reverse SSH Tunnel (Recommended)
```sh
bash install_rpi_streamer.sh --reverse-ssh
```

**Features:**
- Automatic device registration with hardware console
- Secure tunnels with AutoSSH reliability and reconnection
- Unique port allocation preventing device conflicts
- SSH key management with 4096-bit RSA keys
- Central server access via SSH port forwarding

**Access your device:**
```bash
# SSH to server with port forwarding
ssh -L 8080:localhost:[device_port] user@streamer.lambda-tek.com -p 2024
# Then visit: http://localhost:8080
```

### Option 2: Tailscale VPN
```sh
bash install_rpi_streamer.sh --tailscale
```

**Features:**
- Mesh networking for direct device access
- No server configuration required
- Mobile app support for smartphone access
- Automatic IP assignment and DNS resolution

**For detailed multi-device setup**, see [MULTI_DEVICE_SETUP.md](MULTI_DEVICE_SETUP.md)

## Hardware Support

### 4G Cellular Connectivity
- **Waveshare SIM7600G-H**: Plug-and-play 4G dongle with automatic RNDIS configuration
- **Multi-carrier support**: Works with most global cellular carriers
- **Automatic reconnection**: Service handles dongle disconnection/reconnection
- **Internet + GPS**: Provides both cellular internet and GPS functionality

### GPS Tracking Hardware
- **SIM7600G-H integrated GPS**: GNSS support (GPS, GLONASS, Galileo, BeiDou)
- **High accuracy tracking**: Professional-grade positioning for flight recording
- **Hardware resilience**: Automatic reconnection handling

### Power Management (Optional)
- **X1200 UPS HAT**: Battery backup with automatic safe shutdown
- **Power monitoring**: Real-time battery status and AC power detection
- **Configurable thresholds**: Customizable shutdown timing and battery levels

### Storage & Connectivity
- **USB storage**: Automatic detection and mounting of USB drives for recordings
- **WiFi hotspot**: Built-in access point mode for standalone operation
- **Ethernet**: Standard wired network support
- **GPIO**: Full Raspberry Pi GPIO access for custom hardware integration

## Installation Requirements

- **Hardware**: Raspberry Pi (any model with USB port)
- **Operating System**: Raspberry Pi OS Lite (or compatible Linux distribution)
- **Network**: Internet connection for initial setup (WiFi, Ethernet, or 4G)
- **Storage**: MicroSD card (16GB+ recommended) + optional USB storage
- **Optional**: SIM7600G-H dongle, UPS HAT, GPS antenna

## Quick Setup Guide

1. **Flash Raspberry Pi OS Lite** to SD card
2. **Enable SSH** and configure WiFi (if using wireless for setup)
3. **Run installation script**:
   ```bash
   curl -H "Cache-Control: no-cache" -O https://raw.githubusercontent.com/tfelici/RPI-Streamer/main/install_rpi_streamer.sh?$(date +%s)
   bash install_rpi_streamer.sh --remote
   ```
4. **Configure Flight Settings** via web interface (if using GPS tracking)
5. **Connect hardware** (4G dongle, cameras, etc.) as needed

## Web Interface Access

After installation, access the control panel at:
- **Local**: `http://[raspberry-pi-ip]/`
- **Remote**: Via configured tunnel or VPN
- **Default login**: No authentication required (configure as needed)

## Documentation

- **[GPS Tracker Guide](GPS_TRACKER_README.md)**: GPS tracking configuration and usage
- **[Flight Settings](FLIGHT_SETTINGS.md)**: GPS username, tracking modes, and vehicle registration
- **[SIM7600 Setup](SIM7600_INTERNET_SETUP.md)**: 4G dongle configuration and troubleshooting
- **[SIM7600 Daemon](SIM7600_DAEMON_README.md)**: Communication daemon and API reference
- **[Multi-Device Setup](MULTI_DEVICE_SETUP.md)**: Central server management and tunneling
- **[WiFi Hotspot](WIFI_HOTSPOT_SETUP.md)**: Standalone access point configuration

## Support & Troubleshooting

### Common Issues
```bash
# Check service status
sudo systemctl status flask_app.service
sudo systemctl status sim7600-internet.service

# View logs
sudo journalctl -u flask_app.service -f
sudo journalctl -u sim7600-internet.service -f

# Test 4G connectivity
ping -c 3 8.8.8.8

# Check GPS tracking
sudo journalctl -u gps-startup.service -f
```

### Getting Help
- **GitHub Issues**: [Report bugs or request features](https://github.com/tfelici/RPI-Streamer/issues)
- **Documentation**: Check the relevant documentation files above
- **Service Logs**: Use `journalctl` commands to diagnose service issues
- **Hardware Console**: Access device management through the web interface

### System Requirements
- Raspberry Pi with Raspberry Pi OS Lite
- 16GB+ MicroSD card
- Internet connection (WiFi, Ethernet, or 4G dongle)
- Python 3.7+ and FFmpeg (automatically installed)

---

**RPI Streamer** - Professional streaming and GPS tracking solution for Raspberry Pi
