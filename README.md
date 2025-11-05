# RPI Streamer v3.10

A comprehensive Flask-based web application and streaming platform for Raspberry Pi, delivering professional-grade video streaming, GPS flight tracking, cellular connectivity, and centralized device management. Built for pilots, content creators, and professionals requiring reliable remote streaming and tracking solutions.

## 🎯 Overview

The RPI Streamer transforms any Raspberry Pi into a professional streaming and tracking device with cellular connectivity, GPS tracking, video recording, and remote management capabilities. Perfect for aviation, surveillance, live events, and remote monitoring applications.

## ⚡ Quick Start

### One-Line Installation

Run the following commands in your home directory (do **not** use superuser/root):

#### Latest Development Version (Recommended)
```bash
curl -H "Cache-Control: no-cache" -O https://raw.githubusercontent.com/tfelici/RPI-Streamer/develop/install_rpi_streamer.sh?$(date +%s)
bash install_rpi_streamer.sh --develop
```

#### Stable Release Version
```bash
curl -H "Cache-Control: no-cache" -O https://raw.githubusercontent.com/tfelici/RPI-Streamer/main/install_rpi_streamer.sh?$(date +%s)
bash install_rpi_streamer.sh --main
```

#### Automated Deployment (No Prompts)
```bash
curl -H "Cache-Control: no-cache" -O https://raw.githubusercontent.com/tfelici/RPI-Streamer/develop/install_rpi_streamer.sh?$(date +%s)
bash install_rpi_streamer.sh --develop --daemon
```

### Access Your Device
After installation, access the control panel at:
- **Local Network**: `http://[raspberry-pi-ip]/`
- **Direct Connection**: Enable WiFi hotspot mode for standalone access
- **Remote Access**: Configure cellular connectivity for anywhere access

## Installation Options

The installation script provides several configuration options:

```sh
# Development installation with latest features (recommended)
bash install_rpi_streamer.sh --develop

# Stable release installation (main branch)
bash install_rpi_streamer.sh --main

# Silent installation without interactive prompts
bash install_rpi_streamer.sh --develop --daemon
```

### Installation Option Details

- **`--develop`**: Install latest development branch with newest features
- **`--main`**: Install stable main branch (default if no branch specified)
- **`--daemon`**: Run in daemon mode with no interactive prompts (ideal for automated deployment)

### Use Cases

```sh
# First-time installation with latest features
bash install_rpi_streamer.sh --develop

# Production deployment (stable)
bash install_rpi_streamer.sh --main

# Automated deployment without prompts
bash install_rpi_streamer.sh --develop --daemon
```

## Branch Structure

The RPI Streamer uses industry standard git branching for reliable deployments:

- **🔵 Develop Branch (Latest Features)**: Current development version with latest features
  - **Recommended installation** for most users
  - Latest features and improvements
  - Centralized hardware console integration
  - Enhanced GPS simulation and device management
  - Modern responsive mobile interface
  
- **🟢 Main Branch**: Stable release version
  - Stable version for conservative deployments
  - Use only if develop branch features are not needed

To install the latest version:
```sh
# Development installation with latest features (recommended)
bash install_rpi_streamer.sh --develop

# Stable release installation
bash install_rpi_streamer.sh
```

### Installation Features

Every installation automatically includes:

- **Complete WiFi Management**: Modern network scanner with real-time network discovery and one-click connections
- **WiFi Hotspot with Routing**: Full NAT routing via ethernet with automatic NetworkManager configuration
- **GPS Daemon System**: Streamlined GPS daemon with integrated initialization, multi-client architecture, and simulation mode
- **Enhanced Cellular Modem Support**: RNDIS/NON-RNDIS mode switching with USB power cycling recovery
- **MediaMTX Streaming Server**: Professional RTMP/WebRTC streaming capabilities
- **Device Registration**: Automatic hardware console integration with centralized management
- **System Diagnostics**: Centralized hardware monitoring via heartbeat daemon with UPS status, power metrics, and vcgencmd integration
- **USB Storage Detection**: Automatic recording storage to USB devices
- **Heartbeat Monitoring**: Centralized hardware diagnostics daemon collecting vcgencmd, UPS, and power data every 5 seconds
- **Mobile Responsive Interface**: Modern mobile-friendly web interface for device management

## Development Configuration Tool

For development and maintenance tasks, the RPI Streamer includes an interactive configuration menu:

```sh
# Run the configuration menu
rpiconfig
```

### Available Options

