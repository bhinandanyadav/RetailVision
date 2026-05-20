from flask import Flask, render_template, Response, request, jsonify, send_file, session, redirect, url_for
import os
import base64
import io
import csv
import time
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from model import generate_frames, generate_heatmap, stop_processing, get_analytics_data, apply_settings
from analysis_state import current_analysis

app = Flask(__name__)
app.secret_key = os.environ.get('STORE_TRACKER_SECRET', 'store-tracker-dev-key')

ADMIN_PASSWORD = os.environ.get('STORE_TRACKER_ADMIN_PASSWORD', 'admin123')
VIEWER_PASSWORD = os.environ.get('STORE_TRACKER_VIEWER_PASSWORD', 'viewer123')

current_source_key = '0'
camera_stats_cache = {}

video_source = 0  # default webcam
current_mode = 'tracking'  # 'tracking' or 'heatmap'

def get_role():
    return session.get('role')


def require_login():
    return get_role() is not None


@app.route("/")
def home():
    if not require_login():
        return redirect(url_for('login'))
    return render_template("index.html", user_role=get_role())


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        role = request.form.get('role')
        password = request.form.get('password', '')
        if role == 'admin' and password == ADMIN_PASSWORD:
            session['role'] = 'admin'
            return redirect(url_for('home'))
        if role == 'viewer' and password == VIEWER_PASSWORD:
            session['role'] = 'viewer'
            return redirect(url_for('home'))
        return render_template('login.html', error='Invalid credentials')
    return render_template('login.html', error=None)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/video_feed')
def video_feed():
    if not require_login():
        return Response(status=401)
    global current_source_key
    source_param = request.args.get('source', '0')
    mode_param = request.args.get('mode', 'tracking')

    current_source = 0
    if source_param.isdigit():
        current_source = int(source_param)
        current_source_key = source_param
    else:
        video_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', source_param))
        if os.path.exists(video_path):
            current_source = video_path
            current_source_key = source_param
        else:
            print(f"Error: Video file not found at {video_path}")
            # Return an empty response or an error image
            return Response(status=404)

    if mode_param == 'heatmap':
        return Response(
            generate_heatmap(current_source),
            mimetype='multipart/x-mixed-replace; boundary=frame'
        )
    else:
        return Response(
            generate_frames(current_source),
            mimetype='multipart/x-mixed-replace; boundary=frame'
        )


@app.route('/switch_mode/<mode>', methods=['POST'])
def switch_mode(mode):
    global current_mode
    if not require_login():
        return jsonify({'status': 'error', 'message': 'Login required'}), 401
    
    if mode in ['tracking', 'heatmap']:
        stop_processing()
        current_mode = mode
        return jsonify({'status': 'success', 'mode': current_mode})
    
    return jsonify({'status': 'error', 'message': 'Invalid mode'})


@app.route('/stop', methods=['POST'])
def stop():
    if not require_login():
        return jsonify({'status': 'error', 'message': 'Login required'}), 401
    stop_processing()
    return jsonify({'status': 'success'})


@app.route('/update_settings', methods=['POST'])
def update_settings():
    if not require_login():
        return jsonify({'status': 'error', 'message': 'Login required'}), 401
    if get_role() != 'admin':
        return jsonify({'status': 'error', 'message': 'Admin access required'}), 403
    payload = request.get_json(silent=True) or {}
    apply_settings(payload)
    return jsonify({'status': 'success'})


@app.route('/get_stats', methods=['GET'])
def get_stats():
    """Endpoint to get analytics data from the last run."""
    try:
        if not require_login():
            return jsonify({'status': 'error', 'message': 'Login required'}), 401
        source_key = request.args.get('source')
        if source_key and source_key != current_source_key:
            cached = camera_stats_cache.get(source_key)
            if cached:
                return jsonify(cached)

        stats = current_analysis.get_stats()
        all_track_ids = current_analysis.all_track_ids
        
        all_positions = [pos for history in current_analysis.track_history.values() for pos in history]
        unique_positions = set(all_positions)

        img_base64 = get_analytics_data(
            stats['frameCount'],
            stats['totalDetections'],
            all_track_ids,
            unique_positions,
            stats['duration']
        )

        payload = {
            'status': 'success',
            'image': img_base64,
            'stats': stats,
            'alerts': current_analysis.alerts[-10:],
            'lastAlertId': current_analysis.alerts[-1]['id'] if current_analysis.alerts else 0
        }

        if source_key:
            camera_stats_cache[source_key] = payload

        if img_base64:
            return jsonify(payload)

        payload['image'] = None
        payload['message'] = 'Not enough data for chart yet.'
        return jsonify(payload)
    except Exception as e:
        print(f"Error getting stats: {e}")
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/get_last_heatmap', methods=['GET'])
def get_last_heatmap():
    try:
        if not require_login():
            return jsonify({'status': 'error', 'message': 'Login required'}), 401
        overlay_path = current_analysis.last_heatmap_overlay_path
        heatmap_path = current_analysis.last_heatmap_path

        image_path = overlay_path or heatmap_path
        if not image_path or not os.path.exists(image_path):
            return jsonify({'status': 'error', 'message': 'No saved heatmap found.'})

        with open(image_path, 'rb') as handle:
            img_base64 = base64.b64encode(handle.read()).decode('utf-8')

        return jsonify({
            'status': 'success',
            'image': img_base64,
            'filename': os.path.basename(image_path),
            'type': 'overlay' if overlay_path else 'heatmap'
        })
    except Exception as e:
        print(f"Error getting heatmap: {e}")
        return jsonify({'status': 'error', 'message': str(e)})


