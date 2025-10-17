#!/usr/bin/env python3
"""
GStreamer pipeline subprocess for relay-ffmpeg.py
This file runs the actual streaming pipeline in a separate process for isolation.
"""
import json
import sys
import os
import time
import signal
import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst, GObject

Gst.init(None)

# Import utilities from parent directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import get_setting

# Global flag for signal handler
cleanup_requested = False

def handle_shutdown_signal(signum, frame):
    """Handle SIGTERM/SIGINT for clean shutdown."""
    global cleanup_requested
    print(f"Received signal {signum}, initiating clean shutdown...")
    cleanup_requested = True

# Set up signal handlers
signal.signal(signal.SIGTERM, handle_shutdown_signal)
signal.signal(signal.SIGINT, handle_shutdown_signal)

def get_srt_bytes_sent(srtsink):
    """
    Get the total bytes sent from an SRT sink element.
    
    Args:
        srtsink: GStreamer SRT sink element
        
    Returns:
        int: Total bytes sent, or 0 if unable to retrieve
    """
    # Get bytes sent from SRT sink statistics
    stats = srtsink.get_property("stats")
    if stats:
        if stats.has_field("bytes-sent-total"):
            success, bytes_out = stats.get_uint64("bytes-sent-total")
            bytes_out = bytes_out if success else 0
        elif stats.has_field("bytes-sent"):
            success, bytes_out = stats.get_uint64("bytes-sent")
            bytes_out = bytes_out if success else 0
        else:
            bytes_out = 0
    else:
        # Fallback: try to get statistics from pad
        sink_pad = srtsink.get_static_pad("sink")
        if sink_pad:
            query = Gst.Query.new_stats(Gst.PadDirection.SINK)
            if sink_pad.query(query):
                stats = query.parse_stats()
                if stats and stats.has_field("bytes"):
                    success, bytes_out = stats.get_uint64("bytes")
                    bytes_out = bytes_out if success else 0
                else:
                    bytes_out = 0
            else:
                bytes_out = 0
        else:
            bytes_out = 0
    
    return bytes_out

