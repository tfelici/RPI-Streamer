#!/bin/bash
# RPI Streamer installation script
# Usage: bash install_rpi_streamer.sh [--tailscale]

# This script installs the RPI Streamer Flask app and MediaMTX on a Raspberry Pi running Raspberry Pi OS Lite.
# It also sets up a systemd service for the Flask app and MediaMTX, and installs Tailscale for remote access.
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

# Check if jq is installed, if not, install it (needed for version comparison)
if ! command -v jq &> /dev/null; then
    sudo apt-get install jq -y
fi

# Get the latest version from GitHub API
printf "Checking latest MediaMTX version...\n"
latest_version=$(curl -s https://api.github.com/repos/bluenviron/mediamtx/releases/latest | jq -r ".tag_name")
if [ -z "$latest_version" ] || [ "$latest_version" = "null" ]; then
    echo "Warning: Could not fetch latest MediaMTX version from GitHub (network error or rate limit exceeded)."
    echo "Skipping MediaMTX update check - will use existing installation if available."
    NEED_INSTALL=false
    # Check if MediaMTX binary exists, if not we still need to install
    if [ ! -f "$HOME/mediamtx" ]; then
        echo "MediaMTX binary not found and cannot check for updates. Installation will be skipped."
        echo "Please run the script again later when GitHub API is available."
    else
        echo "Existing MediaMTX installation will be used."
    fi
else
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
fi

# Only proceed with download if we need to install/update and have a valid latest_version
if [ "$NEED_INSTALL" = "true" ] && [ -n "$latest_version" ] && [ "$latest_version" != "null" ]; then
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
    if [ -z "$latest_url" ] || [ "$latest_url" = "null" ]; then
        echo "Warning: Could not find the latest MediaMTX release URL (API may be unavailable)."
        echo "Skipping MediaMTX installation/update."
    else
        cd "$HOME"
        wget "$latest_url"
        exit_code=$?
        if [ $exit_code -ne 0 ]; then
            echo "Warning: Failed to download MediaMTX."
            echo "Continuing with existing installation if available."
        else
            printf "Extracting MediaMTX...\n"
            # Extract the downloaded file
            if [ ! -f "$(basename "$latest_url")" ]; then
                echo "Error: Downloaded file not found."
            elif [[ "$(basename "$latest_url")" != *.tar.gz ]]; then
                echo "Error: Downloaded file is not a tar.gz file."
                rm "$(basename "$latest_url")" 2>/dev/null || true
            else
                tar xvf $(basename "$latest_url")
                chmod +x mediamtx
                #delete the downloaded file
                rm $(basename "$latest_url")
                # Check if the mediamtx binary exists
                if [ ! -f mediamtx ]; then
                    echo "Error: MediaMTX binary not found after extraction."
                else
                    echo "MediaMTX installation completed successfully."
                fi
            fi
        fi
    fi
elif [ "$NEED_INSTALL" = "true" ]; then
    echo "Skipping MediaMTX installation - cannot check latest version due to API unavailability."
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

# Install tailscale if the specified in the command line arguments
if [[ "$@" == *"--tailscale"* ]]; then
    echo "Installing Tailscale..."
    curl -fsSL https://tailscale.com/install.sh | sh
    sudo tailscale up
else
    echo "Skipping Tailscale installation."
fi

# Optimize WiFi connection speed for faster hotspot detection
printf "Optimizing WiFi connection settings for faster hotspot detection...\n"

# Create NetworkManager configuration for faster WiFi scanning and connection
sudo tee /etc/NetworkManager/conf.d/99-wifi-optimization.conf >/dev/null << 'EOF'
[main]
# Faster WiFi scanning and connection settings
no-auto-default=*

[connection]
# Faster connection attempts
connection.autoconnect-retries=0
ipv4.dhcp-timeout=10
ipv6.dhcp-timeout=10

[device]
# Aggressive WiFi scanning for faster detection of available networks
wifi.scan-rand-mac-address=no
wifi.powersave=2

[connectivity]
# Faster connectivity checking
uri=http://nmcheck.gnome.org/check_network_status.txt
interval=10
EOF

# Configure systemd-networkd to not interfere with NetworkManager
sudo systemctl disable systemd-networkd 2>/dev/null || true
sudo systemctl stop systemd-networkd 2>/dev/null || true

# Ensure NetworkManager starts early in the boot process and scans immediately
sudo systemctl enable NetworkManager
sudo systemctl enable NetworkManager-wait-online

# Set NetworkManager to start scanning WiFi immediately on boot
sudo tee /etc/systemd/system/wifi-fast-scan.service >/dev/null << 'EOF'
[Unit]
Description=Fast WiFi Scanning Service
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=oneshot
ExecStart=/usr/bin/nmcli device wifi rescan
ExecStartPost=/bin/sleep 2
ExecStartPost=/usr/bin/nmcli connection up id $(nmcli -t -f NAME connection show --active | head -1) 2>/dev/null || true
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable wifi-fast-scan.service

# Create enhanced WiFi reconnection monitoring service for automatic reconnection on disconnect
sudo tee /etc/systemd/system/wifi-reconnect.service >/dev/null << 'EOF'
[Unit]
Description=Enhanced WiFi Reconnection Monitor
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=simple
Restart=always
RestartSec=5
ExecStart=/bin/bash -c '
# Enhanced WiFi reconnection logic with improved detection and recovery
RECONNECT_LOG="/var/log/wifi-reconnect.log"
LAST_SSID_FILE="/tmp/last_connected_ssid"
RECONNECT_ATTEMPTS=0
MAX_RECONNECT_ATTEMPTS=10
SCAN_INTERVAL=15
CONNECTED_CHECK_INTERVAL=20

# Function to log with timestamp
log_message() {
    echo "$(date): $1" | tee -a "$RECONNECT_LOG"
}

# Function to check if internet is actually working
check_internet() {
    # Try multiple methods to verify internet connectivity
    if ping -c 1 -W 3 8.8.8.8 >/dev/null 2>&1 || \
       ping -c 1 -W 3 1.1.1.1 >/dev/null 2>&1 || \
       curl -s --connect-timeout 5 http://captive.apple.com >/dev/null 2>&1; then
        return 0
    else
        return 1
    fi
}

# Function to get current WiFi connection info
get_wifi_info() {
    nmcli -t -f ACTIVE,SSID,SIGNAL,DEVICE device wifi list 2>/dev/null | grep "^yes:" | head -1
}

# Function to perform aggressive WiFi reconnection
reconnect_wifi() {
    local attempt=$1
    log_message "WiFi reconnection attempt $attempt/$MAX_RECONNECT_ATTEMPTS"
    
    # Kill any stuck NetworkManager processes
    if [ $attempt -gt 3 ]; then
        log_message "Restarting NetworkManager to clear any stuck states..."
        sudo systemctl restart NetworkManager
        sleep 10
    fi
    
    # Power cycle WiFi interface if multiple attempts have failed
    if [ $attempt -gt 5 ]; then
        log_message "Power cycling WiFi interface..."
        wifi_device=$(nmcli -t -f DEVICE,TYPE device | grep ":wifi" | cut -d: -f1 | head -1)
        if [ -n "$wifi_device" ]; then
            sudo nmcli radio wifi off
            sleep 3
            sudo nmcli radio wifi on
            sleep 5
        fi
    fi
    
    # Force immediate and thorough WiFi scan
    log_message "Performing WiFi scan..."
    nmcli device wifi rescan 2>/dev/null || true
    sleep 5
    
    # Try to reconnect to the last known good SSID first
    if [ -f "$LAST_SSID_FILE" ]; then
        last_ssid=$(cat "$LAST_SSID_FILE")
        if [ -n "$last_ssid" ]; then
            log_message "Attempting to reconnect to last known SSID: $last_ssid"
            if nmcli connection up "$last_ssid" 2>/dev/null; then
                log_message "Successfully reconnected to $last_ssid"
                return 0
            fi
        fi
    fi
    
    # Get all configured WiFi connections sorted by priority/signal strength
    available_networks=$(nmcli -t -f SSID,SIGNAL device wifi list 2>/dev/null | sort -t: -k2 -nr)
    configured_connections=$(nmcli -t -f NAME,TYPE connection show | grep ":802-11-wireless" | cut -d: -f1)
    
    # Try to connect to configured networks in order of signal strength
    for ssid in $configured_connections; do
        # Check if this SSID is available
        if echo "$available_networks" | grep -q "^$ssid:"; then
            signal=$(echo "$available_networks" | grep "^$ssid:" | cut -d: -f2 | head -1)
            log_message "Attempting to connect to $ssid (signal: $signal%)"
            
            # Use timeout to prevent hanging
            if timeout 30 nmcli connection up "$ssid" 2>/dev/null; then
                log_message "Successfully connected to $ssid"
                echo "$ssid" > "$LAST_SSID_FILE"
                return 0
            else
                log_message "Failed to connect to $ssid"
            fi
        fi
    done
    
    return 1
}

# Main monitoring loop
log_message "WiFi reconnection monitor started"

while true; do
    # Check if WiFi is physically connected
    wifi_connected=$(nmcli -t -f TYPE,STATE device | grep "wifi:connected" | wc -l)
    
    if [ "$wifi_connected" -eq 0 ]; then
        log_message "WiFi disconnected detected"
        RECONNECT_ATTEMPTS=$((RECONNECT_ATTEMPTS + 1))
        
        # Attempt reconnection
        if reconnect_wifi $RECONNECT_ATTEMPTS; then
            log_message "WiFi reconnection successful"
            RECONNECT_ATTEMPTS=0
            
            # Wait for IP and internet connectivity
            sleep 10
            if check_internet; then
                log_message "Internet connectivity confirmed"
            else
                log_message "Warning: WiFi connected but no internet access"
            fi
            
            sleep $CONNECTED_CHECK_INTERVAL
        else
            log_message "WiFi reconnection failed, attempt $RECONNECT_ATTEMPTS"
            
            # Reset attempts counter if we ve tried too many times
            if [ $RECONNECT_ATTEMPTS -ge $MAX_RECONNECT_ATTEMPTS ]; then
                log_message "Maximum reconnection attempts reached, resetting counter"
                RECONNECT_ATTEMPTS=0
                sleep 60  # Wait longer before trying again
            else
                sleep $SCAN_INTERVAL
            fi
        fi
    else
        # WiFi is connected, but verify internet connectivity
        current_wifi=$(get_wifi_info)
        if [ -n "$current_wifi" ]; then
            current_ssid=$(echo "$current_wifi" | cut -d: -f2)
            signal=$(echo "$current_wifi" | cut -d: -f3)
            
            # Update last known good SSID
            echo "$current_ssid" > "$LAST_SSID_FILE"
            
            # Check if internet is actually working
            if ! check_internet; then
                log_message "WiFi connected to $current_ssid but no internet access, attempting reconnection"
                # Treat this as a disconnection
                continue
            fi
            
            # Reset reconnection attempts on successful connection
            RECONNECT_ATTEMPTS=0
        fi
        
        sleep $CONNECTED_CHECK_INTERVAL
    fi
done
'

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable wifi-reconnect.service

# Create enhanced WiFi connection watchdog that monitors signal quality and connection health
sudo tee /etc/systemd/system/wifi-watchdog.service >/dev/null << 'EOF'
[Unit]
Description=Enhanced WiFi Connection Watchdog
After=NetworkManager.service wifi-reconnect.service
Wants=NetworkManager.service

[Service]
Type=simple
Restart=always
RestartSec=10
ExecStart=/bin/bash -c '
# Enhanced WiFi watchdog with improved signal monitoring and connection health checks
WATCHDOG_LOG="/var/log/wifi-watchdog.log"
SIGNAL_THRESHOLD=20
POOR_SIGNAL_COUNT=0
MAX_POOR_SIGNAL_COUNT=3
HEALTH_CHECK_INTERVAL=45

# Function to log with timestamp
log_message() {
    echo "$(date): $1" | tee -a "$WATCHDOG_LOG"
}

# Function to check internet connectivity
check_internet() {
    if ping -c 1 -W 2 8.8.8.8 >/dev/null 2>&1; then
        return 0
    else
        return 1
    fi
}

# Function to find best available network
find_best_network() {
    local current_ssid="$1"
    local current_signal="$2"
    local min_improvement=15
    
    # Get all available networks with signal strength
    available_networks=$(nmcli -t -f SSID,SIGNAL device wifi list 2>/dev/null | sort -t: -k2 -nr)
    configured_connections=$(nmcli -t -f NAME,TYPE connection show | grep ":802-11-wireless" | cut -d: -f1)
    
    best_ssid=""
    best_signal=0
    
    for ssid in $configured_connections; do
        if [ "$ssid" != "$current_ssid" ]; then
            signal=$(echo "$available_networks" | grep "^$ssid:" | cut -d: -f2 | head -1)
            if [ -n "$signal" ] && [ "$signal" -gt "$best_signal" ] && [ "$signal" -gt $((current_signal + min_improvement)) ]; then
                best_ssid="$ssid"
                best_signal="$signal"
            fi
        fi
    done
    
    if [ -n "$best_ssid" ]; then
        echo "$best_ssid:$best_signal"
        return 0
    else
        return 1
    fi
}

log_message "WiFi watchdog started"

while true; do
    # Get current WiFi connection info
    wifi_info=$(nmcli -t -f ACTIVE,SSID,SIGNAL,DEVICE device wifi list 2>/dev/null | grep "^yes:" | head -1)
    
    if [ -n "$wifi_info" ]; then
        current_ssid=$(echo "$wifi_info" | cut -d: -f2)
        current_signal=$(echo "$wifi_info" | cut -d: -f3)
        device=$(echo "$wifi_info" | cut -d: -f4)
        
        # Check if signal is poor
        if [ "$current_signal" -lt "$SIGNAL_THRESHOLD" ]; then
            POOR_SIGNAL_COUNT=$((POOR_SIGNAL_COUNT + 1))
            log_message "Poor signal detected on $current_ssid: $current_signal% (count: $POOR_SIGNAL_COUNT)"
            
            # If we ve had poor signal for several checks, take action
            if [ "$POOR_SIGNAL_COUNT" -ge "$MAX_POOR_SIGNAL_COUNT" ]; then
                log_message "Persistent poor signal, searching for better networks..."
                
                # Force a fresh scan
                nmcli device wifi rescan 2>/dev/null || true
                sleep 5
                
                # Look for better networks
                if better_network=$(find_best_network "$current_ssid" "$current_signal"); then
                    better_ssid=$(echo "$better_network" | cut -d: -f1)
                    better_signal=$(echo "$better_network" | cut -d: -f2)
                    
                    log_message "Found better network: $better_ssid ($better_signal%), switching..."
                    
                    # Attempt to connect to better network
                    if timeout 30 nmcli connection up "$better_ssid" 2>/dev/null; then
                        log_message "Successfully switched to $better_ssid"
                        POOR_SIGNAL_COUNT=0
                        
                        # Verify internet connectivity after switch
                        sleep 10
                        if check_internet; then
                            log_message "Internet connectivity confirmed after switch"
                        else
                            log_message "Warning: No internet after switch, may need to revert"
                        fi
                    else
                        log_message "Failed to switch to $better_ssid, staying with current connection"
                    fi
                else
                    log_message "No better networks found, staying with $current_ssid"
                    POOR_SIGNAL_COUNT=0  # Reset counter to avoid constant switching attempts
                fi
            fi
        else
            # Signal is good, reset poor signal counter
            if [ "$POOR_SIGNAL_COUNT" -gt 0 ]; then
                log_message "Signal improved on $current_ssid: $current_signal%"
                POOR_SIGNAL_COUNT=0
            fi
        fi
        
        # Perform periodic internet connectivity check
        if ! check_internet; then
            log_message "Internet connectivity lost on $current_ssid, triggering reconnection"
            # Let the wifi-reconnect service handle this
            sleep 5
        fi
        
    else
        # No WiFi connection detected
        log_message "No active WiFi connection detected"
        POOR_SIGNAL_COUNT=0
    fi
    
    sleep $HEALTH_CHECK_INTERVAL
done
'

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable wifi-watchdog.service

# Create WiFi hotspot detector service for immediate connection when hotspots become available
sudo tee /etc/systemd/system/wifi-hotspot-detector.service >/dev/null << 'EOF'
[Unit]
Description=WiFi Hotspot Detector
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=simple
Restart=always
RestartSec=30
ExecStart=/bin/bash -c '
# WiFi hotspot detector that monitors for new networks becoming available
DETECTOR_LOG="/var/log/wifi-hotspot-detector.log"
KNOWN_NETWORKS_FILE="/tmp/known_wifi_networks"
SCAN_INTERVAL=20
HOTSPOT_KEYWORDS="hotspot mobile phone tether"

# Function to log with timestamp
log_message() {
    echo "$(date): $1" | tee -a "$DETECTOR_LOG"
}

# Function to get current network list
get_current_networks() {
    nmcli -t -f SSID device wifi list 2>/dev/null | grep -v "^$" | sort
}

# Function to check if network is configured
is_network_configured() {
    local ssid="$1"
    nmcli -t -f NAME,TYPE connection show | grep ":802-11-wireless" | cut -d: -f1 | grep -q "^$ssid$"
}

# Function to detect hotspot-like networks
is_likely_hotspot() {
    local ssid="$1"
    # Convert to lowercase for comparison
    local ssid_lower=$(echo "$ssid" | tr "[:upper:]" "[:lower:]")
    
    # Check for common hotspot patterns
    for keyword in $HOTSPOT_KEYWORDS; do
        if echo "$ssid_lower" | grep -q "$keyword"; then
            return 0
        fi
    done
    
    # Check for phone/device name patterns (iPhone, Android, etc.)
    if echo "$ssid_lower" | grep -qE "(iphone|android|samsung|pixel|oneplus|huawei|xiaomi|galaxy)" || \
       echo "$ssid_lower" | grep -qE "(\s|_|-)*(phone|mobile|cell)" || \
       echo "$ssid" | grep -qE "^[A-Z][a-z]+\s+[A-Z][a-z]+$" || \
       echo "$ssid" | grep -qE "^[A-Z][a-z]+[0-9]+$"; then
        return 0
    fi
    
    return 1
}

log_message "WiFi hotspot detector started"

# Initialize known networks file
get_current_networks > "$KNOWN_NETWORKS_FILE"

while true; do
    # Check if we are currently connected to WiFi
    current_connection=$(nmcli -t -f ACTIVE,SSID device wifi list 2>/dev/null | grep "^yes:" | head -1)
    
    if [ -z "$current_connection" ]; then
        log_message "No WiFi connection detected, scanning for new networks..."
        
        # Force WiFi scan
        nmcli device wifi rescan 2>/dev/null || true
        sleep 5
        
        # Get current available networks
        current_networks=$(get_current_networks)
        
        if [ -f "$KNOWN_NETWORKS_FILE" ]; then
            # Compare with previously known networks
            new_networks=$(comm -13 "$KNOWN_NETWORKS_FILE" <(echo "$current_networks"))
            
            if [ -n "$new_networks" ]; then
                log_message "New networks detected:"
                echo "$new_networks" | while read -r ssid; do
                    if [ -n "$ssid" ]; then
                        log_message "  - $ssid"
                        
                        # Check if this network is configured
                        if is_network_configured "$ssid"; then
                            log_message "New configured network detected: $ssid, attempting connection..."
                            
                            # Attempt immediate connection
                            if timeout 30 nmcli connection up "$ssid" 2>/dev/null; then
                                log_message "Successfully connected to newly available network: $ssid"
                                break
                            else
                                log_message "Failed to connect to $ssid"
                            fi
                        elif is_likely_hotspot "$ssid"; then
                            log_message "Potential hotspot detected: $ssid (not configured)"
                        fi
                    fi
                done
            fi
        fi
        
        # Update known networks
        echo "$current_networks" > "$KNOWN_NETWORKS_FILE"
        
    else
        current_ssid=$(echo "$current_connection" | cut -d: -f2)
        # We are connected, just update the known networks periodically
        if [ $(($(date +%s) % 120)) -eq 0 ]; then  # Every 2 minutes
            get_current_networks > "$KNOWN_NETWORKS_FILE"
        fi
    fi
    
    sleep $SCAN_INTERVAL
done
'

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable wifi-hotspot-detector.service

echo "Enhanced WiFi reconnection system installation completed!"
echo
echo "The following improvements have been made:"
echo "1. Enhanced wifi-reconnect.service with:"
echo "   - Internet connectivity verification"
echo "   - Progressive reconnection attempts with timeouts"
echo "   - NetworkManager restart on persistent failures"
echo "   - WiFi interface power cycling for stubborn issues"
echo "   - Proper logging and last-known-good SSID tracking"
echo
echo "2. Enhanced wifi-watchdog.service with:"
echo "   - Smarter signal quality monitoring"
echo "   - Connection health checks with internet verification"
echo "   - Improved network switching logic"
echo
echo "3. New wifi-hotspot-detector.service that:"
echo "   - Monitors for new networks becoming available"
echo "   - Automatically connects to newly available configured networks"
echo "   - Detects potential hotspots by name patterns"
echo
echo "All services are enabled and will start automatically on boot."
echo "To test the improvements manually, run:"
echo "  sudo systemctl start wifi-reconnect wifi-watchdog wifi-hotspot-detector"
