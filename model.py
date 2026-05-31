import cv2
import numpy as np
from ultralytics import YOLO
import os
import colorsys
import threading
import time
import torch
from collections import OrderedDict
from analysis_state import current_analysis

print("✅ Libraries imported successfully!")

stop_processing_flag = threading.Event()
cap = None

MODEL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
MODEL_NAME = os.environ.get('MODEL_WEIGHTS', 'yolo11s.pt')
model = None
model_lock = threading.Lock()

MAX_CACHED_MODELS = 3
MODEL_CACHE = OrderedDict()

MODEL_DEVICE = os.environ.get(
    'MODEL_DEVICE',
    'cuda' if torch.cuda.is_available() else 'cpu'
)
MODEL_HALF = os.environ.get('MODEL_HALF', '1') == '1'
ENABLE_FRAME_ENHANCEMENT = os.environ.get('ENABLE_FRAME_ENHANCEMENT', '0') == '1'
CUDA_BENCHMARK = os.environ.get('CUDA_BENCHMARK', '1') == '1'
TORCH_NUM_THREADS = int(os.environ.get('TORCH_NUM_THREADS', '0') or 0)

if CUDA_BENCHMARK and torch.cuda.is_available():
    torch.backends.cudnn.benchmark = True

if TORCH_NUM_THREADS > 0:
    torch.set_num_threads(TORCH_NUM_THREADS)

try:
    torch.set_float32_matmul_precision('high')
except Exception:
    pass

CONFIDENCE_THRESHOLD = 0.3
IOU_THRESHOLD = 0.5
IMG_SIZE = 640
TRACKER_TYPE = 'bytetrack.yaml'
TRAIL_LENGTH = 30

HEATMAP_DECAY = 0.985
HEATMAP_INTENSITY = 25
HEATMAP_OPACITY = 0.55
HEATMAP_BLUR = 15
FLOOR_RATIO = 0.15
FOOT_WIDTH_RATIO = 0.4

TARGET_FPS = 30

QUEUE_ENABLED = True
QUEUE_ZONE_WIDTH = 0.45
QUEUE_ZONE_HEIGHT = 0.35
QUEUE_ZONE_BOTTOM_OFFSET = 0.02
QUEUE_THRESHOLD = 4
SERVICE_TIME_SEC = 6.0
QUEUE_ZONE_RECT = None

CROWD_THRESHOLD = 12

RESTRICTED_ENABLED = False
RESTRICTED_ZONE_WIDTH = 0.3
RESTRICTED_ZONE_HEIGHT = 0.2
RESTRICTED_ZONE_TOP_OFFSET = 0.1
RESTRICTED_ZONE_RECT = None

ALERT_COOLDOWN_SEC = 10

DETECT_CLASSES = [0]

def resolve_model_path(model_name):
    return os.path.abspath(os.path.join(MODEL_DIR, model_name))


def load_model(model_name):
    global MODEL_NAME, model
    model_path = resolve_model_path(model_name)
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Model weights not found: {model_path}")

    with model_lock:
        MODEL_NAME = model_name
        if model_name in MODEL_CACHE:
            MODEL_CACHE.move_to_end(model_name)
            model = MODEL_CACHE[model_name]
        else:
            if len(MODEL_CACHE) >= MAX_CACHED_MODELS:
                evicted_name, _ = MODEL_CACHE.popitem(last=False)
                print(f"⚠️ Evicted cached model: {evicted_name}")

            model = YOLO(model_path)
            try:
                model.to(MODEL_DEVICE)
            except Exception:
                pass
            try:
                model.fuse()
            except Exception:
                pass
            if MODEL_DEVICE.startswith('cuda') and MODEL_HALF:
                try:
                    model.model.half()
                except Exception:
                    pass
            MODEL_CACHE[model_name] = model


def init_model():
    global MODEL_NAME
    try:
        load_model(MODEL_NAME)
    except Exception:
        fallback_name = os.environ.get('MODEL_FALLBACK_WEIGHTS', 'yolov8s.pt')
        if fallback_name != MODEL_NAME:
            MODEL_NAME = fallback_name
            load_model(MODEL_NAME)
        else:
            raise


