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

# Optimize NetworkManager for faster WiFi connections
printf "Optimizing NetworkManager for faster WiFi connections...\n"
sudo tee /etc/NetworkManager/conf.d/99-wifi-optimization.conf > /dev/null << 'EOF'
[main]
# Faster WiFi scanning and connection settings
no-auto-default=*

[connection]
# Faster connection attempts
wifi.powersave=2
ipv4.dhcp-timeout=10
ipv6.dhcp-timeout=10

[device]
# Aggressive WiFi scanning for faster detection of available networks
wifi.scan-rand-mac-address=no
match-device=driver:brcmfmac

[connectivity]
# Faster connectivity checking
uri=http://nmcheck.gnome.org/check_network_status.txt
interval=10
EOF

# Restart NetworkManager to apply the new configuration
sudo systemctl restart NetworkManager

# Print completion message
echo "RPI Streamer installation completed successfully!"