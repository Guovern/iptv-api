import os
import sys
import threading
import time

sys.path.append(os.path.dirname(sys.path[0]))
from flask import Flask, send_from_directory, make_response, request, jsonify, Response
from utils.tools import get_result_file_content, resource_path, get_public_url
from utils.config import config
import utils.constants as constants
import atexit
from rtmp import start_rtmp_service, stop_rtmp_service, app_rtmp_url, hls_temp_path, STREAMS_LOCK, \
    hls_running_streams, start_hls_to_rtmp, hls_last_access, hls_idle_monitor, HLS_WAIT_TIMEOUT, HLS_WAIT_INTERVAL
import logging

app = Flask(__name__)
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)


@app.route("/")
def show_index():
    return get_result_file_content(
        path=constants.hls_result_path if config.open_rtmp else config.final_file,
        file_type="m3u" if config.open_m3u_result else "txt"
    )


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(resource_path('static/images'), 'favicon.ico',
                               mimetype='image/vnd.microsoft.icon')


@app.route("/txt")
def show_txt():
    return get_result_file_content(path=config.final_file, file_type="txt")


@app.route("/ipv4/txt")
def show_ipv4_txt():
    return get_result_file_content(path=constants.ipv4_result_path, file_type="txt")


@app.route("/ipv6/txt")
def show_ipv6_txt():
    return get_result_file_content(path=constants.ipv6_result_path, file_type="txt")


@app.route("/hls")
def show_hls():
    return get_result_file_content(path=constants.hls_result_path,
                                   file_type="m3u" if config.open_m3u_result else "txt")


@app.route("/hls/txt")
def show_hls_txt():
    return get_result_file_content(path=constants.hls_result_path, file_type="txt")


@app.route("/hls/ipv4/txt")
def show_hls_ipv4_txt():
    return get_result_file_content(path=constants.hls_ipv4_result_path, file_type="txt")


@app.route("/hls/ipv6/txt")
def show_hls_ipv6_txt():
    return get_result_file_content(path=constants.hls_ipv6_result_path, file_type="txt")


@app.route("/m3u")
def show_m3u():
    return get_result_file_content(path=config.final_file, file_type="m3u")


@app.route("/hls/m3u")
def show_hls_m3u():
    return get_result_file_content(path=constants.hls_result_path, file_type="m3u")


@app.route("/ipv4/m3u")
def show_ipv4_m3u():
    return get_result_file_content(path=constants.ipv4_result_path, file_type="m3u")


@app.route("/ipv4")
def show_ipv4_result():
    return get_result_file_content(
        path=constants.hls_ipv4_result_path if config.open_rtmp else constants.ipv4_result_path,
        file_type="m3u" if config.open_m3u_result else "txt"
    )


@app.route("/ipv6/m3u")
def show_ipv6_m3u():
    return get_result_file_content(path=constants.ipv6_result_path, file_type="m3u")


@app.route("/ipv6")
def show_ipv6_result():
    return get_result_file_content(
        path=constants.hls_ipv6_result_path if config.open_rtmp else constants.ipv6_result_path,
        file_type="m3u" if config.open_m3u_result else "txt"
    )


@app.route("/hls/ipv4/m3u")
def show_hls_ipv4_m3u():
    return get_result_file_content(path=constants.hls_ipv4_result_path, file_type="m3u")


@app.route("/hls/ipv6/m3u")
def show_hls_ipv6_m3u():
    return get_result_file_content(path=constants.hls_ipv6_result_path, file_type="m3u")


@app.route("/content")
def show_content():
    return get_result_file_content(
        path=constants.hls_result_path if config.open_rtmp else config.final_file,
        file_type="m3u" if config.open_m3u_result else "txt",
        show_content=True
    )


@app.route("/epg/epg.xml")
def show_epg():
    return get_result_file_content(path=constants.epg_result_path, file_type="xml", show_content=False)


@app.route("/epg/epg.gz")
def show_epg_gz():
    return get_result_file_content(path=constants.epg_gz_result_path, file_type="gz", show_content=False)


@app.route("/log/result")
def show_result_log():
    if os.path.exists(constants.result_log_path):
        with open(constants.result_log_path, "r", encoding="utf-8") as file:
            content = file.read()
    else:
        content = constants.waiting_tip
    response = make_response(content)
    response.mimetype = "text/plain"
    return response