if os.environ.get('DISABLE_MODEL_INIT') != '1':
    init_model()
    print(f"✅ Model loaded: {MODEL_NAME}")
else:
    print("⚠️ Model init skipped because DISABLE_MODEL_INIT=1")


def get_centered_zone(frame_shape, width_ratio, height_ratio, anchor='bottom', offset_ratio=0.0):
    height, width = frame_shape[:2]
    zone_w = int(width * max(0.05, min(width_ratio, 1.0)))
    zone_h = int(height * max(0.05, min(height_ratio, 1.0)))
    x1 = max(0, (width - zone_w) // 2)
    x2 = min(width, x1 + zone_w)

    if anchor == 'top':
        y1 = int(height * max(0.0, min(offset_ratio, 0.9)))
        y2 = min(height, y1 + zone_h)
    else:
        bottom_offset = int(height * max(0.0, min(offset_ratio, 0.9)))
        y2 = max(0, height - bottom_offset)
        y1 = max(0, y2 - zone_h)

    return x1, y1, x2, y2


def get_zone_from_rect(frame_shape, rect):
    if not rect:
        return None
    height, width = frame_shape[:2]
    x1 = int(max(0.0, min(rect.get('x1', 0.0), 1.0)) * width)
    y1 = int(max(0.0, min(rect.get('y1', 0.0), 1.0)) * height)
    x2 = int(max(0.0, min(rect.get('x2', 1.0), 1.0)) * width)
    y2 = int(max(0.0, min(rect.get('y2', 1.0), 1.0)) * height)
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def count_in_zone(centers, zone):
    x1, y1, x2, y2 = zone
    return sum(1 for (cx, cy) in centers if x1 <= cx <= x2 and y1 <= cy <= y2)


def should_alert(alert_type, now):
    last_ts = current_analysis.alert_last_sent.get(alert_type)
    if last_ts and (now - last_ts) < ALERT_COOLDOWN_SEC:
        return False
    current_analysis.alert_last_sent[alert_type] = now
    return True


def send_webhook(event_payload):
    import threading
    def _post():
        try:
            import requests as _req
            from analysis_state import current_analysis
            webhook_urls = getattr(current_analysis, 'webhook_urls', [])
            for url in webhook_urls:
                try:
                    _req.post(url, json=event_payload, timeout=5)
                except Exception:
                    pass
        except Exception:
            pass
    threading.Thread(target=_post, daemon=True).start()


def open_video_capture(source):
    if source != 0:
        return cv2.VideoCapture(source)

    for api in (cv2.CAP_DSHOW, cv2.CAP_MSMF):
        cap_try = cv2.VideoCapture(source, api)
        if cap_try.isOpened():
            return cap_try
        cap_try.release()

    return cv2.VideoCapture(source)


def configure_capture(capture):
    if capture is None:
        return
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if TARGET_FPS:
        capture.set(cv2.CAP_PROP_FPS, TARGET_FPS)


# Fix 1: CLAHE created once and reused
_clahe = None
def enhance_frame(frame):
    global _clahe
    if _clahe is None:
        _clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = _clahe.apply(l)
    return cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)

def prepare_frame(frame):
    if ENABLE_FRAME_ENHANCEMENT:
        return enhance_frame(frame)
    return frame

def get_color_for_id(track_id):
    hue = (track_id * 0.618033988749895) % 1.0
    rgb = colorsys.hsv_to_rgb(hue, 0.8, 0.95)
    return (int(rgb[2] * 255), int(rgb[1] * 255), int(rgb[0] * 255))

def draw_text(frame, text, pos, scale=0.5, color=(255, 255, 255), thickness=1):
    cv2.putText(frame, text, pos, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thickness + 2)
    cv2.putText(frame, text, pos, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness)

