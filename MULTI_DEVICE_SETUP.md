# RPI Streamer Multi-Device Integration Guide

🎥 **Complete setup guide for managing multiple RPI Streamer devices through your AlmaLinux server**

## 📋 Overview

This setup allows you to:
- ✅ Manage unlimited RPI Streamer devices from one central server
- ✅ Access each device via SSH port forwarding (e.g., `ssh -L 8080:localhost:45001 user@server -p 45002`)
- ✅ Automatic port allocation (no conflicts)
- ✅ Secure reverse SSH tunnels with AutoSSH reliability
- ✅ Hardware registration integration with gyropilots.org
- ✅ Device management through SSH forwarding

## 🏗️ Architecture

```
Your Computer/Laptop
    ↓ SSH Port Forward: ssh -L 8080:localhost:45001 user@server -p 45002
Your AlmaLinux Server
    ↓ Reverse SSH Tunnels (internal ports 45000-70000)
RPI Streamer Devices (home networks)
    ↓ Access via: http://localhost:8080
```

## 🚀 Quick Start

### 1. Server Setup (Run on your AlmaLinux server)

```bash
# No special server setup required beyond SSH access
# The devices will automatically register and create tunnels
# Just ensure SSH service is running:
sudo systemctl enable ssh
sudo systemctl start ssh

# Optional: Monitor active tunnels
netstat -tlnp | grep :450  # Shows active tunnel ports
```

### 2. Device Setup (Run on each RPI Streamer)

```bash
# Download and run the enhanced installer with reverse SSH tunnel setup
curl -sSL https://raw.githubusercontent.com/tfelici/RPI-Streamer/main/install_rpi_streamer.sh | bash -s -- --reverse-ssh

# The installer will prompt you for:
#   - AlmaLinux server hostname/IP
#   - SSH port on your server (default: 22)
#   - SSH username on your server
#   - Confirm auto-generated tunnel ports (recommended: Y)

# The setup automatically:
#   ✅ Generates SSH key pair if needed
#   ✅ Registers device with gyropilots.org hardware console
#   ✅ Adds SSH key to your server's authorized_keys
#   ✅ Creates and starts AutoSSH tunnel service
#   ✅ Saves device configuration locally
```

**What happens automatically:**
- SSH key generation (RSA 4096-bit)
- Hardware ID detection and unique port assignment
- Device registration with the hardware console API
- Secure tunnel establishment with auto-reconnection
- Service configuration and startup

### 3. Accessing Your Devices

```bash
# Each device provides unique tunnel ports (stored in device-info.json)
# Example for accessing a device:

# 1. SSH to your server with port forwarding
ssh -L 8080:localhost:45001 user@your-server.com -p 45002

# 2. Open your browser to: http://localhost:8080
# This gives you direct access to the RPI Streamer web interface

# For multiple devices, use different local ports:
ssh -L 8081:localhost:45003 user@your-server.com -p 45004  # Device 2
ssh -L 8082:localhost:45005 user@your-server.com -p 45006  # Device 3

# Then access: http://localhost:8081, http://localhost:8082, etc.
```

## 📊 Device Management

### Command Line Tools

```bash
# Show all active reverse tunnels on your server
netstat -tlnp | grep :450

# List all registered devices in the hardware console
curl -s "https://gyropilots.org/manage-hardware/"

# Check a specific device's tunnel from server
curl http://localhost:TUNNEL_HTTP_PORT

# Connect directly to a device via SSH
ssh pi@localhost -p TUNNEL_SSH_PORT
```

## 🔧 Configuration Details

### Port Allocation System
- **Range**: 45000-70000 (internal server-side tunnels)
- **Algorithm**: SHA256 hash of hardware ID + 30000 offset
- **Format**: HTTP port = base + 30000, SSH port = base + 30001
- **Collision**: Automatic detection and retry with fallback ports
- **Access**: Via SSH port forwarding (no direct public exposure)

### Device Registration
- **Automatic registration** with gyropilots.org hardware console
- **SSH key management**: Auto-generated RSA 4096-bit keys
- **Hardware ID**: CPU serial or MAC address fallback
- **Database storage**: Device info stored in streamerhardware table
- **Local config**: Device info saved to `/home/pi/device-info.json`