The `rpiconfig` script provides a user-friendly menu with the following options:

1. **Restart Flask App Service** - Quick restart of the main web application
2. **Check Flask App Service Logs** - View and monitor Flask application logs
3. **Install/Update (Develop Branch)** - Full installation with latest develop branch features
4. **Install Local Code (Develop, No Update)** - Install using existing local files without GitHub updates
5. **Toggle GPS Mode (Simulation/Real)** - Switch between GPS simulation mode and real hardware mode
6. **Restart GPS Startup Manager** - Restart the GPS automatic startup management service
7. **Restart Heartbeat Daemon** - Restart the centralized hardware monitoring daemon
8. **Show System Status** - Display current service states and system information
9. **Enable/Disable Power Monitor Service** - Toggle UPS power monitoring service on/off
**r. Reboot Now** - Safely restart the Raspberry Pi system
**0. Exit** - Close the configuration menu

### Features

- **🎨 Color-coded interface** with clear status indicators
- **🔧 System status overview** showing all service states including UPS monitor
- **⚡ Quick service management** without memorizing systemd commands
- **🛰️ GPS mode switching** with intelligent toggle between simulation and real hardware
- **🔋 Power monitor control** for enabling/disabling UPS monitoring service
- **📊 Real-time status** updates and error reporting
- **🔒 Privilege detection** automatically handles sudo requirements

### Usage Examples

```sh
# Interactive development menu
rpiconfig

# Check if script exists first
[ -f "rpiconfig.sh" ] && bash rpiconfig.sh || echo "Run from RPI Streamer directory"

# Make executable and run directly (on Raspberry Pi)
chmod +x rpiconfig.sh && ./rpiconfig.sh
```

This tool streamlines common development workflows and eliminates the need to remember complex systemd commands during development and testing.

## 🚀 Core Features

### 📡 Professional Video Streaming
- **Dual Streaming Engines**: Choose between FFmpeg and GStreamer for optimal performance
- **Multi-Protocol Support**: RTMP, WebRTC, HLS, and SRT streaming via MediaMTX server  
- **Advanced Video Controls**: Resolution (480p-1080p), framerate (15-60fps), bitrate optimization
- **Video Enhancement Features**:
  - **Video Stabilization**: Software stabilization to reduce camera shake
  - **Video Vertical Mirror**: Flip video upside-down for inverted camera mounting
  - **Dynamic Bitrate**: Automatic bitrate adjustment based on network conditions
  - **Hardware Acceleration**: RPi hardware encoder support for efficient streaming
- **Audio Processing**: Multi-format audio capture with volume control and noise reduction
- **Device Auto-Detection**: Automatic detection and configuration of USB cameras and microphones

### 🌐 Connectivity & Network Management
- **Complete WiFi Management**: Modern network scanner with one-click connections and real-time status
- **WiFi Hotspot Mode**: Standalone hostname-based access point with full NAT routing via Ethernet
- **Universal Cellular Support**: Compatible with most USB 4G/LTE modems via ModemManager
  - Automatic APN detection and carrier configuration
  - RNDIS/NON-RNDIS mode switching with USB power cycling recovery
  - Hardware reset capabilities via `uhubctl` for robust connection recovery
- **Multi-Connection Priority**: Intelligent network priority management (Ethernet → WiFi → Cellular)
- **Remote Access Solutions**:
  - Reverse SSH tunnels through central server with AutoSSH reliability
  - Tailscale VPN mesh networking for seamless device access
  - Port forwarding and firewall management

### �️ GPS Flight Tracking System
- **Modern GPS Daemon**: Streamlined architecture with integrated initialization and multi-client support
- **Universal GPS Hardware Support**:
  - USB GPS receivers with automatic detection
  - GPS HATs and expansion boards
  - Cellular modem integrated GPS (SIM7600G-H and compatible)
- **Enhanced GNSS Constellation Support**:
  - GPS + GLONASS + Galileo + BeiDou satellites
  - Real-time constellation display with satellite metrics
  - Signal quality monitoring and positioning accuracy
- **Intelligent Tracking Modes**:
  - Manual start/stop control
  - Auto-start on device boot
  - Auto-start on motion detection with configurable sensitivity
  - GPS-linked streaming (start/stop streaming with GPS tracking)
- **Advanced GPS Features**:
  - **GPS Simulation Mode**: Oxford Airport circular flight pattern with realistic altitude profiles
  - **Power Loss Protection**: Configurable auto-stop on power loss with timeout settings
  - **Track Export**: Standard format track files for flight analysis
  - **Real-time Web Display**: Live GPS status and tracking information

