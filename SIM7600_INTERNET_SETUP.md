# SIM7600G-H 4G/LTE Internet Setup Guide v3.00

## Overview

The RPI Streamer v3.00 includes comprehensive support for the SIM7600G-H 4G/LTE modem, providing reliable cellular internet connectivity for remote operations. The system integrates with ModemManager for automatic carrier detection and network registration.

## Hardware Requirements

### Compatible Modems
- **SIM7600G-H**: Primary supported 4G/LTE modem with GPS capabilities
- **Other SIM7600 Series**: Most SIM7600 variants are supported
- **USB Interface**: Standard USB connection to Raspberry Pi
- **SIM Card**: Active cellular data plan with your carrier

### Installation
1. **Physical Connection**: Connect SIM7600G-H to Raspberry Pi via USB
2. **SIM Card**: Insert activated SIM card into modem
3. **Power**: Ensure adequate power supply (recommended: 3A+ power adapter)
4. **Antenna**: Connect cellular antenna for optimal signal reception

## Automatic Setup

### ModemManager Integration
The RPI Streamer installation automatically configures cellular internet:

```bash
# Cellular support is included in standard installation
bash install_rpi_streamer.sh --develop
```

The installation automatically:
- Installs ModemManager and related dependencies
- Configures automatic carrier detection
- Sets up NetworkManager integration
- Enables automatic connection on boot
- Configures GPS access for supported modems

### Service Management
Cellular connectivity is managed by system services:

```bash
# Check modem status
sudo mmcli -L

# View connection status
sudo mmcli -m 0

# Check NetworkManager connections
nmcli connection show
```

## Manual Configuration

### Carrier APN Settings
Most carriers are detected automatically, but manual configuration may be needed:

```bash
# Create connection for specific carrier
sudo nmcli connection add type gsm ifname '*' con-name cellular \
    connection.autoconnect yes \
    gsm.apn "your-carrier-apn"

# For carriers requiring username/password
sudo nmcli connection modify cellular \
    gsm.username "your-username" \
    gsm.password "your-password"

# Activate connection
sudo nmcli connection up cellular
```

### Common Carrier APNs
- **Verizon**: `vzwinternet`
- **AT&T**: `broadband` or `phone`
- **T-Mobile**: `fast.t-mobile.com`
- **Three UK**: `3internet`
- **Vodafone UK**: `internet`

### Testing Connection
```bash
# Check if modem is detected
lsusb | grep -i sim

# Verify ModemManager sees the modem
sudo mmcli -L

# Check signal strength and carrier
sudo mmcli -m 0 --signal-get

# Test internet connectivity
ping -c 3 8.8.8.8
```

## GPS Integration

### SIM7600G-H GPS Features
The SIM7600G-H includes integrated GPS capabilities:

- **Automatic Detection**: GPS is automatically detected and configured
- **Multi-GNSS Support**: GPS + GLONASS + Galileo + BeiDou
- **High Accuracy**: Professional-grade positioning accuracy
- **Integrated Antenna**: Uses same antenna for cellular and GPS (optional external GPS antenna)

### GPS Configuration
GPS functionality is automatically configured with RPI Streamer:

```bash
# GPS daemon automatically detects SIM7600G-H GPS
# No manual configuration required

# Check GPS status
sudo systemctl status gps-daemon.service

# View GPS coordinates
curl http://localhost:5000/api/gps-status
```

## Troubleshooting

### Common Issues

#### Modem Not Detected
```bash
# Check USB connection
lsusb | grep -i sim

# Restart ModemManager
sudo systemctl restart ModemManager

# Check kernel messages
dmesg | grep -i sim
```

#### No Internet Connection
```bash
# Check modem status
sudo mmcli -m 0

# Verify APN settings
nmcli connection show cellular

# Restart connection
sudo nmcli connection down cellular
sudo nmcli connection up cellular
```

#### Poor Signal Quality
- **Antenna Placement**: Ensure antenna has clear view of sky
- **Antenna Connection**: Verify antenna is properly connected
- **Location**: Move to area with better cellular coverage
- **Signal Check**: `sudo mmcli -m 0 --signal-get`

### Advanced Diagnostics
```bash
# Detailed modem information
sudo mmcli -m 0 --output-keyvalue

# Network registration status
sudo mmcli -m 0 --3gpp-scan

# View system logs
journalctl -u ModemManager -f
```

## Network Prioritization

### Connection Priority
RPI Streamer automatically prioritizes internet connections:

1. **Ethernet**: Highest priority for stability
2. **WiFi**: Medium priority for local networks
3. **Cellular**: Backup connectivity for remote areas

### Manual Priority Control
```bash
# Set cellular as primary
sudo nmcli connection modify cellular connection.autoconnect-priority 10

# Set WiFi as backup
sudo nmcli connection modify "WiFi-Network" connection.autoconnect-priority 5
```

## Power Management

### Power Consumption
- **Active**: ~2-3W during data transmission
- **Idle**: ~0.5-1W when connected but idle
- **Standby**: ~0.1W when modem is off

### Power Optimization
```bash
# Enable power saving mode
sudo mmcli -m 0 --set-power-state-low

# Disable when not needed
sudo mmcli -m 0 --disable
```

## Security Considerations

### VPN Recommendations
For secure remote access over cellular, configure VPN separately after installation:

```bash
# Install RPI Streamer first
bash install_rpi_streamer.sh --develop
# Then configure VPN services as needed
```

### Firewall Configuration
The installation automatically configures basic firewall rules for cellular connections.

## Data Usage Monitoring

### Built-in Monitoring
RPI Streamer includes basic data usage tracking in the system diagnostics dashboard.

### Manual Monitoring
```bash
# Check data usage
sudo mmcli -m 0 --bearer-list
sudo mmcli -b 0  # Replace 0 with bearer ID

# Monitor real-time usage
watch -n 1 'cat /sys/class/net/wwan0/statistics/rx_bytes /sys/class/net/wwan0/statistics/tx_bytes'
```