### Access Method
- **SSH Port Forwarding**: No Apache/HTTPS setup required
- **Command**: `ssh -L 8080:localhost:TUNNEL_HTTP_PORT user@server -p TUNNEL_SSH_PORT`
- **Local Access**: Open browser to `http://localhost:8080`
- **Multiple Devices**: Use different local ports (8081, 8082, etc.)

## 📁 File Structure

### On RPI Streamer Devices:
```
/home/pi/
├── device-info.json              # Device registration info
├── tunnel-config.txt             # Tunnel configuration reference
├── .ssh/
│   ├── id_rsa                    # Private key (auto-generated)
│   └── id_rsa.pub                # Public key (sent to server)
└── /etc/systemd/system/
    └── reverse-ssh-tunnel.service # AutoSSH tunnel service
```

### On AlmaLinux Server:
```
~/
├── .ssh/
│   └── authorized_keys           # Contains RPI device public keys
/var/log/
├── auth.log                     # SSH authentication logs
└── rpi-devices/                 # Optional: device-specific logs
```

## 🔐 Security Features

### SSH Security
- **Dedicated SSH keys** per device (RSA 4096-bit)
- **Automatic key management** via hardware console API
- **Restricted tunnel binding** (127.0.0.1 only)
- **AutoSSH reliability** with automatic reconnection
- **Server-side key storage** in authorized_keys with device comments

### Web Security
- **Local access only**: Devices accessed via SSH port forwarding
- **No public exposure**: Tunnel ports bound to 127.0.0.1 only
- **SSH authentication**: Strong RSA 4096-bit key authentication
- **Encrypted tunnels**: All traffic encrypted via SSH

### Access Control
- **SSH-based access**: Only users with SSH access to server can reach devices
- **Key-based authentication**: No password authentication
- **Network isolation**: Devices not directly accessible from internet
- **Port binding**: Tunnels bound to localhost only

## 🛠️ Troubleshooting

### Common Issues

1. **Device not appearing in dashboard**
   ```bash
   # Check tunnel status on device
   sudo systemctl status reverse-ssh-tunnel.service
   
   # Check AutoSSH logs
   journalctl -u reverse-ssh-tunnel.service -f
   
   # Verify port allocation and connection
   cat /home/pi/tunnel-config.txt
   ```

2. **Device registration failed**
   ```bash
   # Check device registration status
   cat /home/pi/device-info.json
   
   # Re-run registration manually
   bash install_rpi_streamer.sh --reverse-ssh
   
   # Check internet connectivity
   ping -c 4 gyropilots.org
   ```

3. **SSH tunnel authentication issues**
   ```bash
   # Verify SSH key exists
   ls -la /home/pi/.ssh/id_rsa*
   
   # Test SSH connection manually
   ssh -i /home/pi/.ssh/id_rsa user@your-server.com -p 22
   
   # Check authorized_keys on server
   cat ~/.ssh/authorized_keys | grep "RPI-Streamer"
   ```

2. **SSL certificate issues**
   ```bash
   # Not applicable - no SSL certificates needed
   # Access is via SSH port forwarding to localhost
   ```

3. **Port forwarding connection issues**
   ```bash
   # Test tunnel from server side
   curl http://localhost:TUNNEL_HTTP_PORT
   
   # Test SSH port forwarding
   ssh -L 8080:localhost:TUNNEL_HTTP_PORT user@server -p TUNNEL_SSH_PORT
   
   # Check if local port is available
   netstat -tlnp | grep :8080
   ```

4. **Automated setup troubleshooting**
   ```bash
   # Check hardware console registration
   curl -s "https://gyropilots.org/manage-hardware/?hardwareid=$(cat /proc/cpuinfo | grep Serial | cut -d ' ' -f 2)"
   
   # Verify tunnel ports are unique
   cat /home/pi/device-info.json | grep -E "(http_port|ssh_port)"
   
   # Test tunnel connectivity from server
   curl http://localhost:TUNNEL_HTTP_PORT
   ```

### Log Locations
- **Device tunnels**: `journalctl -u reverse-ssh-tunnel.service -f`
- **Device installation**: `/var/log/rpi-streamer-install.log`
- **Hardware registration**: Check email notifications to admin
- **SSH authentication**: `/var/log/auth.log` (on server)
- **SSH tunnel logs**: `journalctl -u ssh.service` (on server)

## 🔄 Maintenance

### Daily Tasks (Automated)
- Device tunnel health monitoring via AutoSSH
- SSH key authentication logging
- Hardware console registration status