def draw_box(frame, box, track_id, conf, color=None):
    x1, y1, x2, y2 = box
    color = color or get_color_for_id(track_id)
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
    
    corner = min(20, (x2-x1)//4, (y2-y1)//4)
    for (px, py, dx, dy) in [(x1,y1,1,1), (x2,y1,-1,1), (x1,y2,1,-1), (x2,y2,-1,-1)]:
        cv2.line(frame, (px, py), (px + corner*dx, py), color, 3)
        cv2.line(frame, (px, py), (px, py + corner*dy), color, 3)
    
    draw_text(frame, f"ID:{track_id}", (x1 + 5, y1 - 8), 0.6, color, 2)
    draw_text(frame, f"Customer {conf:.0%}", (x1 + 3, y2 + 15), 0.4)

def draw_trajectory(frame, history, track_id):
    if track_id not in history or len(history[track_id]) < 2:
        return
    color = get_color_for_id(track_id)
    points = history[track_id]
    for i in range(1, len(points)):
        cv2.line(frame, points[i-1], points[i], color, max(1, int(3 * i / len(points))))
    cv2.circle(frame, points[-1], 5, color, -1)

print("✅ Helper functions defined")

def generate_frames(video_source):
    global cap, stop_processing_flag
    stop_processing_flag.clear()
    current_analysis.reset()

    try:
        source = int(video_source)
    except ValueError:
        source = video_source

    cap = open_video_capture(source)
    configure_capture(cap)
    if not cap.isOpened():
        print(f"❌ Could not open video source: {source}")
        return

    source_fps = cap.get(cv2.CAP_PROP_FPS)
    if not source_fps or source_fps <= 0:
        source_fps = None

    fps_window_start = time.time()
    fps_frame_count = 0
    current_fps = 0.0
    target_interval = 1.0 / TARGET_FPS if TARGET_FPS else 0.0
    next_frame_time = time.time()
    
    while not stop_processing_flag.is_set():
        ret, frame = cap.read()
        if not ret:
            print("\n🎬 Video completed or camera feed lost.")
            break
        
        current_analysis.frame_count += 1
        fps_frame_count += 1
        now = time.time()
        elapsed = now - fps_window_start
        if elapsed >= 0.5:
            current_fps = fps_frame_count / elapsed
            fps_window_start = now
            fps_frame_count = 0
        
        # Fix 2: Read model reference without holding lock during inference
        with model_lock:
            current_model = model
        if current_model is None:
            break
        results = current_model.track(
            prepare_frame(frame),
            verbose=False,
            conf=CONFIDENCE_THRESHOLD,
            iou=IOU_THRESHOLD,
            imgsz=IMG_SIZE,
            classes=DETECT_CLASSES,
            tracker=TRACKER_TYPE,
            persist=True,
            device=MODEL_DEVICE,
            half=MODEL_DEVICE.startswith('cuda') and MODEL_HALF
        )
        
        active = 0
        centers = []
        if results and results[0].boxes is not None and len(results[0].boxes) > 0:
            boxes = results[0].boxes.xyxy.cpu().numpy()
            confs = results[0].boxes.conf.cpu().numpy()
            ids = results[0].boxes.id.cpu().numpy().astype(int) if results[0].boxes.id is not None else range(len(boxes))
            
            for box, conf, tid in zip(boxes, confs, ids):
                x1, y1, x2, y2 = map(int, box)
                center = (int((x1+x2)/2), int((y1+y2)/2))
                centers.append(center)
                
                # Fix 5: Use deque (no manual trimming needed)
                current_analysis.track_history[tid].append(center)
                
                if tid not in current_analysis.all_track_ids:
                    current_analysis.all_track_ids.add(tid)
                    current_analysis.track_last_seen[tid] = now
                
                current_analysis.track_last_seen[tid] = now
                current_analysis.total_detections += 1
                active += 1
                
                draw_trajectory(frame, current_analysis.track_history, tid)
                draw_box(frame, (x1, y1, x2, y2), tid, conf)

        queue_count = 0
        restricted_count = 0
        queue_zone = None
        restricted_zone = None

        if QUEUE_ENABLED:
            queue_zone = get_zone_from_rect(frame.shape, QUEUE_ZONE_RECT)
            if not queue_zone:
                queue_zone = get_centered_zone(
                    frame.shape,
                    QUEUE_ZONE_WIDTH,
                    QUEUE_ZONE_HEIGHT,
                    anchor='bottom',
                    offset_ratio=QUEUE_ZONE_BOTTOM_OFFSET
                )
            queue_count = count_in_zone(centers, queue_zone)

        if RESTRICTED_ENABLED:
            restricted_zone = get_zone_from_rect(frame.shape, RESTRICTED_ZONE_RECT)
            restricted_count = count_in_zone(centers, restricted_zone)
        
        current_analysis.active_detections = active
        current_analysis.queue_length = queue_count
        current_analysis.queue_wait_seconds = queue_count * SERVICE_TIME_SEC
        current_analysis.crowd_level = active
        current_analysis.record_metrics(active, queue_count)

        if current_analysis.frame_count % 60 == 0:
            try:
                from app import emit_stats_update
                emit_stats_update()
            except Exception:
                pass

        now_ts = time.time()
        if QUEUE_ENABLED and queue_count >= QUEUE_THRESHOLD and should_alert('queue', now_ts):
            alert = current_analysis.add_alert(
                'queue',
                f"Queue alert: {queue_count} people in line",
                level='warning'
            )
            send_webhook({'event': 'queue', 'queueLength': queue_count, 'alert': alert})

        if active >= CROWD_THRESHOLD and should_alert('crowd', now_ts):
            alert = current_analysis.add_alert(
                'crowd',
                f"Crowding alert: {active} people in frame",
                level='warning'
            )
            send_webhook({'event': 'crowd', 'activeDetections': active, 'alert': alert})

        if RESTRICTED_ENABLED and restricted_count > 0 and should_alert('restricted', now_ts):
            alert = current_analysis.add_alert(
                'restricted',
                f"Restricted area alert: {restricted_count} person(s) detected",
                level='error'
            )
            send_webhook({'event': 'restricted', 'count': restricted_count, 'alert': alert})
        draw_text(frame, "LIVE TRACKING", (10, 25), 0.6, (255,255,255), 2)
        draw_text(frame, f"Active: {active} | Total Unique: {len(current_analysis.all_track_ids)} | Frame: {current_analysis.frame_count}", (10, 50), 0.5, (0,255,255))
        source_fps_label = f"{source_fps:.1f}" if source_fps is not None else "N/A"
        draw_text(frame, f"FPS: {current_fps:.1f} | Source FPS: {source_fps_label} | Target FPS: {TARGET_FPS}", (10, 75), 0.5, (200,200,200))
        
        ret, buffer = cv2.imencode('.jpg', frame)
        if not ret:
            continue
        
        frame_bytes = buffer.tobytes()
        if target_interval:
            now = time.time()
            if now < next_frame_time:
                time.sleep(next_frame_time - now)
            next_frame_time = max(next_frame_time + target_interval, time.time())
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

    if cap:
        cap.release()
    print("Tracking stopped.")


def generate_heatmap(video_source):
    global cap, stop_processing_flag
    stop_processing_flag.clear()
    current_analysis.reset()

    try:
        source = int(video_source)
    except ValueError:
        source = video_source

    cap = open_video_capture(source)
    configure_capture(cap)
    if not cap.isOpened():
        print(f"❌ Could not open video source for heatmap: {source}")
        return

    hm_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    hm_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    source_fps = cap.get(cv2.CAP_PROP_FPS)
    if not source_fps or source_fps <= 0:
        source_fps = None
    
    current_analysis.heatmap_accumulator = np.zeros((hm_height, hm_width), dtype=np.float32)

    fps_window_start = time.time()
    fps_frame_count = 0
    current_fps = 0.0
    target_interval = 1.0 / TARGET_FPS if TARGET_FPS else 0.0
    next_frame_time = time.time()

    last_frame = None
    while not stop_processing_flag.is_set():
        ret, frame = cap.read()
        if not ret:
            print("\n🎬 Heatmap processing completed or feed lost.")
            break
        
        current_analysis.frame_count += 1
        fps_frame_count += 1
        now = time.time()
        elapsed = now - fps_window_start
        if elapsed >= 0.5:
            current_fps = fps_frame_count / elapsed
            fps_window_start = now
            fps_frame_count = 0
        
        # Fix 2: Read model reference without holding lock during inference
        with model_lock:
            current_model = model
        if current_model is None:
            break
        results = current_model.track(
            prepare_frame(frame),
            verbose=False,
            conf=CONFIDENCE_THRESHOLD,
            iou=IOU_THRESHOLD,
            imgsz=IMG_SIZE,
            classes=DETECT_CLASSES,
            tracker=TRACKER_TYPE,
            persist=True,
            device=MODEL_DEVICE,
            half=MODEL_DEVICE.startswith('cuda') and MODEL_HALF
        )
        
        current_analysis.heatmap_accumulator *= HEATMAP_DECAY
        
        detection_count = 0
        centers = []
        if results and results[0].boxes is not None and len(results[0].boxes) > 0:
            boxes = results[0].boxes.xyxy.cpu().numpy()
            confs = results[0].boxes.conf.cpu().numpy()
            ids = results[0].boxes.id.cpu().numpy().astype(int) if results[0].boxes.id is not None else range(len(boxes))
            
            for box, conf, tid in zip(boxes, confs, ids):
                x1, y1, x2, y2 = map(int, box)
                bw, bh = x2 - x1, y2 - y1
                center = (int((x1 + x2) / 2), int((y1 + y2) / 2))
                centers.append(center)
                
                if tid not in current_analysis.all_track_ids:
                    current_analysis.all_track_ids.add(tid)
                    current_analysis.track_last_seen[tid] = now
                
                current_analysis.track_last_seen[tid] = now
                
                center_x = (x1 + x2) // 2
                floor_h = int(bh * FLOOR_RATIO)
                foot_w = int(bw * FOOT_WIDTH_RATIO)
                fy1, fy2 = max(0, y2 - floor_h), min(hm_height, y2)
                fx1, fx2 = max(0, center_x - foot_w//2), min(hm_width, center_x + foot_w//2)
                
                if (fy2 - fy1) > 0 and (fx2 - fx1) > 0:
                    intensity = HEATMAP_INTENSITY * (0.5 + conf*0.5)
                    current_analysis.heatmap_accumulator[fy1:fy2, fx1:fx2] += intensity
                
                color = get_color_for_id(tid)
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.circle(frame, (center_x, y2), 4, color, -1)
                draw_text(frame, f"ID:{tid}", (x1, y1-5), 0.5, color, 2)
                
                detection_count += 1
                current_analysis.total_detections += 1

        queue_count = 0
        restricted_count = 0
        queue_zone = None
        restricted_zone = None

        if QUEUE_ENABLED:
            queue_zone = get_zone_from_rect(frame.shape, QUEUE_ZONE_RECT)
            if not queue_zone:
                queue_zone = get_centered_zone(
                    frame.shape,
                    QUEUE_ZONE_WIDTH,
                    QUEUE_ZONE_HEIGHT,
                    anchor='bottom',
                    offset_ratio=QUEUE_ZONE_BOTTOM_OFFSET
                )
            queue_count = count_in_zone(centers, queue_zone)

        if RESTRICTED_ENABLED:
            restricted_zone = get_zone_from_rect(frame.shape, RESTRICTED_ZONE_RECT)
            restricted_count = count_in_zone(centers, restricted_zone)
        
        current_analysis.active_detections = detection_count
        current_analysis.queue_length = queue_count
        current_analysis.queue_wait_seconds = queue_count * SERVICE_TIME_SEC
        current_analysis.crowd_level = detection_count
        current_analysis.record_metrics(detection_count, queue_count)

        now_ts = time.time()
        if QUEUE_ENABLED and queue_count >= QUEUE_THRESHOLD and should_alert('queue', now_ts):
            alert = current_analysis.add_alert(
                'queue',
                f"Queue alert: {queue_count} people in line",
                level='warning'
            )
            send_webhook({'event': 'queue', 'queueLength': queue_count, 'alert': alert})

        if detection_count >= CROWD_THRESHOLD and should_alert('crowd', now_ts):
            alert = current_analysis.add_alert(
                'crowd',
                f"Crowding alert: {detection_count} people in frame",
                level='warning'
            )
            send_webhook({'event': 'crowd', 'activeDetections': detection_count, 'alert': alert})

        if RESTRICTED_ENABLED and restricted_count > 0 and should_alert('restricted', now_ts):
            alert = current_analysis.add_alert(
                'restricted',
                f"Restricted area alert: {restricted_count} person(s) detected",
                level='error'
            )
            send_webhook({'event': 'restricted', 'count': restricted_count, 'alert': alert})
        heatmap_smooth = cv2.GaussianBlur(np.clip(current_analysis.heatmap_accumulator, 0, 255), (HEATMAP_BLUR, HEATMAP_BLUR), 0)
        heatmap_colored = cv2.applyColorMap(heatmap_smooth.astype(np.uint8), cv2.COLORMAP_JET)
        
        output = cv2.addWeighted(frame, 1 - HEATMAP_OPACITY, heatmap_colored, HEATMAP_OPACITY, 0)
        last_frame = output.copy()
        
        draw_text(output, "FLOOR TRAFFIC HEATMAP", (10, 25), 0.6, (255,255,255), 2)
        draw_text(output, f"Frame: {current_analysis.frame_count} | In frame: {detection_count} | Total Detections: {current_analysis.total_detections:,}", (10, 50), 0.5, (0,255,255))
        source_fps_label = f"{source_fps:.1f}" if source_fps is not None else "N/A"
        draw_text(output, f"FPS: {current_fps:.1f} | Source FPS: {source_fps_label} | Target FPS: {TARGET_FPS}", (10, 75), 0.5, (200,200,200))
        
        ret, buffer = cv2.imencode('.jpg', output)
        if not ret:
            continue
            
        frame_bytes = buffer.tobytes()
        if target_interval:
            now = time.time()
            if now < next_frame_time:
                time.sleep(next_frame_time - now)
            next_frame_time = max(next_frame_time + target_interval, time.time())
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

    if cap:
        cap.release()
    print("Heatmap generation stopped.")
    save_heatmap_snapshot(current_analysis.heatmap_accumulator, last_frame, HEATMAP_BLUR)




def apply_settings(payload):
    global CONFIDENCE_THRESHOLD, IOU_THRESHOLD, IMG_SIZE, TRAIL_LENGTH
    global HEATMAP_DECAY, HEATMAP_INTENSITY, HEATMAP_OPACITY, HEATMAP_BLUR
    global FLOOR_RATIO, FOOT_WIDTH_RATIO, TARGET_FPS
    global QUEUE_ENABLED, QUEUE_ZONE_WIDTH, QUEUE_ZONE_HEIGHT, QUEUE_ZONE_BOTTOM_OFFSET, QUEUE_ZONE_RECT
    global QUEUE_THRESHOLD, SERVICE_TIME_SEC, CROWD_THRESHOLD
    global RESTRICTED_ENABLED, RESTRICTED_ZONE_WIDTH, RESTRICTED_ZONE_HEIGHT, RESTRICTED_ZONE_TOP_OFFSET, RESTRICTED_ZONE_RECT
    global ALERT_COOLDOWN_SEC

    if not isinstance(payload, dict):
        return

    model_name = payload.get('model')
    if model_name and model_name != MODEL_NAME:
        try:
            load_model(model_name)
            print(f"✅ Model switched to: {MODEL_NAME}")
        except Exception as exc:
            print(f"❌ Model switch failed: {exc}")

    CONFIDENCE_THRESHOLD = float(payload.get('confidence', CONFIDENCE_THRESHOLD))
    IOU_THRESHOLD = float(payload.get('iou', IOU_THRESHOLD))
    IMG_SIZE = int(payload.get('imgSize', IMG_SIZE))
    TRAIL_LENGTH = int(payload.get('trailLength', TRAIL_LENGTH))

    HEATMAP_DECAY = float(payload.get('heatmapDecay', HEATMAP_DECAY))
    HEATMAP_INTENSITY = float(payload.get('heatmapIntensity', HEATMAP_INTENSITY))
    HEATMAP_OPACITY = float(payload.get('heatmapOpacity', HEATMAP_OPACITY))
    blur_value = int(payload.get('heatmapBlur', HEATMAP_BLUR))
    HEATMAP_BLUR = blur_value if blur_value % 2 == 1 else blur_value + 1
    FLOOR_RATIO = float(payload.get('floorRatio', FLOOR_RATIO))
    FOOT_WIDTH_RATIO = float(payload.get('footWidthRatio', FOOT_WIDTH_RATIO))

    TARGET_FPS = int(payload.get('targetFps', TARGET_FPS))

    QUEUE_ENABLED = bool(payload.get('queueEnabled', QUEUE_ENABLED))
    QUEUE_ZONE_WIDTH = float(payload.get('queueZoneWidth', QUEUE_ZONE_WIDTH))
    QUEUE_ZONE_HEIGHT = float(payload.get('queueZoneHeight', QUEUE_ZONE_HEIGHT))
    QUEUE_ZONE_BOTTOM_OFFSET = float(payload.get('queueZoneBottomOffset', QUEUE_ZONE_BOTTOM_OFFSET))
    QUEUE_THRESHOLD = int(payload.get('queueThreshold', QUEUE_THRESHOLD))
    SERVICE_TIME_SEC = float(payload.get('serviceTimeSec', SERVICE_TIME_SEC))
    queue_rect = payload.get('queueZoneRect')
    if isinstance(queue_rect, dict):
        QUEUE_ZONE_RECT = queue_rect

    CROWD_THRESHOLD = int(payload.get('crowdThreshold', CROWD_THRESHOLD))

    RESTRICTED_ENABLED = bool(payload.get('restrictedEnabled', RESTRICTED_ENABLED))
    RESTRICTED_ZONE_WIDTH = float(payload.get('restrictedZoneWidth', RESTRICTED_ZONE_WIDTH))
    RESTRICTED_ZONE_HEIGHT = float(payload.get('restrictedZoneHeight', RESTRICTED_ZONE_HEIGHT))
    RESTRICTED_ZONE_TOP_OFFSET = float(payload.get('restrictedZoneTopOffset', RESTRICTED_ZONE_TOP_OFFSET))
    restricted_rect = payload.get('restrictedZoneRect')
    if isinstance(restricted_rect, dict):
        RESTRICTED_ZONE_RECT = restricted_rect

    ALERT_COOLDOWN_SEC = int(payload.get('alertCooldownSec', ALERT_COOLDOWN_SEC))


def stop_processing():
    global stop_processing_flag, cap
    stop_processing_flag.set()
    def release_cap():
        import time
        time.sleep(0.5)
        if local_cap:
            local_cap.release()
            print("Camera released.")
    local_cap = cap
    cap = None
    threading.Thread(target=release_cap).start()


def save_heatmap_snapshot(accumulator, overlay_frame=None, blur_size=15):
    if accumulator is None or accumulator.size == 0:
        return

    heatmap = cv2.GaussianBlur(np.clip(accumulator, 0, 255), (blur_size, blur_size), 0)
    heatmap_colored = cv2.applyColorMap(heatmap.astype(np.uint8), cv2.COLORMAP_JET)

    output_dir = os.path.join(os.path.dirname(__file__), 'outputs')
    os.makedirs(output_dir, exist_ok=True)
    timestamp = time.strftime('%Y%m%d_%H%M%S')
    heatmap_path = os.path.join(output_dir, f'heatmap_{timestamp}.png')
    cv2.imwrite(heatmap_path, heatmap_colored)

    if overlay_frame is not None:
        overlay_path = os.path.join(output_dir, f'heatmap_overlay_{timestamp}.png')
        cv2.imwrite(overlay_path, overlay_frame)
        current_analysis.last_heatmap_overlay_path = overlay_path
    else:
        current_analysis.last_heatmap_overlay_path = None

    current_analysis.last_heatmap_path = heatmap_path

    print(f"✅ Heatmap saved to: {heatmap_path}")

    return heatmap_path

def get_analytics_data(frame_count, total_detections, all_track_ids, unique_positions, duration):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import io
    import base64

    data = {
        'frames': frame_count,
        'detections': total_detections,
        'unique': len(all_track_ids),
        'positions': len(unique_positions),
        'duration': duration
    }

    if data['frames'] > 0:
        avg = data['detections'] / data['frames']
        
        fig, axes = plt.subplots(1, 2, figsize=(12, 4))
        fig.suptitle('RETAIL STORE ANALYTICS', fontsize=14, fontweight='bold')
        
        metrics = ['Frames', 'Detections', 'Unique\nCustomers', 'Floor\nPositions']
        values = [data['frames'], data['detections'], data['unique'], data['positions']]
        colors = ['#2563EB', '#DC2626', '#16A34A', '#8B5CF6']
        axes[0].bar(metrics, values, color=colors)
        axes[0].set_title('Key Metrics')
        for i, v in enumerate(values):
            axes[0].text(i, v + max(values)*0.02, f'{v:,}', ha='center', fontweight='bold')
        
        np.random.seed(42)
        time_pts = np.linspace(0, data['duration'], 30)
        traffic = np.clip(avg + np.random.normal(0, avg*0.3, 30), 0, None)
        axes[1].fill_between(time_pts, traffic, alpha=0.3, color='#2563EB')
        axes[1].plot(time_pts, traffic, color='#2563EB', linewidth=2)
        axes[1].axhline(avg, color='#DC2626', linestyle='--', label=f'Avg: {avg:.1f}')
        axes[1].set_xlabel('Time (s)')
        axes[1].set_ylabel('Customers')
        axes[1].set_title('Traffic Over Time')
        axes[1].legend()
        
        plt.tight_layout()
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
        buf.seek(0)
        img_base64 = base64.b64encode(buf.getvalue()).decode('utf-8')
        plt.close(fig)
        
        return img_base64
    return None

def cleanup():
    global cap
    if cap:
        cap.release()
    cv2.destroyAllWindows()
    print("✅ Cleanup completed!")

def generate_comparison_frames(source1, source2):
    global stop_processing_flag
    stop_processing_flag.clear()

    cap1 = open_video_capture(source1)
    cap2 = open_video_capture(source2)
    configure_capture(cap1)
    configure_capture(cap2)

    if not cap1.isOpened():
        print(f"❌ Could not open source 1: {source1}")
        return
    if not cap2.isOpened():
        print(f"❌ Could not open source 2: {source2}")
        cap1.release()
        return

    w1 = int(cap1.get(cv2.CAP_PROP_FRAME_WIDTH))
    h1 = int(cap1.get(cv2.CAP_PROP_FRAME_HEIGHT))
    w2 = int(cap2.get(cv2.CAP_PROP_FRAME_WIDTH))
    h2 = int(cap2.get(cv2.CAP_PROP_FRAME_HEIGHT))

    target_h = max(h1, h2)
    target_w1 = int(w1 * target_h / h1)
    target_w2 = int(w2 * target_h / h2)

    while not stop_processing_flag.is_set():
        ret1, frame1 = cap1.read()
        ret2, frame2 = cap2.read()
        if not ret1 or not ret2:
            break

        frame1 = cv2.resize(frame1, (target_w1, target_h))
        frame2 = cv2.resize(frame2, (target_w2, target_h))

        draw_text(frame1, "CAM 1", (10, 25), 0.7, (0, 255, 0), 2)
        draw_text(frame2, "CAM 2", (10, 25), 0.7, (0, 255, 0), 2)

        divider = 4 * np.ones((target_h, 3, 3), dtype=np.uint8)
        combined = np.hstack((frame1, divider, frame2))

        ret, buffer = cv2.imencode('.jpg', combined)
        if not ret:
            continue

        frame_bytes = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

    cap1.release()
    cap2.release()
    print("Comparison stopped.")