### 📹 Recording & Storage Management
- **Intelligent Storage System**: 
  - Automatic USB storage detection and mounting (FAT32, exFAT, NTFS, ext4)
  - Hierarchical recording organization by domain/device/timestamp
  - Fallback to local storage when USB unavailable
- **Recording Features**:
  - Continuous recording with automatic segment management
  - Real-time recording status and active file monitoring
  - Automatic storage space management
- **USB Drive Support**:
  - Hot-plug detection with multiple detection methods
  - Safe mounting/unmounting with proper filesystem support
  - Executable synchronization for portable operation

### 🔧 Device Management & Monitoring
- **Centralized Hardware Console**: Web-based fleet management with device settings and status
- **Real-time System Diagnostics**:
  - **Heartbeat Monitoring**: Independent daemon collecting system metrics every 5 seconds
  - **Hardware Status**: CPU, memory, temperature, and storage monitoring
  - **Service Health**: Real-time status of all system services
  - **UPS Integration**: Battery status, AC power detection, and safe shutdown management
- **Mobile-Responsive Interface**: Modern web interface optimized for smartphones and tablets
- **Device Registration**: Automatic hardware ID generation and server registration
- **Remote Configuration**: Apply settings changes remotely via queued command system

### ⚡ Power & Hardware Management
- **UPS Monitoring System** (Optional):
  - X1200 UPS HAT and compatible I2C-based UPS devices
  - Real-time battery voltage, capacity, and health monitoring
  - Configurable grace periods and critical shutdown thresholds
  - AC power detection with automatic power loss response
- **GPIO Integration**: Full Raspberry Pi GPIO access for custom hardware interfacing
- **Hardware Auto-Detection**: Automatic recognition of connected cameras, microphones, and dongles
- **System Integration**: Seamless integration with NetworkManager and ModemManager

### 🛠️ Development & Maintenance Tools
- **Interactive Configuration Menu** (`rpiconfig`):
  - Service management without memorizing systemd commands
  - GPS mode switching (simulation/real hardware)
  - System status overview with color-coded indicators
  - Quick restart and update capabilities
- **Automatic Updates**: Branch-aware GitHub synchronization with development/stable branches
- **Service Management**: Comprehensive systemd integration for all components
- **Logging & Diagnostics**: Centralized logging with real-time log viewing capabilities

## 🎥 Advanced Video Processing Features

### Video Enhancement Controls
The RPI Streamer includes professional video processing capabilities accessible through all device settings interfaces:

#### **Video Stabilization**
- **Software Stabilization**: Reduces camera shake during flight or vehicle movement
- **Real-time Processing**: Applied during streaming with minimal latency impact
- **Configurable**: Enable/disable via web interface or remote device management
- **Engine Support**: Available in both FFmpeg (`deshake` filter) and GStreamer (`videostabilize` element)

#### **Video Vertical Mirror**  
- **Upside-Down Flip**: Vertically mirrors video for inverted camera mounting scenarios
- **Common Use Cases**: 
  - Cameras mounted upside-down in aircraft installations
  - Ceiling-mounted surveillance cameras
  - Inverted action cameras on drones or vehicles
- **Real-time Operation**: Applied during streaming without affecting recording quality
- **Universal Support**: Works with both FFmpeg (`vflip` filter) and GStreamer (`videoflip method=vertical-flip`) engines

#### **Dynamic Bitrate Adjustment**
- **Network-Adaptive**: Automatically adjusts video quality based on available bandwidth
- **Connection Monitoring**: Real-time network performance assessment
- **Quality Optimization**: Maintains best possible quality within bandwidth constraints
- **Cellular-Optimized**: Particularly useful for cellular streaming with variable signal strength

#### **Hardware Acceleration**
- **Raspberry Pi GPU**: Utilizes VideoCore GPU for efficient H.264 encoding
- **Automatic Detection**: Probes and selects optimal encoder (hardware vs. software)
- **Fallback Support**: Gracefully falls back to software encoding if hardware unavailable
- **Performance Benefits**: Reduced CPU usage and improved streaming performance

### Video Configuration Options

All video processing features are configurable through:
- **Local Web Interface**: Direct device configuration via browser
- **Streamer Admin Console**: Remote fleet management and settings deployment  
- **API Endpoints**: Programmatic configuration for automated deployments
- **Real-time Updates**: Settings changes applied immediately without restart required