### Weekly Tasks
```bash
# Check active tunnels on server
netstat -tlnp | grep :450

# Review SSH authentication logs
sudo tail -100 /var/log/auth.log

# Test device connectivity
for port in $(netstat -tlnp | grep :450 | awk '{print $4}' | cut -d: -f2); do
  echo "Testing port $port..."
  curl -s http://localhost:$port || echo "Port $port not responding"
done
```

### Monthly Tasks
```bash
# Update RPI Streamer software (on each device)
sudo systemctl restart install_rpi_streamer.service

# Or manually update each device via SSH tunnel
ssh pi@localhost -p TUNNEL_SSH_PORT "cd /home/pi/flask_app && sudo git pull && sudo systemctl restart flask_app"

# Review security logs
sudo journalctl --since "1 month ago" | grep -i "failed\|error"

# Backup configurations
tar -czf rpi-streamer-backup-$(date +%Y%m%d).tar.gz \
  ~/.ssh/authorized_keys \
  /var/log/auth.log
```

## 🌟 Advanced Features

### Custom Device Access Scripts
```bash
# Create helper scripts for easier device access
# ~/connect-device1.sh:
#!/bin/bash
ssh -L 8080:localhost:45001 user@your-server.com -p 45002

# ~/connect-device2.sh:
#!/bin/bash
ssh -L 8081:localhost:45003 user@your-server.com -p 45004

# Make them executable
chmod +x ~/connect-device*.sh
```

### Multiple Device Access
```bash
# Open multiple SSH tunnels simultaneously in different terminals:

# Terminal 1 - Kitchen Device
ssh -L 8080:localhost:45001 user@server.com -p 45002

# Terminal 2 - Garage Device  
ssh -L 8081:localhost:45003 user@server.com -p 45004

# Terminal 3 - Workshop Device
ssh -L 8082:localhost:45005 user@server.com -p 45006

# Then access:
# http://localhost:8080 - Kitchen Device
# http://localhost:8081 - Garage Device
# http://localhost:8082 - Workshop Device
```

### Monitoring Integration
```bash
# Check tunnel status from server
netstat -tlnp | grep :450

# Monitor device connectivity
for port in $(netstat -tlnp | grep :450 | awk '{print $4}' | cut -d: -f2); do
  curl -s http://localhost:$port/_health 2>/dev/null || echo "Port $port offline"
done
```

## 📱 Mobile Access

### SSH Client Apps
- **iOS**: Termius, SSH Files, or similar SSH client apps
- **Android**: JuiceSSH, Termux, or ConnectBot
- Set up port forwarding in your SSH client app
- Access via device browser after establishing tunnel

### Browser Bookmarks
```bash
# After establishing SSH tunnel, bookmark these URLs:
# http://localhost:8080 - Device 1
# http://localhost:8081 - Device 2  
# http://localhost:8082 - Device 3
```

## 🤝 Support

### Community Resources
- **Documentation**: [GitHub Repository](https://github.com/tfelici/RPI-Streamer)
- **Issues**: [GitHub Issues](https://github.com/tfelici/RPI-Streamer/issues)
- **Hardware Console**: [gyropilots.org/manage-hardware](https://gyropilots.org/manage-hardware)

### Getting Help
1. Check the troubleshooting section above
2. Review device logs: `journalctl -u reverse-ssh-tunnel.service -f`
3. Test connectivity: `curl -v http://localhost:TUNNEL_HTTP_PORT`
4. Check device registration: `cat /home/pi/device-info.json`
5. Create GitHub issue with logs and hardware ID

---

**🎉 Congratulations!** You now have a complete multi-device RPI Streamer management system using secure SSH tunnels. Each device is accessible via SSH port forwarding, providing secure remote access without exposing devices directly to the internet.

**Next Steps:**
1. Add more devices using the installer with `--reverse-ssh` flag
2. Create helper scripts for easier device connections
3. Set up SSH client apps on mobile devices for remote access
4. Monitor tunnel health and device connectivity

**Access Summary:**
- **Server**: SSH to your AlmaLinux server
- **Tunnels**: Each device creates reverse SSH tunnels automatically  
- **Access**: Use SSH port forwarding to reach device web interfaces
- **Security**: All traffic encrypted, no public device exposure

*Happy streaming! 📹*
