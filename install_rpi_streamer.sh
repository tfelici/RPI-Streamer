#!/bin/bash
# RPI Streamer installation script
# Usage: bash install_rpi_streamer.sh [--tailscale] [--sim7600]

# This script installs the RPI Streamer Flask app and MediaMTX on a Raspberry Pi running Raspberry Pi OS Lite.
# It also sets up a systemd service for the Flask app and MediaMTX, and installs Tailscale for remote access.
# Optional: --sim7600 sets up SIM7600G-H 4G dongle for internet connectivity
################################################

set -e

# Wait for internet connectivity (max 20 seconds)
for i in {1..20}; do
    if ping -c 1 github.com &>/dev/null; then
    echo "Internet is up."
    break
    fi
    echo "Waiting for internet connection..."
    sleep 1
done

# If still no connection, exit with error
if ! ping -c 1 github.com &>/dev/null; then
    echo "No internet connection after 20 seconds, aborting install."
    exit 1
fi

# Update and install dependencies
sudo apt-get update -y
sudo apt-get upgrade -y
sudo apt-get install ffmpeg -y
sudo apt-get install v4l-utils alsa-utils -y
sudo apt-get install python3 python3-pip -y
sudo apt-get install python3-flask -y
sudo apt-get install python3-psutil -y
sudo apt-get install python3-requests -y
sudo apt-get install python3-werkzeug -y
sudo apt-get install gunicorn python3-gevent python3-requests-toolbelt -y
sudo apt-get install python3-pymediainfo -y
sudo apt-get install mediainfo -y

# GPS Tracker dependencies
sudo apt-get install python3-serial -y
sudo apt-get install python3-rpi.gpio -y

# SIM7600G-H 4G dongle dependencies (installed always for potential use)
sudo apt-get install minicom screen ppp usb-modeswitch usb-modeswitch-data -y

# WiFi hotspot dependencies (for hotspot mode functionality)
sudo apt-get install hostapd dnsmasq -y

#also add gstreamer dependencies
sudo apt install gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav gstreamer1.0-alsa gstreamer1.0-pulseaudio -y
sudo apt-get install python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-gst-plugins-base-1.0 gir1.2-gstreamer-1.0 -y

#create symbolic link for python3 to python if it doesn't exist
if [ ! -L /usr/bin/python ]; then
    echo "Creating symbolic link for python3 to python..."
    sudo ln -s /usr/bin/python3 /usr/bin/python
else
    echo "Symbolic link for python3 to python already exists."
fi


# Setup flask app directory
echo "Setting up Flask app directory... $HOME/flask_app"
mkdir -p "$HOME/flask_app"
mkdir -p "$HOME/streamerData"
cd "$HOME/flask_app"


#     Force update the codebase to match the remote GitHub repository (overwriting local changes, restoring missing files, removing extra tracked files), fix permissions, and restart services.
#     This is useful if the codebase has been modified locally and you want to reset it to the latest version from the remote repository.
#     If the repository is not already cloned, it will clone it.
echo "Updating RPI Streamer codebase..."

# Check if git is installed
if ! command -v git &> /dev/null; then
    echo "Git not found, installing..."
    sudo apt-get install git -y
fi

# If repository exists, force update it
# If not, clone it fresh
if [ -d .git ]; then
    sudo git fetch --all
    sudo git reset --hard origin/main
    sudo git clean -f -d
else
    rm -rf *
    echo "Repository not found, cloning fresh copy..."
    sudo git clone https://github.com/tfelici/RPI-Streamer.git .
fi
#change ownership of the flask_app directory to the current user
sudo chown -R "$USER":"$USER" "$HOME/flask_app"
#this command is needed to allow any users to run git in the flask_app directory
sudo git config --global --add safe.directory "$HOME/flask_app"
echo "Repository update complete"

# Check if the app.py file exists
if [ ! -f app.py ]; then
    echo "Error: app.py file not found in the Flask app directory."
    exit 1
fi
# Check if the flask_app directory is writable
if [ ! -w "$HOME/flask_app" ]; then
    echo "Error: Flask app directory is not writable."
    exit 1