### Technical Implementation

#### **Dual Engine Architecture**
```bash
# FFmpeg Pipeline (with vertical mirror and stabilization)
ffmpeg -i /dev/video0 -vf "deshake=x=-1:y=-1:w=-1:h=-1:rx=16:ry=16,vflip,scale=1280:-2" -c:v h264_v4l2m2m

# GStreamer Pipeline (with vertical mirror and stabilization)
gst-launch-1.0 v4l2src device=/dev/video0 ! videostabilize ! videoflip method=vertical-flip ! h264enc
```

#### **Real-time Configuration**
- **Settings Detection**: Automatic detection of configuration changes
- **Pipeline Restart**: Intelligent pipeline restart when settings modified
- **State Preservation**: Maintains streaming connections during configuration updates
- **Error Recovery**: Automatic fallback and error recovery mechanisms

## UPS Management (Optional)

The RPI Streamer includes optional UPS (Uninterruptible Power Supply) monitoring and management capabilities for systems with compatible UPS HATs.

### Installation

Install UPS management **before** the main RPI Streamer installation:

#### Latest Development Version
```sh
curl -H "Cache-Control: no-cache" -O https://raw.githubusercontent.com/tfelici/RPI-Streamer/develop/install_ups_management.sh?$(date +%s)
bash install_ups_management.sh
```

#### Stable Version
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
python3 -c "
from x120x import X120X
with X120X() as ups:
    print(ups.get_status())
"
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
- **`heartbeat-daemon.service`**: Independent system health monitoring and status reporting

### GPS and Connectivity Services
- **ModemManager.service**: Cellular modem management (standard Linux cellular service)
- **NetworkManager.service**: Network connection management (WiFi, Ethernet, Cellular)
- **`gps-daemon.service`**: Modern GPS daemon with integrated initialization, multi-client support, and simulation mode

### Optional Services
- **`gps-startup.service`**: GPS tracking startup manager (configured via Flight Settings)
- **`install_rpi_streamer.service`**: Automatic update service for maintenance

### Service Management
```bash
# Check service status
sudo systemctl status flask_app.service
sudo systemctl status ModemManager.service

# View service logs
sudo journalctl -u flask_app.service -f

# Restart services if needed
sudo systemctl restart flask_app.service
sudo systemctl restart mediamtx.service
```

## System Updates

The RPI Streamer includes branch-aware automatic update capabilities via the web interface:

### Development Branch Updates
- **Branch-aware updates**: Development installations update from develop branch, main installations from main branch
- **Web interface**: Check for updates and apply them through System Settings page
- **Safe updates**: Automatic backup and rollback capabilities
- **Service restart**: Automatic service restart after updates

### Manual Updates
```bash
# Using the development configuration tool (recommended)
rpiconfig
# Then select option 2 or 3 for installation/updates

# Or manually update via installation script:
cd ~/flask_app
bash install_rpi_streamer.sh --develop               # Full update from develop branch
bash install_rpi_streamer.sh --main                  # Update to stable main branch
```

### Update Process
1. **Check**: System automatically detects differences with remote repository
2. **Update**: Downloads and applies changes from the correct branch (develop/main)
3. **Restart**: Automatically restarts services to apply changes
4. **Verify**: Status confirmation through web interface

## Hardware Support

### 4G Cellular Connectivity
- **Universal Cellular Modem Support**: Compatible with most USB cellular modems via ModemManager
- **RNDIS/NON-RNDIS Mode Switching**: Automatic USB PID configuration for RNDIS (9011) and traditional (9001) modes
- **Hardware Recovery**: USB power cycling with automatic modem detection for robust connection recovery
- **Multi-carrier support**: Works with most global cellular carriers with automatic APN detection
- **Enhanced Recovery System**: Graduated recovery methods including hardware reset via `uhubctl`
- **NetworkManager Integration**: Seamless integration with system network management

### GPS Tracking Hardware
- **Universal GPS Support**: USB GPS receivers, GPS HATs, and cellular modem GPS with automatic detection
- **Enhanced GNSS**: GPS + GLONASS + Galileo + BeiDou constellation support via modern GPS daemon
- **High accuracy tracking**: Professional-grade positioning for flight recording with real-time constellation display
- **Modern GPS Architecture**: Streamlined daemon eliminates race conditions and provides real-time data to multiple clients
- **GPS Simulation**: Built-in Oxford Airport circular flight pattern with realistic altitude profiles for testing
- **Constellation-Specific Metrics**: Detailed satellite tracking with per-constellation visibility and signal quality

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
- **Network**: Internet connection for initial setup (WiFi, Ethernet, or cellular modem)
- **Storage**: MicroSD card (16GB+ recommended) + optional USB storage
- **Optional**: USB cellular modem, UPS HAT, GPS hardware, external antenna