@app.route("/log/speed-test")
def show_speed_log():
    if os.path.exists(constants.speed_test_log_path):
        with open(constants.speed_test_log_path, "r", encoding="utf-8") as file:
            content = file.read()
    else:
        content = constants.waiting_tip
    response = make_response(content)
    response.mimetype = "text/plain"
    return response


@app.route("/log/statistic")
def show_statistic_log():
    if os.path.exists(constants.statistic_log_path):
        with open(constants.statistic_log_path, "r", encoding="utf-8") as file:
            content = file.read()
    else:
        content = constants.waiting_tip
    response = make_response(content)
    response.mimetype = "text/plain"
    return response


@app.route("/log/nomatch")
def show_nomatch_log():
    if os.path.exists(constants.nomatch_log_path):
        with open(constants.nomatch_log_path, "r", encoding="utf-8") as file:
            content = file.read()
    else:
        content = constants.waiting_tip
    response = make_response(content)
    response.mimetype = "text/plain"
    return response


@app.route('/hls_proxy/<channel_id>', methods=['GET'])
def hls_proxy(channel_id):
    if not channel_id:
        return jsonify({'Error': 'Channel id is required'}), 400

    channel_file = f'{channel_id}.m3u8'
    m3u8_path = os.path.join(hls_temp_path, channel_file)

    need_start = False
    with STREAMS_LOCK:
        proc = hls_running_streams.get(channel_id)
        if not proc or proc.poll() is not None:
            need_start = True
            if channel_id in hls_running_streams:
                hls_running_streams.pop(channel_id, None)

    if need_start:
        host = f"{app_rtmp_url}/hls"
        start_hls_to_rtmp(host, channel_id)

    hls_min_segments = 5
    waited = 0.0
    while waited < HLS_WAIT_TIMEOUT:
        if os.path.exists(m3u8_path):
            try:
                with open(m3u8_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                segment_count = content.count('#EXTINF')
                ends_with_discont = content.rstrip().endswith('#EXT-X-DISCONTINUITY')
                if segment_count >= hls_min_segments and not ends_with_discont:
                    break
            except Exception as e:
                print(f"❌ Read m3u8 while waiting failed for {channel_id}: {e}")
        time.sleep(HLS_WAIT_INTERVAL)
        waited += HLS_WAIT_INTERVAL

    if not os.path.exists(m3u8_path):
        return jsonify({'Error': 'HLS m3u8 not ready'}), 504

    try:
        with open(m3u8_path, 'rb') as f:
            data = f.read()
    except Exception as e:
        print(f"❌ Read m3u8 failed for {channel_id}: {e}")
        return jsonify({'Error': 'Failed to read m3u8'}), 500

    now = time.time()
    with STREAMS_LOCK:
        hls_last_access[channel_id] = now

    return Response(data, mimetype='application/vnd.apple.mpegurl')


@app.post('/on_publish')
def on_publish():
    form = request.form
    stream_id = form.get('name', '')

    print(f'RTMP publish: stream_id={stream_id}')
    return ''


@app.post('/on_done')
def on_done():
    form = request.form
    stream_id = form.get('name', '')

    print(f'RTMP done: stream_id={stream_id}')
    return ''


def run_service():
    try:
        if not os.getenv("GITHUB_ACTIONS"):
            if config.open_rtmp and sys.platform == "win32":
                start_rtmp_service()
            public_url = get_public_url()
            print(f"📄 Speed test log: {public_url}/log")
            if config.open_rtmp:
                print(f"🚀 RTMP api: {public_url}/hls")
            print(f"🚀 IPv4 api: {public_url}/ipv4")
            print(f"🚀 IPv6 api: {public_url}/ipv6")
            print(f"✅ You can use this url to watch IPTV 📺: {public_url}")
            app.run(host="0.0.0.0", port=config.app_port)
    except Exception as e:
        print(f"❌ Service start failed: {e}")


if __name__ == "__main__":
    if config.open_rtmp:
        if sys.platform == "win32":
            atexit.register(stop_rtmp_service)
        idle_thread = threading.Thread(target=hls_idle_monitor, daemon=True)
        idle_thread.start()
    run_service()