def build_report_summary():
    history = current_analysis.metric_history
    if not history:
        return {
            'duration': current_analysis.duration,
            'peak_active': 0,
            'peak_queue': 0,
            'peak_time': None,
            'avg_active': 0,
            'avg_queue': 0
        }

    peak_point = max(history, key=lambda entry: entry['active'])
    peak_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(peak_point['ts']))
    avg_active = sum(item['active'] for item in history) / len(history)
    avg_queue = sum(item['queue'] for item in history) / len(history)
    return {
        'duration': current_analysis.duration,
        'peak_active': peak_point['active'],
        'peak_queue': max(item['queue'] for item in history),
        'peak_time': peak_time,
        'avg_active': avg_active,
        'avg_queue': avg_queue
    }


@app.route('/export_report', methods=['GET'])
def export_report():
    if not require_login():
        return redirect(url_for('login'))
    report_range = request.args.get('range', 'daily')
    fmt = request.args.get('format', 'csv')
    stats = current_analysis.get_stats()
    summary = build_report_summary()

    if fmt == 'pdf':
        fig, ax = plt.subplots(figsize=(10, 5))
        history = current_analysis.metric_history
        if history:
            timestamps = [entry['ts'] for entry in history]
            start_ts = timestamps[0]
            rel_time = [(ts - start_ts) / 60 for ts in timestamps]
            active_series = [entry['active'] for entry in history]
            queue_series = [entry['queue'] for entry in history]
            ax.plot(rel_time, active_series, label='Active', color='#2563eb')
            ax.plot(rel_time, queue_series, label='Queue', color='#f97316')
            ax.set_xlabel('Minutes')
            ax.set_ylabel('People')
            ax.legend()
            ax.grid(alpha=0.2)

        fig.suptitle(f"Store Analytics Report ({report_range.title()})", fontsize=14, fontweight='bold')
        text_block = (
            f"Duration: {summary['duration']:.1f}s\n"
            f"Unique Customers: {stats['uniqueCustomers']}\n"
            f"Total Detections: {stats['totalDetections']}\n"
            f"Peak Active: {summary['peak_active']} @ {summary['peak_time']}\n"
            f"Avg Active: {summary['avg_active']:.1f}\n"
            f"Peak Queue: {summary['peak_queue']}\n"
            f"Avg Queue: {summary['avg_queue']:.1f}\n"
            f"Avg Detections per Customer: {stats['totalDetections'] / max(stats['uniqueCustomers'], 1):.2f}"
        )
        fig.text(0.02, 0.02, text_block, fontsize=9, va='bottom')
        buf = io.BytesIO()
        fig.tight_layout(rect=[0, 0.12, 1, 0.95])
        fig.savefig(buf, format='pdf')
        plt.close(fig)
        buf.seek(0)
        filename = f"report_{report_range}_{int(time.time())}.pdf"
        return send_file(buf, mimetype='application/pdf', as_attachment=True, download_name=filename)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([f"Store Analytics Report ({report_range.title()})"])
    writer.writerow([])
    writer.writerow(["Duration (s)", f"{summary['duration']:.1f}"])
    writer.writerow(["Unique Customers", stats['uniqueCustomers']])
    writer.writerow(["Total Detections", stats['totalDetections']])
    writer.writerow(["Peak Active", summary['peak_active']])
    writer.writerow(["Peak Queue", summary['peak_queue']])
    writer.writerow(["Peak Time", summary['peak_time'] or 'N/A'])
    writer.writerow(["Avg Active", f"{summary['avg_active']:.2f}"])
    writer.writerow(["Avg Queue", f"{summary['avg_queue']:.2f}"])
    writer.writerow(["Avg Detections per Customer", f"{stats['totalDetections'] / max(stats['uniqueCustomers'], 1):.2f}"])
    writer.writerow([])
    writer.writerow(["Time (s)", "Active", "Queue"])

    if current_analysis.metric_history:
        start_ts = current_analysis.metric_history[0]['ts']
        for entry in current_analysis.metric_history:
            writer.writerow([f"{entry['ts'] - start_ts:.1f}", entry['active'], entry['queue']])

    csv_bytes = io.BytesIO(output.getvalue().encode('utf-8'))
    csv_bytes.seek(0)
    filename = f"report_{report_range}_{int(time.time())}.csv"
    return send_file(csv_bytes, mimetype='text/csv', as_attachment=True, download_name=filename)
if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=5000)