## Quick Setup Guide

1. **Flash Raspberry Pi OS Lite** to SD card
2. **Enable SSH** and configure WiFi (if using wireless for setup)
3. **Run installation script**:

   **Development version (recommended):**
   ```bash
   curl -H "Cache-Control: no-cache" -O https://raw.githubusercontent.com/tfelici/RPI-Streamer/develop/install_rpi_streamer.sh?$(date +%s)
   bash install_rpi_streamer.sh --develop
   ```
   
   **Stable version:**
   ```bash
   curl -H "Cache-Control: no-cache" -O https://raw.githubusercontent.com/tfelici/RPI-Streamer/main/install_rpi_streamer.sh?$(date +%s)
   bash install_rpi_streamer.sh --main
   ```

   **Automated deployment (no prompts):**
   ```bash
   curl -H "Cache-Control: no-cache" -O https://raw.githubusercontent.com/tfelici/RPI-Streamer/develop/install_rpi_streamer.sh?$(date +%s)
   bash install_rpi_streamer.sh --develop --daemon
   ```

4. **Configure Flight Settings** via web interface (if using GPS tracking)
5. **Connect hardware** (cellular modem, cameras, etc.) as needed
6. **Use development tools** - Run `rpiconfig` for quick maintenance tasks

## Web Interface Access

After installation, access the control panel at:
- **Local**: `http://[raspberry-pi-ip]/`
- **Remote**: Via configured tunnel or VPN
- **Default login**: No authentication required (configure as needed)

## Documentation

- **[Heartbeat Daemon](HEARTBEAT_DAEMON.md)**: Independent system health monitoring and status reporting
- **[GPS Tracker Guide](GPS_TRACKER_README.md)**: GPS tracking configuration and usage with simulation mode
- **[Flight Settings](FLIGHT_SETTINGS.md)**: GPS username, tracking modes, and vehicle registration
- **[GPS and Cellular Setup](GPS_AND_CELLULAR_SETUP.md)**: Modern GPS and cellular connectivity guide
- **[Multi-Device Setup](MULTI_DEVICE_SETUP.md)**: Central server management and tunneling
- **[WiFi Hotspot](WIFI_HOTSPOT_SETUP.md)**: Standalone access point configuration

## Support & Troubleshooting

### Common Issues
```bash
# Quick system status check
rpiconfig  # Select option 6 for system status

# Individual service checks
sudo systemctl status flask_app.service
sudo systemctl status NetworkManager.service

# View logs
sudo journalctl -u flask_app.service -f
sudo journalctl -u NetworkManager.service -f

# Development tools
rpiconfig  # Interactive menu for maintenance tasks

# Test cellular connectivity
ping -c 3 8.8.8.8

# Check GPS tracking
sudo journalctl -u gps-startup.service -f

# Check modem status
mmcli -L
mmcli -m 0

# Check GPS status (for SIM7600G-H modems)
sudo journalctl -u gps-daemon.service -f
```

## Configuration & Default Settings

### Device-Specific Configuration
The RPI Streamer uses intelligent default settings that adapt to each device:

- **Dynamic Hotspot SSID**: WiFi hotspot names are automatically generated from the device hostname (e.g., "raspberrypi", "streamer-01") ensuring unique network identification for multiple devices
- **Flexible Domain Configuration**: Domain settings start empty, allowing configuration for any flight tracking platform or custom server
- **Hostname-Based Identification**: Each device automatically uses its system hostname for network identification

### Default Settings Structure
New devices are initialized with comprehensive default settings including:
- **Streaming Configuration**: Resolution (1280x720), framerate (30fps), bitrate (768kbps)
- **Audio Settings**: Sample rate (16kHz), bitrate (16k), volume (100%)
- **GPS Tracking**: Manual start mode, platform integration disabled by default
- **WiFi Management**: Client mode default with hostname-based hotspot fallback
- **Hardware Settings**: Auto-detection for video/audio inputs and USB storage

### Multi-Device Deployment
For managing multiple devices:
- Each device maintains unique network identification
- Centralized management through Streamer Admin console
- Consistent default settings across fleet deployments
- Individual device customization through web interface

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