fi

#download the executables directory from the Streamer-Uploader repository
#only download if the executables have changed or don't exist
if [ ! -d "$HOME/executables" ]; then
    mkdir -p "$HOME/executables"
fi

# Function to check and download executable if needed
# This function handles both SHA checking and downloading in one place
# Returns 0 on success (up to date or downloaded), 1 on failure (but doesn't exit)

# Check if jq is installed, if not, install it (needed for version comparison)
if ! command -v jq &> /dev/null; then
    sudo apt-get install jq -y
fi

check_and_download_executable() {
    local platform=$1
    local filename=$2
    local api_path=$3
    local download_url=$4
    local local_file="$HOME/executables/$filename"
    local sha_file="$HOME/executables/${filename}.sha"
    
    printf "Checking %s executable...\n" "$platform"
    
    # Get remote file SHA from GitHub API
    remote_sha=$(curl -s "https://api.github.com/repos/tfelici/Streamer-Uploader/contents/$api_path" | jq -r ".sha")
    
    if [ -z "$remote_sha" ] || [ "$remote_sha" = "null" ]; then
    echo "Warning: Could not fetch remote SHA for $platform executable. Remote file may not exist or API is unavailable."
    echo "Skipping $platform executable update check."
    return 0  # Return success to continue gracefully
    fi
    
    # Check if local file exists and has a stored SHA
    local need_download=false
    if [ -f "$local_file" ] && [ -f "$sha_file" ]; then
    # Read the stored SHA
    stored_sha=$(cat "$sha_file" 2>/dev/null || echo "")
        
    if [ -z "$stored_sha" ]; then
        echo "No stored SHA found for $platform executable, will download."
        need_download=true
    elif [ "$stored_sha" = "$remote_sha" ]; then
        echo "$platform executable is up to date."
        return 0  # Already up to date
    else
        echo "$platform executable needs update (stored: ${stored_sha:0:7}, remote: ${remote_sha:0:7})."
        need_download=true
    fi
    else
    echo "$platform executable not found, will download."
    need_download=true
    fi
    
    # Download if needed
    if [ "$need_download" = "true" ]; then
    printf "Downloading %s executable...\n" "$platform"
    if curl -H "Cache-Control: no-cache" -L "$download_url?$(date +%s)" -o "$local_file"; then
        echo "$platform executable downloaded successfully."
        # Store the remote SHA for future comparisons
        echo "$remote_sha" > "$sha_file"
        return 0  # Success
    else
        echo "Warning: Failed to download $platform executable. This may be due to network issues or the file not existing."
        echo "Continuing with installation..."
        return 0  # Return success to continue gracefully
    fi
    fi
}

# Download the StreamerUploader executables for different platforms
printf "Checking StreamerUploader executables...\n"

# Check and download Windows executable
check_and_download_executable "Windows" "Uploader-windows.exe" "windows/dist/StreamerUploader.exe" "https://github.com/tfelici/Streamer-Uploader/raw/main/windows/dist/StreamerUploader.exe"

# Check and download macOS executable
check_and_download_executable "macOS" "Uploader-macos" "macos/dist/StreamerUploader" "https://github.com/tfelici/Streamer-Uploader/raw/main/macos/dist/StreamerUploader"

# Check and download Linux executable
check_and_download_executable "Linux" "Uploader-linux" "linux/dist/StreamerUploader" "https://github.com/tfelici/Streamer-Uploader/raw/main/linux/dist/StreamerUploader"

printf "StreamerUploader executable check completed.\n"

# Make the downloaded linux and macos executables executable
[ -f "$HOME/executables/Uploader-linux" ] && chmod +x "$HOME/executables/Uploader-linux"
[ -f "$HOME/executables/Uploader-macos" ] && chmod +x "$HOME/executables/Uploader-macos"
# search and install latest version of mediamtx
#only install MediaMTX if it does not exist or is not the latest version
printf "Checking for existing MediaMTX installation...\n"