def run_static_pipeline(rtsp_url, stream_url, status_file):
    """
    Run static SRT passthrough pipeline.
    
    Args:
        rtsp_url: Source SRT URL
        stream_url: Destination stream URL
        status_file: Path to status file for monitoring
    """
    global cleanup_requested
    
    print(f"Starting static pipeline")
    print(f"Source: {rtsp_url}")
    print(f"Destination: {stream_url}")
    
    pipeline_str = f"srtsrc uri={rtsp_url} ! srtsink uri={stream_url}"
    pipeline = Gst.parse_launch(pipeline_str)
    pipeline.set_state(Gst.State.PLAYING)
    bus = pipeline.get_bus()
    
    # Simple monitoring for subprocess
    last_bytes_out = 0
    last_timestamp = time.time()
    monitor_counter = 0
    
    print("Static pipeline started, monitoring bitrate...")
    
    while True:
        # Check for cleanup request from signal handler
        if cleanup_requested:
            print("Cleanup requested, stopping pipeline...")
            break
            
        msg = bus.timed_pop_filtered(1000 * Gst.MSECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if msg:
            if msg.type == Gst.MessageType.ERROR:
                err, debug = msg.parse_error()
                print(f"Error: {err}, {debug}")
                break
            elif msg.type == Gst.MessageType.EOS:
                print("End of stream")
                break
        
        # Simple bitrate monitoring every 5 seconds
        monitor_counter += 1
        if monitor_counter >= 5:  # 5 seconds at 1000ms intervals
            monitor_counter = 0
            current_timestamp = time.time()
            time_diff = current_timestamp - last_timestamp
            
            # Find srtsink for monitoring
            srtsink = None
            iterator = pipeline.iterate_elements()
            while True:
                result, element = iterator.next()
                if result != Gst.IteratorResult.OK:
                    break
                if element.get_factory().get_name() == "srtsink":
                    srtsink = element
                    break
            
            if srtsink:
                try:
                    bytes_out = get_srt_bytes_sent(srtsink)
                    if last_bytes_out > 0 and bytes_out > last_bytes_out:
                        bytes_diff = bytes_out - last_bytes_out
                        measured_bitrate = (bytes_diff * 8) // (time_diff * 1024)
                    else:
                        measured_bitrate = 0
                    
                    print(f"Measured {measured_bitrate} kbps")
                    
                    # Write status file
                    status_info = {
                        'bitrate': 'passthrough',
                        'measured_bitrate': measured_bitrate,
                        'network_status': 'static',
                        'pipeline_status': 'running',
                        'stream_health': 'passthrough',
                        'timestamp': int(time.time())
                    }
                    
                    try:
                        with open(status_file, 'w') as f:
                            json.dump(status_info, f)
                    except Exception as e:
                        print(f"Could not write status: {e}")
                    
                    last_bytes_out = bytes_out
                    last_timestamp = current_timestamp
                    
                except Exception as e:
                    print(f"Monitoring error: {e}")
    
    # Clean shutdown
    pipeline.set_state(Gst.State.NULL)
    print("Static pipeline stopped")

def run_dynamic_pipeline(rtsp_url, stream_url, status_file):
    """
    Run dynamic pipeline with re-encoding and bitrate monitoring.
    
    Args:
        rtsp_url: Source SRT URL
        stream_url: Destination stream URL
        status_file: Path to status file for monitoring
    """
    global cleanup_requested
    
    print(f"Starting dynamic pipeline")
    print(f"Source: {rtsp_url}")
    print(f"Destination: {stream_url}")
    
    # Build pipeline elements
    pipeline = Gst.Pipeline.new("relay-pipeline")
    srtsrc = Gst.ElementFactory.make("srtsrc", None)
    srtsrc.set_property("uri", rtsp_url)
    demux = Gst.ElementFactory.make("tsdemux", "demux")
    video_queue = Gst.ElementFactory.make("queue", None)
    h264parse = Gst.ElementFactory.make("h264parse", None)
    avdec_h264 = Gst.ElementFactory.make("avdec_h264", None)
    videoconvert = Gst.ElementFactory.make("videoconvert", None)
    x264enc = Gst.ElementFactory.make("x264enc", None)
    x264enc.set_property("tune", "zerolatency")
    x264enc.set_property("bitrate", get_setting('vbitrate'))
    x264enc.set_property("speed-preset", "ultrafast")
    h264_caps = Gst.Caps.from_string("video/x-h264,profile=baseline")
    video_capsfilter = Gst.ElementFactory.make("capsfilter", None)
    video_capsfilter.set_property("caps", h264_caps)
    video_queue2 = Gst.ElementFactory.make("queue", None)
    audio_queue = Gst.ElementFactory.make("queue", None)
    opusparse = Gst.ElementFactory.make("opusparse", None)
    audio_queue2 = Gst.ElementFactory.make("queue", None)
    mux = Gst.ElementFactory.make("mpegtsmux", "mux")
    srtsink = Gst.ElementFactory.make("srtsink", "srtsink")
    srtsink.set_property("uri", stream_url)

    # Add elements to pipeline
    for elem in [srtsrc, demux, video_queue, h264parse, avdec_h264, videoconvert, x264enc, video_capsfilter, video_queue2, audio_queue, opusparse, audio_queue2, mux, srtsink]:
        pipeline.add(elem)

    # Link static parts
    srtsrc.link(demux)
    mux.link(srtsink)

    # Dynamic pad linking
    def on_pad_added(demux, pad):
        string = pad.query_caps(None).to_string()
        if string.startswith("video/"):
            print("Linking video pad:", string)
            pad.link(video_queue.get_static_pad("sink"))
            video_queue.link(h264parse)
            h264parse.link(avdec_h264)
            avdec_h264.link(videoconvert)
            videoconvert.link(x264enc)
            x264enc.link(video_capsfilter)
            video_capsfilter.link(video_queue2)
            video_queue2.link(mux)
        elif string.startswith("audio/x-opus"):
            print("Linking audio pad:", string)
            pad.link(audio_queue.get_static_pad("sink"))
            audio_queue.link(opusparse)
            opusparse.link(audio_queue2)
            audio_queue2.link(mux)
        else:
            print("Unknown pad type:", string)
    
    demux.connect("pad-added", on_pad_added)

    pipeline.set_state(Gst.State.PLAYING)
    bus = pipeline.get_bus()
    
    # Dynamic bitrate monitoring and adjustment
    from collections import deque
    
    last_bytes_out = 0
    last_timestamp = time.time()
    monitor_counter = 0
    
    # Dynamic bitrate adjustment state
    vbitrate = get_setting('vbitrate')
    current_bitrate = vbitrate
    max_bitrate = vbitrate
    min_bitrate = 256
    recent_bitrates = deque(maxlen=6)  # Track recent measurements
    probe_counter = 0
    last_probe_time = time.time()
    failed_probes = 0
    
    print(f"Dynamic pipeline started with initial bitrate {current_bitrate} kbps")
    
    while True:
        # Check for cleanup request from signal handler
        if cleanup_requested:
            print("Cleanup requested, stopping dynamic pipeline...")
            break
            
        msg = bus.timed_pop_filtered(1000 * Gst.MSECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if msg:
            if msg.type == Gst.MessageType.ERROR:
                err, debug = msg.parse_error()
                print(f"Error: {err}, {debug}")
                break
            elif msg.type == Gst.MessageType.EOS:
                print("End of stream")
                break
        
        # Dynamic bitrate monitoring every 5 seconds
        monitor_counter += 1
        if monitor_counter >= 5:  # 5 seconds at 1000ms intervals
            monitor_counter = 0
            probe_counter += 1
            current_timestamp = time.time()
            time_diff = current_timestamp - last_timestamp
            
            # Check for vbitrate setting changes
            new_vbitrate = get_setting('vbitrate')
            if new_vbitrate != vbitrate:
                print(f"vbitrate setting changed from {vbitrate} to {new_vbitrate}")
                vbitrate = new_vbitrate
                max_bitrate = vbitrate
                current_bitrate = min(current_bitrate, max_bitrate)
                x264enc.set_property('bitrate', current_bitrate)
            
            try:
                bytes_out = get_srt_bytes_sent(srtsink)
                if last_bytes_out > 0 and bytes_out > last_bytes_out:
                    bytes_diff = bytes_out - last_bytes_out
                    measured_bitrate = (bytes_diff * 8) // (time_diff * 1024)
                else:
                    measured_bitrate = 0
                
                # Track recent bitrates for stability
                if measured_bitrate > 0:
                    recent_bitrates.append(measured_bitrate)
                
                # Calculate network stability
                network_stable = True
                if len(recent_bitrates) >= 3:
                    avg_bitrate = sum(recent_bitrates) / len(recent_bitrates)
                    variance = sum((br - avg_bitrate) ** 2 for br in recent_bitrates) / len(recent_bitrates)
                    coefficient_of_variation = (variance ** 0.5) / avg_bitrate if avg_bitrate > 0 else 0
                    network_stable = coefficient_of_variation < 0.15  # 15% variance threshold
                
                network_status = "stable" if network_stable else "unstable"
                
                print(f"Measured {measured_bitrate} kbps, Encoder {current_bitrate} kbps, Network {network_status}")
                
                # Probing logic - try higher bitrate every 20 seconds if conditions are good
                time_since_last_probe = current_timestamp - last_probe_time
                should_probe = (
                    probe_counter >= 4 and  # Every 20 seconds (4 * 5s intervals)
                    current_bitrate < max_bitrate and
                    network_stable and
                    measured_bitrate > current_bitrate * 0.8 and  # Good throughput
                    time_since_last_probe >= 15  # At least 15s between probes
                )
                
                if should_probe:
                    # Calculate probe step (10-20% increase, minimum 128 kbps)
                    probe_step = max(128, min(int(current_bitrate * 0.15), max_bitrate - current_bitrate))
                    test_bitrate = min(max_bitrate, current_bitrate + probe_step)
                    
                    if test_bitrate > current_bitrate:
                        print(f"Probing higher bitrate: {test_bitrate} kbps (+{probe_step} kbps)")
                        x264enc.set_property('bitrate', test_bitrate)
                        last_probe_time = current_timestamp
                        probe_counter = 0
                        
                        # Wait and measure probe result
                        time.sleep(5)
                        probe_timestamp = time.time()
                        probe_time_diff = probe_timestamp - current_timestamp
                        
                        try:
                            probe_bytes_out = get_srt_bytes_sent(srtsink)
                            if probe_bytes_out > bytes_out:
                                probe_bytes_diff = probe_bytes_out - bytes_out
                                probe_bitrate = (probe_bytes_diff * 8) // (probe_time_diff * 1024)
                            else:
                                probe_bitrate = 0
                            
                            # Evaluate probe success
                            probe_success = (
                                probe_bitrate > measured_bitrate * 1.05 and  # 5% improvement
                                probe_time_diff > 3  # Sufficient measurement time
                            )
                            
                            if probe_success:
                                current_bitrate = test_bitrate
                                failed_probes = 0
                                print(f"Probe successful, keeping bitrate: {current_bitrate} kbps")
                            else:
                                failed_probes += 1
                                print(f"Probe failed (attempt {failed_probes}), reverting to {current_bitrate} kbps")
                                x264enc.set_property('bitrate', current_bitrate)
                            
                            # Update tracking variables with probe data
                            last_bytes_out = probe_bytes_out
                            last_timestamp = probe_timestamp
                            continue
                            
                        except Exception as e:
                            print(f"Probe measurement error: {e}")
                            failed_probes += 1
                            x264enc.set_property('bitrate', current_bitrate)
                
                # Congestion detection - reduce bitrate if throughput is poor
                congestion_detected = (
                    measured_bitrate > 0 and 
                    measured_bitrate < current_bitrate * 0.65 and  # Significant drop
                    current_bitrate > min_bitrate and
                    not network_stable
                )
                
                if congestion_detected:
                    # Reduce bitrate by 15-25% depending on severity
                    reduction_factor = 0.75 if measured_bitrate < current_bitrate * 0.5 else 0.85
                    new_bitrate = max(min_bitrate, int(current_bitrate * reduction_factor))
                    print(f"Congestion detected (measured: {measured_bitrate}, encoder: {current_bitrate}), reducing to {new_bitrate} kbps")
                    current_bitrate = new_bitrate
                    failed_probes = 0  # Reset probe failures after congestion response
                    x264enc.set_property('bitrate', current_bitrate)
                
                # Write status file
                status_info = {
                    'bitrate': str(current_bitrate),
                    'measured_bitrate': measured_bitrate,
                    'network_status': network_status,
                    'pipeline_status': 'running',
                    'stream_health': 'good' if measured_bitrate > current_bitrate * 0.7 else 'degraded',
                    'recent_bitrates': list(recent_bitrates),
                    'probe_failures': failed_probes,
                    'timestamp': int(time.time())
                }
                
                try:
                    with open(status_file, 'w') as f:
                        json.dump(status_info, f)
                except Exception as e:
                    print(f"Could not write status: {e}")
                
                last_bytes_out = bytes_out
                last_timestamp = current_timestamp
                
            except Exception as e:
                print(f"Monitoring error: {e}")

    # Clean shutdown
    pipeline.set_state(Gst.State.NULL)
    print("Dynamic pipeline stopped")

def main():
    """Main subprocess entry point."""
    if len(sys.argv) != 5:
        print("Usage: relay-ffmpeg-subprocess.py <pipeline_type> <rtsp_url> <stream_url> <status_file>")
        sys.exit(1)
    
    pipeline_type = sys.argv[1]
    rtsp_url = sys.argv[2]
    stream_url = sys.argv[3]
    status_file = sys.argv[4]
    
    print(f"Starting {pipeline_type} pipeline")
    
    try:
        if pipeline_type == "static":
            run_static_pipeline(rtsp_url, stream_url, status_file)
        elif pipeline_type == "dynamic":
            run_dynamic_pipeline(rtsp_url, stream_url, status_file)
        else:
            print(f"Unknown pipeline type: {pipeline_type}")
            sys.exit(1)
    except Exception as e:
        print(f"Exception: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()