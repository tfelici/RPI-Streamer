# RPI Encoder

A Flask-based web app and streaming server for Raspberry Pi, with easy installation and systemd integration.

## Installation

Run the following commands in your home directory (do **not** use superuser/root):

```sh
curl -O https://raw.githubusercontent.com/tfelici/RPI-Encoder/main/install_rpi_encoder.sh
bash install_rpi_encoder.sh --install
```

## Features

- Simple web UI for streaming and settings
- Systemd service setup
- MediaMTX streaming server
- Audio/video device selection
- Easy update and maintenance

## Requirements

- Raspberry Pi OS Lite (or compatible Linux)
- Python 3
- ffmpeg

## Usage

After installation, access the web UI at `http://<your-pi-ip>/` in your browser.

## Support

For issues or questions, open an issue on the [GitHub repository](https://github.com/tfelici/RPI-Encoder).
