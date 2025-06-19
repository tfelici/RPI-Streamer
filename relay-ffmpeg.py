#!/usr/bin/env python3
import json
import sys
import os
import signal
from utils import get_setting
import gi
import threading
import psutil

gi.require_version('Gst', '1.0')
from gi.repository import Gst, GObject

Gst.init(None)

def main():
    if len(sys.argv) < 2:
        print("Usage: python relay-ffmpeg.py <MTX_PATH>")
        sys.exit(1)
    mtx_path = sys.argv[1]
    stream_url = get_setting('stream_url')
    if not stream_url:
        print("Error: 'stream_url' must be set in settings.json")
        sys.exit(1)
    #rtsp_url = f"rtsp://localhost:8554/{mtx_path}"
    rtsp_url = f"srt://localhost:8890?streamid=read:{mtx_path}"

    # Save the PID of this process in a file unique to the MTX_PATH
    safe_mtx_path = mtx_path.replace('/', '_').replace('\\', '_')
    ACTIVE_PIDFILE = f"/tmp/relay-ffmpeg-{safe_mtx_path}.pid"
    try:
        with open(ACTIVE_PIDFILE, 'w') as f:
            f.write(str(os.getpid()) + '\n')
        print(f"Active PID file written: {ACTIVE_PIDFILE} with PID {os.getpid()}")
    except Exception as e:
        print(f"Warning: Could not write active PID file: {e}")

    # Get video bitrate from settings or use default
    vbitrate = get_setting('vbitrate', 2000)

    # Determine protocol for -f option
    protocol = None
    if stream_url.startswith('rtsp://') or stream_url.startswith('rtsps://'):
        protocol = 'rtsp'
    elif stream_url.startswith('rtmp://') or stream_url.startswith('rtmps://'):
        protocol = 'flv'
    elif stream_url.startswith('srt://') or stream_url.startswith('udp://'):
        protocol = 'mpegts'
    elif stream_url.startswith('hls://'):
        protocol = 'hls'
    else:
        print(f"Error: Unsupported protocol in stream_url: {stream_url}")
        sys.exit(1)

    def get_active_network_interface():
        # Prefer ethernet if up, else wifi
        candidates = ['eth0', 'en0', 'enp1s0', 'enp2s0', 'wlan0', 'wlp2s0', 'wlp3s0']
        stats = psutil.net_if_stats()
        for iface in candidates:
            if iface in stats and stats[iface].isup:
                return iface        # Fallback: pick the first non-loopback interface that is up
        for iface, stat in stats.items():
            if stat.isup and not iface.startswith('lo'):
                return iface
        return None

    def monitor_network_and_adjust_bitrate(pipeline, x264enc, vbitrate, min_bitrate=256, max_bitrate=4096, interval=5, base_probe_interval=4):
        """
        Monitors stream-specific bitrate using GStreamer pipeline statistics and adjusts encoder bitrate dynamically.
        Uses adaptive probing strategy with exponential backoff, network stability awareness, and intelligent success criteria.
        """
        print("Starting enhanced stream-specific bitrate monitoring with adaptive probing")
        current_bitrate = vbitrate
        import time
        from collections import deque
        
        # Enhanced state tracking
        probe_counter = 0
        last_bytes_out = 0
        last_timestamp = time.time()        
        # Adaptive probing state
        failed_probes = 0
        last_successful_probe = time.time()
        stable_measurements = 0
        unstable_measurements = 0
        recent_bitrates = deque(maxlen=10)  # Track recent measurements for variance
        probe_interval = base_probe_interval
        next_probe_counter = base_probe_interval
        
        # Network stability thresholds
        stability_threshold = 0.15  # 15% variance threshold
        min_stable_count = 3
        max_unstable_count = 2
        
        def calculate_bitrate_variance(bitrates):
            """Calculate coefficient of variation for bitrate stability"""
            if len(bitrates) < 3:
                return 0
            avg = sum(bitrates) / len(bitrates)
            if avg == 0:
                return 0
            variance = sum((x - avg) ** 2 for x in bitrates) / len(bitrates)
            std_dev = variance ** 0.5
            return std_dev / avg  # Coefficient of variation
        
        def is_network_stable():
            """Determine if network conditions are stable enough for probing"""
            if len(recent_bitrates) < min_stable_count:
                return False
            variance = calculate_bitrate_variance(recent_bitrates)
            return variance < stability_threshold
          # Get reference to the srtsink element for statistics
        srtsink = pipeline.get_by_name("srtsink") or pipeline.get_child_by_name("srtsink")
        if not srtsink:
            # Try to find srtsink by iterating through elements
            iterator = pipeline.iterate_elements()
            while True:
                result, element = iterator.next()
                if result != Gst.IteratorResult.OK:
                    break
                if element.get_factory().get_name() == "srtsink":
                    srtsink = element
                    break
        
        if not srtsink:
            print("Warning: Could not find srtsink element for statistics. Bitrate adjustment disabled.")
            return
            
        print("Found srtsink element for stream monitoring")
        
        while True:
            time.sleep(interval)
            current_timestamp = time.time()
            time_diff = current_timestamp - last_timestamp
            
            # Get SRT statistics from the sink
            try:
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
                
                if last_bytes_out > 0 and bytes_out > last_bytes_out:
                    bytes_diff = bytes_out - last_bytes_out
                    measured_bitrate = (bytes_diff * 8) // (time_diff * 1024)  # kbps
                else:
                    measured_bitrate = 0
                    
                # Track recent bitrates for stability analysis
                if measured_bitrate > 0:
                    recent_bitrates.append(measured_bitrate)
                    
                    # Update stability counters
                    if is_network_stable():
                        stable_measurements += 1
                        unstable_measurements = 0
                    else:
                        unstable_measurements += 1
                        stable_measurements = 0
                        
                # Adaptive probe interval based on network stability
                if stable_measurements >= min_stable_count:
                    probe_interval = max(2, base_probe_interval - 1)  # Probe more frequently when stable
                elif unstable_measurements >= max_unstable_count:
                    probe_interval = min(10, base_probe_interval * 2)  # Probe less when unstable
                else:
                    probe_interval = base_probe_interval
                    
                print(f"[Stream] Measured: {measured_bitrate} kbps, Encoder: {current_bitrate} kbps, Stability: {'stable' if stable_measurements >= min_stable_count else 'unstable'}")
                
                last_bytes_out = bytes_out
                last_timestamp = current_timestamp
                
            except Exception as e:
                print(f"Warning: Could not get stream statistics: {e}")
                measured_bitrate = 0            
            probe_counter += 1
            
            # Enhanced adaptive probing logic
            time_since_last_successful_probe = time.time() - last_successful_probe
            should_probe = (
                probe_counter >= next_probe_counter and 
                current_bitrate < max_bitrate and
                is_network_stable() and
                time_since_last_successful_probe >= (probe_interval * interval)
            )
            
            if should_probe:
                # Exponential probe step based on current bitrate
                probe_step = max(128, int(current_bitrate * 0.15))  # 15% increase, minimum 128 kbps
                test_bitrate = min(max_bitrate, current_bitrate + probe_step)
                
                print(f"[Bitrate] Probing higher encoder bitrate: {test_bitrate} kbps (step: +{probe_step} kbps, stability: {calculate_bitrate_variance(recent_bitrates):.3f})")
                
                if x264enc:
                    x264enc.set_property('bitrate', test_bitrate)
                    print(f"[Debug] Encoder bitrate set to: {x264enc.get_property('bitrate')} kbps")                
                
                # Measure for probe period
                time.sleep(interval)
                probe_timestamp = time.time()
                probe_time_diff = probe_timestamp - current_timestamp
                
                try:
                    stats = srtsink.get_property("stats")
                    if stats:
                        if stats.has_field("bytes-sent-total"):
                            success, probe_bytes_out = stats.get_uint64("bytes-sent-total")
                            probe_bytes_out = probe_bytes_out if success else 0
                        elif stats.has_field("bytes-sent"):
                            success, probe_bytes_out = stats.get_uint64("bytes-sent")
                            probe_bytes_out = probe_bytes_out if success else 0
                        else:
                            probe_bytes_out = 0
                    else:
                        probe_bytes_out = 0
                        
                    if probe_bytes_out > bytes_out:
                        probe_bytes_diff = probe_bytes_out - bytes_out
                        probe_bitrate = (probe_bytes_diff * 8) // (probe_time_diff * 1024)
                    else:
                        probe_bitrate = 0
                        
                    print(f"[Probe] Measured stream bitrate after probe: {probe_bitrate} kbps")
                    
                    # Enhanced success criteria
                    probe_success = (
                        probe_bitrate > measured_bitrate * 1.1 and  # Throughput improved by 10%
                        probe_bitrate > current_bitrate * 0.85 and   # No significant drop from encoder setting
                        probe_time_diff > interval * 0.8            # Sufficient measurement time
                    )
                    
                    if probe_success:
                        current_bitrate = test_bitrate
                        failed_probes = 0
                        last_successful_probe = time.time()
                        next_probe_counter = probe_counter + probe_interval  # Reset probe counter
                        print(f"[Bitrate] Probe successful, keeping increased bitrate: {current_bitrate} kbps")
                    else:
                        failed_probes += 1
                        # Exponential backoff: wait longer after failures
                        backoff_multiplier = min(2 ** failed_probes, 8)  # Max 8x backoff
                        next_probe_counter = probe_counter + (probe_interval * backoff_multiplier)
                        print(f"[Bitrate] Probe failed (attempt {failed_probes}), reverting to {current_bitrate} kbps, next probe in {probe_interval * backoff_multiplier} cycles")
                        if x264enc:
                            x264enc.set_property('bitrate', current_bitrate)
                    
                    last_bytes_out = probe_bytes_out
                    last_timestamp = probe_timestamp
                    continue
                    
                except Exception as e:
                    print(f"Warning: Could not get probe statistics: {e}")
                    failed_probes += 1
                    # Revert to previous bitrate on error
                    if x264enc:
                        x264enc.set_property('bitrate', current_bitrate)            
            # Enhanced congestion detection with multiple criteria
            congestion_detected = (
                measured_bitrate > 0 and 
                measured_bitrate < current_bitrate * 0.7 and  # Significant throughput drop
                current_bitrate > min_bitrate and
                unstable_measurements >= max_unstable_count  # Only reduce if network is unstable
            )
            
            if congestion_detected:
                # More conservative reduction with minimum step
                reduction_factor = 0.8 if failed_probes > 2 else 0.85  # More aggressive if many probe failures
                new_bitrate = max(min_bitrate, int(current_bitrate * reduction_factor))
                print(f"[Bitrate] Congestion detected (measured: {measured_bitrate}, encoder: {current_bitrate}), reducing to {new_bitrate} kbps")
                current_bitrate = new_bitrate
                failed_probes = 0  # Reset probe failures after congestion response
                if x264enc:
                    x264enc.set_property('bitrate', current_bitrate)

    # Global pipeline reference for cleanup
    global_pipeline = {'pipeline': None}

    def run_gstreamer_pipeline_dynamic(rtsp_url, stream_url, vbitrate):
        # Build pipeline elements
        pipeline = Gst.Pipeline.new("relay-pipeline")
        global_pipeline['pipeline'] = pipeline
        srtsrc = Gst.ElementFactory.make("srtsrc", None)
        srtsrc.set_property("uri", rtsp_url)
        demux = Gst.ElementFactory.make("tsdemux", "demux")
        video_queue = Gst.ElementFactory.make("queue", None)
        h264parse = Gst.ElementFactory.make("h264parse", None)
        avdec_h264 = Gst.ElementFactory.make("avdec_h264", None)
        videoconvert = Gst.ElementFactory.make("videoconvert", None)
        x264enc = Gst.ElementFactory.make("x264enc", None)
        x264enc.set_property("tune", "zerolatency")
        x264enc.set_property("bitrate", vbitrate)
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
        monitor_thread = threading.Thread(target=monitor_network_and_adjust_bitrate, args=(pipeline, x264enc, vbitrate), daemon=True)
        monitor_thread.start()
        while True:
            msg = bus.timed_pop_filtered(100 * Gst.MSECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS)
            if msg:
                if msg.type == Gst.MessageType.ERROR:
                    err, debug = msg.parse_error()
                    print(f"GStreamer Error: {err}, {debug}")
                    break
                elif msg.type == Gst.MessageType.EOS:
                    print("GStreamer End of stream")
                    break
        pipeline.set_state(Gst.State.NULL)

    def run_gstreamer_pipeline_static(rtsp_url, stream_url):
        pipeline_str = f"srtsrc uri={rtsp_url} ! srtsink uri={stream_url}"
        pipeline = Gst.parse_launch(pipeline_str)
        global_pipeline['pipeline'] = pipeline
        pipeline.set_state(Gst.State.PLAYING)
        bus = pipeline.get_bus()
        while True:
            msg = bus.timed_pop_filtered(100 * Gst.MSECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS)
            if msg:
                if msg.type == Gst.MessageType.ERROR:
                    err, debug = msg.parse_error()
                    print(f"GStreamer Error: {err}, {debug}")
                    break
                elif msg.type == Gst.MessageType.EOS:
                    print("GStreamer End of stream")
                    break
        pipeline.set_state(Gst.State.NULL)

    dynamicBitrate = get_setting('dynamicBitrate', True)

    def handle_exit(signum, frame):
        print(f"Received exit signal {signum}, cleaning up...")
        # Set pipeline to NULL for clean shutdown
        try:
            if global_pipeline['pipeline'] is not None:
                print("Setting pipeline state to NULL for cleanup...")
                global_pipeline['pipeline'].set_state(Gst.State.NULL)
        except Exception as e:
            print(f"Warning: Could not set pipeline to NULL on exit: {e}")
        # Remove PID file
        try:
            if os.path.exists(ACTIVE_PIDFILE):
                os.remove(ACTIVE_PIDFILE)
        except Exception as e:
            print(f"Warning: Could not remove active PID file on exit: {e}")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    while True:
        if stream_url.startswith('https://') and '/whip' in stream_url:
            print("WHIP streaming is not supported in this version. Exiting.")
            break
        elif not dynamicBitrate:
            print("Running SRT passthrough pipeline (no dynamic bitrate)")
            run_gstreamer_pipeline_static(rtsp_url, stream_url)
        else:
            print("Running GStreamer pipeline with dynamic pad linking (video+audio)")
            run_gstreamer_pipeline_dynamic(rtsp_url, stream_url, vbitrate)
        print("Process exited, restarting in 1 second...")
        import time
        time.sleep(1)

if __name__ == "__main__":
    main()