# Get the latest version from GitHub API
printf "Checking latest MediaMTX version...\n"
latest_version=$(curl -s https://api.github.com/repos/bluenviron/mediamtx/releases/latest | jq -r ".tag_name")
if [ -z "$latest_version" ]; then
    echo "Error: Could not fetch latest MediaMTX version from GitHub."
    exit 1
fi
printf "Latest MediaMTX version: %s\n" "$latest_version"

# Check if MediaMTX exists and get its version
NEED_INSTALL=false
if [ -f "$HOME/mediamtx" ]; then
    echo "MediaMTX binary found, checking version..."
    # Check if the mediamtx binary is executable
    if [ ! -x "$HOME/mediamtx" ]; then
    echo "MediaMTX binary is not executable, will reinstall."
    NEED_INSTALL=true
    else
    # Get current version
    current_version=$("$HOME/mediamtx" --version 2>/dev/null | head -n1 | grep -o 'v[0-9][0-9.]*' || echo "unknown")
    printf "Current MediaMTX version: %s\n" "$current_version"
        
    if [ "$current_version" = "$latest_version" ]; then
        echo "MediaMTX is already up to date ($current_version)."
        NEED_INSTALL=false
    else
        echo "MediaMTX needs update from $current_version to $latest_version."
        NEED_INSTALL=true
    fi
    fi
else
    echo "MediaMTX is not installed, proceeding with installation."
    NEED_INSTALL=true
fi

# Only proceed with download if we need to install/update
if [ "$NEED_INSTALL" = "true" ]; then
    # Determine the latest MediaMTX release URL based on system architecture
    printf "Detecting system architecture...\n"
    ARCH=$(uname -m)
    if [[ "$ARCH" == "aarch64"* || "$ARCH" == arm* ]]; then
    MTX_SUFFIX="linux_arm64.tar.gz"
    else
    MTX_SUFFIX="linux_amd64.tar.gz"
    fi
    printf "Searching for the latest MediaMTX release for $MTX_SUFFIX...\n"
    
    latest_url=$(curl -s https://api.github.com/repos/bluenviron/mediamtx/releases/latest | jq -r ".assets[] | select(.browser_download_url | endswith(\"$MTX_SUFFIX\")) | .browser_download_url")
    printf "Latest MediaMTX URL: %s\n" "$latest_url"
    if [ -z "$latest_url" ]; then
    echo "Error: Could not find the latest MediaMTX release URL."
    exit 1
    fi
    cd "$HOME"
    wget "$latest_url"
    exit_code=$?
    if [ $exit_code -ne 0 ]; then
    echo "Error: Failed to download MediaMTX."
    exit $exit_code
    fi
    printf "Extracting MediaMTX...\n"
    # Extract the downloaded file
    if [ ! -f "$(basename "$latest_url")" ]; then
    echo "Error: Downloaded file not found."
    exit 1
    fi
    # Ensure the file is a tar.gz file
    if [[ "$(basename "$latest_url")" != *.tar.gz ]]; then
    echo "Error: Downloaded file is not a tar.gz file."
    exit 1
    fi
    tar xvf $(basename "$latest_url")
    chmod +x mediamtx
    #delete the downloaded file
    rm $(basename "$latest_url")
    # Check if the mediamtx binary exists
    if [ ! -f mediamtx ]; then
    echo "Error: MediaMTX binary not found after extraction."
    exit 1
    fi
    echo "MediaMTX installation completed successfully."
else
    echo "Skipping MediaMTX download - already up to date."
fi
#mv mediamtx.yml flask_app

# Create systemd service for flask app - this must run after the install_rpi_streamer.service
printf "Creating systemd service for Flask app...\n"
sudo tee /etc/systemd/system/flask_app.service >/dev/null << EOF
[Unit]
Description=RPI Streamer
After=network.target
[Service]
User=root
WorkingDirectory=$HOME/flask_app
Environment="OWNER=$USER:$USER"
#ExecStart=/usr/bin/python3 app.py
ExecStart=/usr/bin/gunicorn -w 4 -k gevent --graceful-timeout 1 -b 0.0.0.0:80 app:app
Restart=always
[Install]
WantedBy=multi-user.target
EOF

# Create systemd service for mediamtx - this must run after the install_rpi_streamer.service
printf "Creating systemd service for MediaMTX...\n"
sudo tee /etc/systemd/system/mediamtx.service >/dev/null << EOF
[Unit]
Description=MediaMTX Streaming Server
After=network.target
[Service]
User=root
WorkingDirectory=$HOME/flask_app
ExecStart=$HOME/mediamtx $HOME/flask_app/mediamtx.yml
Restart=always
[Install]
WantedBy=multi-user.target
EOF

# Install GPS Startup Manager Service
printf "?? Installing GPS Startup Manager Service...\n"

# Check if required GPS files exist
GPS_STARTUP_SCRIPT="$HOME/flask_app/gps_startup_manager.py"
GPS_SERVICE_FILE="$HOME/flask_app/gps-startup.service"

if [ ! -f "$GPS_STARTUP_SCRIPT" ]; then
    echo "?? Warning: GPS startup script not found: $GPS_STARTUP_SCRIPT"
    echo "GPS startup functionality will not be available until the script is present."
else
    # Make the GPS startup script executable
    chmod +x "$GPS_STARTUP_SCRIPT"
    echo "? Made GPS startup script executable"
    
    # Create the GPS startup service
    printf "Creating systemd service for GPS Startup Manager...\n"
    sudo tee /etc/systemd/system/gps-startup.service >/dev/null << EOF
[Unit]
Description=GPS Startup Manager for RPI Streamer
After=network.target
Wants=network.target

[Service]
Type=simple
User=$USER
Group=$USER
WorkingDirectory=$HOME/flask_app
ExecStart=/usr/bin/python3 $HOME/flask_app/gps_startup_manager.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    
    echo "? GPS Startup Manager service installed"
    echo "?? GPS Service Info:"
    echo "   � Service will be enabled/disabled automatically based on Flight Settings"
    echo "   � Configure GPS start mode in the Flight Settings page"
    echo "   � Manual control: sudo systemctl status gps-startup.service"
fi

#create a systemd service for this script
printf "Creating systemd service for this script...\n"
sudo tee /etc/systemd/system/install_rpi_streamer.service >/dev/null << EOF
[Unit]
Description=RPI Streamer Installation Script
After=network-online.target
Wants=network-online.target
[Service]
User=$USER
Type=oneshot
ExecStart=/usr/bin/curl -H "Cache-Control: no-cache" -L -o $HOME/flask_app/install_rpi_streamer.sh "https://raw.githubusercontent.com/tfelici/RPI-Streamer/main/install_rpi_streamer.sh?$(date +%s)"
ExecStartPost=/bin/bash -e $HOME/flask_app/install_rpi_streamer.sh
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable install_rpi_streamer
sudo systemctl enable flask_app
sudo systemctl restart flask_app
sudo systemctl enable mediamtx
sudo systemctl restart mediamtx

# Note: GPS startup service is installed but not enabled by default
# It will be automatically enabled/disabled based on Flight Settings configuration
echo "?? GPS Startup Service: Available but not enabled (configure via Flight Settings)"

# Function to setup SIM7600G-H 4G dongle internet connectivity
setup_sim7600_internet() {
    echo ""
    echo "=========================================="
    echo "📡 SIM7600G-H 4G DONGLE Internet Setup"
    echo "=========================================="
    echo ""
    
    # Always install the basic dependencies and drivers
    echo "📦 Installing SIM7600G-H dependencies and drivers..."
    
    # Install required packages for SIM7600 support
    sudo apt-get update -qq
    sudo apt-get install -y usb-modeswitch usb-modeswitch-data minicom screen ppp
    
    echo "✅ SIM7600G-H dependencies installed"
    
    # Create the auto-startup service regardless of hardware presence
    echo "🔧 Creating SIM7600G-H auto-startup service..."
    sudo tee /etc/systemd/system/sim7600-internet.service >/dev/null << 'EOFSERVICE'
[Unit]
Description=SIM7600G-H 4G Internet Connection
After=network.target
Wants=network.target

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'sleep 30 && if lsusb | grep -q "1e0e:9011"; then if ip link show | grep -q "usb0"; then ip link set usb0 up && timeout 30 dhclient -v usb0; elif ip link show | grep -q "eth1"; then ip link set eth1 up && timeout 30 dhclient -v eth1; fi; fi'
RemainAfterExit=yes
User=root

[Install]
WantedBy=multi-user.target
EOFSERVICE
    
    sudo systemctl daemon-reload
    sudo systemctl enable sim7600-internet.service
    
    echo "✅ SIM7600G-H service created and enabled"
    
    # Check if the dongle is currently connected for immediate setup
    echo "🔍 Checking for SIM7600G-H dongle..."
    DONGLE_DETECTED=false
    
    if lsusb | grep -i "1e0e:9001\|1e0e:9011"; then
    echo "✅ SIM7600G-H dongle detected!"
    DONGLE_DETECTED=true
    else
    echo "ℹ️  SIM7600G-H dongle not currently detected"
    echo "   📋 The system is now configured to automatically"
    echo "      connect when the dongle is plugged in later."
    echo ""
    echo "   🔌 To use the dongle:"
    echo "   1. Insert SIM card into the dongle"
    echo "   2. Connect dongle to USB port"
    echo "   3. Wait 30-60 seconds for auto-configuration"
    echo "   4. Check connection: ip addr show usb0"
    echo ""
    echo "✅ SIM7600G-H setup completed (hardware will auto-configure when connected)"
    return 0
    fi
    
    # If dongle is detected, proceed with immediate configuration
    if [ "$DONGLE_DETECTED" = true ]; then
    # Check for available ttyUSB ports
    echo "🔍 Checking USB serial ports..."
    USB_PORTS=$(ls /dev/ttyUSB* 2>/dev/null || echo "")
    if [ -z "$USB_PORTS" ]; then
        echo "⚠️  No ttyUSB ports found!"
        echo "   The dongle may not be properly recognized."
        echo "   Try disconnecting and reconnecting the dongle."
        echo "   ℹ️  Auto-configuration will work when dongle is ready."
        return 0
    fi
        
    echo "✅ Found USB ports: $USB_PORTS"
        
    # Determine the AT command port (usually ttyUSB2)
    AT_PORT="/dev/ttyUSB2"
    if [ ! -e "$AT_PORT" ]; then
        echo "⚠️  ttyUSB2 not found, trying ttyUSB1..."
        AT_PORT="/dev/ttyUSB1"
        if [ ! -e "$AT_PORT" ]; then
            echo "⚠️  ttyUSB1 not found, trying ttyUSB0..."
            AT_PORT="/dev/ttyUSB0"
            if [ ! -e "$AT_PORT" ]; then
                echo "❌ No suitable AT command port found!"
                echo "   ℹ️  Auto-configuration will work when dongle is ready."
                return 0
            fi
        fi
    fi
        
    echo "📱 Using AT command port: $AT_PORT"
        
    # Function to send AT command using screen
    send_at_screen() {
        local command="$1"
        echo "📤 Sending AT command: $command"
            
        # Create a screen session and send the command
        screen -dm -S sim7600_at "$AT_PORT" 115200
        sleep 2
        screen -S sim7600_at -p 0 -X stuff "$command^M"
        sleep 3
        screen -S sim7600_at -X quit 2>/dev/null || true
    }
        
    echo "🔧 Configuring SIM7600G-H for RNDIS networking..."
        
    # Switch to RNDIS mode (9011)
    send_at_screen "AT+CUSBPIDSWITCH=9011,1,1"
        
    echo "⏳ Waiting for module to restart (30 seconds)..."
    sleep 30
        
    # Check if usb0 interface appeared
    echo "🔍 Checking for USB network interface..."
    if ip link show | grep -q "usb0"; then
        echo "✅ usb0 interface detected!"
            
        # Bring up the interface
        echo "🌐 Bringing up usb0 interface..."
        sudo ip link set usb0 up
            
        # Get IP address via DHCP
        echo "📡 Requesting IP address via DHCP..."
        timeout 30 sudo dhclient -v usb0 2>&1 | head -20 || echo "DHCP timeout"
            
        # Check if we got an IP
        USB0_IP=$(ip addr show usb0 | grep "inet " | awk '{print $2}' | cut -d/ -f1)
        if [ -n "$USB0_IP" ]; then
            echo "✅ Successfully obtained IP address: $USB0_IP"
                
            # Test internet connectivity
            echo "🌍 Testing internet connectivity..."
            if timeout 10 ping -c 3 8.8.8.8 >/dev/null 2>&1; then
                echo "✅ Internet connection successful!"
                echo "✅ SIM7600G-H internet setup completed successfully!"
                echo "   Interface: usb0"
                echo "   IP Address: $USB0_IP"
                return 0
            else
                echo "⚠️  Got IP but no internet connectivity"
                echo "   This might be due to APN settings or network registration"
            fi
        else
            echo "⚠️  Interface is up but no IP address obtained"
            echo "   Try manually: sudo dhclient -v usb0"
        fi
            
    elif ip link show | grep -q "eth1"; then
        echo "✅ eth1 interface detected (alternative naming)!"
        ETH_IFACE="eth1"
            
        # Similar setup for eth1
        sudo ip link set $ETH_IFACE up
        timeout 30 sudo dhclient -v $ETH_IFACE || echo "DHCP timeout"
            
        ETH_IP=$(ip addr show $ETH_IFACE | grep "inet " | awk '{print $2}' | cut -d/ -f1)
        if [ -n "$ETH_IP" ]; then
            echo "✅ Successfully obtained IP address: $ETH_IP"
            echo "✅ SIM7600G-H internet setup completed successfully!"
            return 0
        fi
    else
        echo "❌ No USB network interface found!"
        echo "   The dongle may need more time or manual configuration."
        echo "   ℹ️  Auto-configuration will retry on next boot."
    fi
    fi
}

# Install SIM7600G-H internet setup if specified in command line arguments
if [[ "$@" == *"--sim7600"* ]]; then
    setup_sim7600_internet
    SIM7600_STATUS=$?
else
    echo "Skipping SIM7600G-H 4G dongle setup. Use --sim7600 flag to enable."
    SIM7600_STATUS=2
fi

# Install tailscale if the specified in the command line arguments
if [[ "$@" == *"--tailscale"* ]]; then
    echo "Installing Tailscale..."
    curl -fsSL https://tailscale.com/install.sh | sh
    sudo tailscale up
else
    echo "Skipping Tailscale installation."
fi

# Print completion message
echo "?? RPI Streamer installation completed successfully!"
echo ""
echo "?? Access your RPI Streamer at: http://$(hostname -I | awk '{print $1}')"
echo "?? New Flight Settings available for GPS tracking configuration"
echo "??  Configure GPS username and tracking modes in Flight Settings"
echo ""
echo "?? Services installed:"
echo "   � Flask App (enabled & running)"
echo "   � MediaMTX (enabled & running)" 
echo "   � GPS Startup Manager (available, configure via web interface)"

# Add SIM7600 status to completion message
if [ "$SIM7600_STATUS" -eq 0 ]; then
    echo "   � SIM7600G-H Internet (enabled & configured)"
elif [ "$SIM7600_STATUS" -eq 1 ]; then
    echo "   � SIM7600G-H Internet (attempted, check manually)"
else
    echo "   � SIM7600G-H Internet (not configured, use --sim7600 to enable)"
fi

echo ""
echo "?? Optional setup flags:"
echo "   --tailscale  : Install Tailscale for remote access"
echo "   --sim7600    : Configure SIM7600G-H 4G dongle internet"
echo ""
echo "?? Documentation:"
echo "   GPS Tracker: GPS_TRACKER_README.md"
echo "   SIM7600 Setup: SIM7600_INTERNET_SETUP.md"
