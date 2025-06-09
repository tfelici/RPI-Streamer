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
sudo apt-get install gunicorn python3-gevent -y
sudo apt-get install gunicorn python3-requests-toolbelt -y
sudo apt-get install python3-pymediainfo -y
sudo apt-get install mediainfo -y
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
#do not fail if the executables do not exist
if [ ! -d "$HOME/executables" ]; then
    mkdir -p "$HOME/executables"
fi
# Download the StreamerUploader executables for different platforms
printf "Downloading StreamerUploader executables...\n"
curl -H "Cache-Control: no-cache" -L "https://github.com/tfelici/Streamer-Uploader/raw/main/windows/dist/StreamerUploader.exe" -o "$HOME/executables/Uploader-windows.exe"
curl -H "Cache-Control: no-cache" -L "https://github.com/tfelici/Streamer-Uploader/raw/main/macos/dist/StreamerUploader" -o "$HOME/executables/Uploader-macos"
curl -H "Cache-Control: no-cache" -L "https://github.com/tfelici/Streamer-Uploader/raw/main/linux/dist/StreamerUploader" -o "$HOME/executables/Uploader-linux"
# if they exist, Make the downloaded linux and macos executables executable
[ -f "$HOME/executables/Uploader-linux" ] && chmod +x "$HOME/executables/Uploader-linux"
[ -f "$HOME/executables/Uploader-macos" ] && chmod +x "$HOME/executables/Uploader-macos"
# search and install latest version of mediamtx
printf "Detecting system architecture...\n"
ARCH=$(uname -m)
if [[ "$ARCH" == "aarch64"* || "$ARCH" == arm* ]]; then
    MTX_SUFFIX="linux_arm64.tar.gz"
else
    MTX_SUFFIX="linux_amd64.tar.gz"
fi
printf "Searching for the latest MediaMTX release for $MTX_SUFFIX...\n"
# Check if jq is installed, if not, install it
if ! command -v jq &> /dev/null; then
    sudo apt-get install jq -y
fi
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
ExecStart=/usr/bin/python3 app.py $USER:$USER
#ExecStart=/usr/bin/gunicorn -w 1 -k gevent --threads 4 -b 0.0.0.0:80 app:app
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
ExecStart=/bin/bash $HOME/flask_app/install_rpi_streamer.sh 
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
