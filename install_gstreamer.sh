#!/bin/bash
set -euo pipefail

LOGFILE="$HOME/install-whipclientsink.log"
BUILD_DIR="$HOME/gst-plugins-rs"
INSTALL_STAGING="/tmp/gst-install"

echo "📋 Logging to $LOGFILE"
exec > >(tee "$LOGFILE") 2>&1

function section {
  echo ""
  echo "🔷 $1"
  echo "--------------------------------------------"
}

section "1. Installing system packages"
sudo apt update
sudo apt install -y \
  libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
  libgstreamer-plugins-bad1.0-dev \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly \
  gstreamer1.0-libav gstreamer1.0-tools gstreamer1.0-alsa \
  libssl-dev pkg-config curl git build-essential

section "2. Installing Rust and cargo-c"
if ! command -v rustup &> /dev/null; then
  curl https://sh.rustup.rs -sSf | sh -s -- -y
  source "$HOME/.cargo/env"
fi

rustup default stable
cargo install cargo-c

section "3. Cloning gst-plugins-rs"
rm -rf "$BUILD_DIR"
git clone https://gitlab.freedesktop.org/gstreamer/gst-plugins-rs.git "$BUILD_DIR"
cd "$BUILD_DIR"

section "4. Building and staging gst-plugin-webrtc"
cargo cbuild -p gst-plugin-webrtc --release --prefix=/usr
rm -rf "$INSTALL_STAGING"
cargo cinstall -p gst-plugin-webrtc --prefix=/usr --destdir="$INSTALL_STAGING"

section "5. Installing plugin to /usr/lib"
sudo cp -rv "$INSTALL_STAGING/usr/lib/"* /usr/lib/

section "6. Cleaning up build and staging directories"
rm -rf "$BUILD_DIR"
rm -rf "$INSTALL_STAGING"

section "7. Verifying installation"
if gst-inspect-1.0 whipclientsink &>/dev/null; then
  echo "✅ whipclientsink successfully installed!"
else
  echo "❌ Installation failed: whipclientsink not found."
  exit 1
fi

echo "📝 Full log saved to: $LOGFILE"
