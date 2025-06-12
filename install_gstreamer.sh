#!/bin/bash
set -e

echo ">>> Updating system..."
sudo apt update
sudo apt upgrade -y

echo ">>> Installing base dependencies..."
sudo apt install -y \
  build-essential \
  git \
  curl \
  cmake \
  ninja-build \
  libssl-dev \
  pkg-config \
  libglib2.0-dev \
  libgirepository1.0-dev \
  libgstreamer1.0-dev \
  libgstreamer-plugins-bad1.0-dev \
  libgstreamer-plugins-base1.0-dev \
  gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly \
  gstreamer1.0-libav \
  gstreamer1.0-tools \
  gstreamer1.0-alsa \
  python3-pip \
  pulseaudio

# Install libnice and related GStreamer NICE plugin separately to catch errors
sudo apt install -y libnice10 libnice-dev gir1.2-nice-0.1
sudo apt install -y gstreamer1.0-nice

echo ">>> Installing Rust toolchain..."
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source "$HOME/.cargo/env"

echo ">>> cleaning up previous builds..."
rm -rf ~/gst-plugins-rs
rm -rf ~/.cargo/git/checkouts/gstreamer-rs-*
echo ">>> Cloning GStreamer Rust Plugins..."
cd ~
git clone https://gitlab.freedesktop.org/gstreamer/gst-plugins-rs.git

echo ">>> Building only rswebrtc plugin..."
cd ~/gst-plugins-rs/net/webrtc
cargo build --release

echo ">>> Installing rswebrtc plugin..."
sudo mkdir -p /usr/local/lib/gstreamer-1.0/
sudo cp ~/gst-plugins-rs/target/release/libgstrswebrtc.so /usr/local/lib/gstreamer-1.0/
sudo ldconfig

echo ">>> Setting GST_PLUGIN_PATH for the current and future sessions..."
export GST_PLUGIN_PATH=/usr/local/lib/gstreamer-1.0
if ! grep -q 'GST_PLUGIN_PATH' ~/.bashrc; then
  echo 'export GST_PLUGIN_PATH=/usr/local/lib/gstreamer-1.0' >> ~/.bashrc
fi

echo "Cleaning up build files..."

# Remove cloned repositories and temporary build files
rm -rf ~/gst-plugins-rs
rm -rf ~/.cargo/git/checkouts/gstreamer-rs-*

echo "Cleanup complete."

echo "✅ Installation complete. After rebooting, you can now use 'whipclientsink' in your GStreamer pipelines."
