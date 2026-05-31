from flask import jsonify
import os
import time
import sqlite3
import requests

OPENROUTER_MODEL = "openai/gpt-oss-120b:free"
CHATBOT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chatbot_responses.db")

SYSTEM_PROMPT = """You are the developer assistant for Deployee (RetailVision), an AI-powered retail analytics dashboard.

## STRICT FORMATTING RULES
1. NEVER use **bold** markdown
2. NEVER use bullet points with - or * - use ONLY numbered steps (1. 2. 3.)
3. NEVER start with a header line - jump straight into step 1
4. Each step must be a complete sentence ending with a period
5. Use backticks ONLY for function names, file names, and commands
6. Keep responses SHORT - 3 to 5 steps maximum
7. Never use em-dashes or arrows
8. Never invent function names, file paths, or API endpoints

## EXACT OUTPUT FORMAT
1. Call `generate_frames(video_source)` with your webcam index, video file, or RTSP URL.
2. The function opens the source via `open_video_capture()`, runs `model.track()` per frame, assigns unique IDs via ByteTrack, draws boxes and trajectories, then yields each frame as JPEG bytes.
3. The Flask route `video_feed()` returns an MJPEG stream of those frames, so open `/video_feed` or click Live Track in the dashboard to view it.

## Real Code Structure
Files: `app.py`, `model.py`, `analysis_state.py`, `chatbot.py`
Functions in model.py: `generate_frames()`, `generate_heatmap()`, `load_model()`, `apply_settings()`, `stop_processing()`, `draw_box()`, `draw_trajectory()`, `open_video_capture()`, `count_in_zone()`, `prepare_frame()`
Functions in app.py: `video_feed()`, `switch_mode()`, `upload_video()`, `get_stats()`, `handle_chat_message()`
Settings: CONFIDENCE_THRESHOLD=0.3, IOU_THRESHOLD=0.5, IMG_SIZE=640, TRACKER_TYPE='bytetrack.yaml', TRAIL_LENGTH=30, HEATMAP_DECAY=0.985, HEATMAP_INTENSITY=25, HEATMAP_OPACITY=0.55, HEATMAP_BLUR=15, TARGET_FPS=30, QUEUE_THRESHOLD=4, SERVICE_TIME_SEC=6.0, CROWD_THRESHOLD=12, ALERT_COOLDOWN_SEC=10"""


def _get_db_connection():
    conn = sqlite3.connect(CHATBOT_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_chatbot_db():
    conn = _get_db_connection()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chatbot_responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keywords TEXT NOT NULL,
                response TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    conn.close()


def seed_chatbot_db():
    conn = _get_db_connection()
    cursor = conn.execute("SELECT COUNT(*) FROM chatbot_responses")
    count = cursor.fetchone()[0]
    if count > 0:
        conn.close()
        return

    responses = [
        # General
        ("hello,hi,hey,good morning,good evening,greetings",
         "1. Hey! I am the Deployee developer assistant.\n"
         "2. I can help with tracking, heatmaps, dashboard, cameras, uploads, reports, auth, payments, and deployment.\n"
         "3. Ask me anything specific about the code!"),

        ("what is,about,project,introduce",
         "1. Deployee (RetailVision) is an AI-powered retail analytics platform.\n"
         "2. It tracks customers in real-time using YOLO + ByteTrack with unique IDs per person.\n"
         "3. It generates floor traffic heatmaps that show high-traffic areas.\n"
         "4. It detects queues, estimates wait times, and provides an analytics dashboard with CSV/PDF reports."),

        ("help,what can you,options,what do you know",
         "1. Tracking - YOLO detection, ByteTrack IDs, detection settings.\n"
         "2. Heatmaps - Floor traffic visualization.\n"
         "3. Dashboard - Live analytics, alerts, charts.\n"
         "4. Cameras - Adding and managing sources.\n"
         "5. Uploads - Video file management.\n"
         "6. Reports - CSV and PDF export.\n"
         "7. Auth and payments - Roles, Razorpay subscription.\n"
         "8. Deployment - Running in dev or production."),

        # Tracking
        ("track,yolo,detection,tracking,how to track,video feed,live track",
         "1. Call `generate_frames(video_source)` with your webcam index (0, 1, 2...), a file path, or an RTSP URL.\n"
         "2. The function opens the source via `open_video_capture()`, runs `model.track()` per frame to detect people, assigns unique IDs via ByteTrack, draws boxes and trajectories, then yields each frame as JPEG bytes.\n"
         "3. The Flask route `video_feed()` returns an MJPEG stream of those frames, so open `/video_feed` or click Live Track in the dashboard to view it."),

        # Heatmap
        ("heatmap,heat,heat map,how to heatmap",
         "1. Call `generate_heatmap(video_source)` with your camera or video file.\n"
         "2. Each frame detects foot positions (0.15 from bottom of box), stamps intensity onto a numpy grid, decays it by 0.985 per frame, and applies GaussianBlur.\n"
         "3. The result is colored with `COLORMAP_JET` and overlaid on the video via `addWeighted`, then streamed to the dashboard."),

        # Queue
        ("queue,wait,line,que,queue detection",
         "1. The system defines a zone at the bottom 35% of the frame by default.\n"
         "2. `count_in_zone()` tallies how many people are in that queue area each frame.\n"
         "3. Wait time is calculated as queue_length multiplied by `SERVICE_TIME_SEC` (default 6 seconds).\n"
         "4. An alert fires when the queue reaches `QUEUE_THRESHOLD` (default 4 people)."),

        # Alerts
        ("alert,notification,warning,alerts",
         "1. Queue alerts trigger when the line exceeds the threshold.\n"
         "2. Crowd alerts trigger when too many people are detected in frame.\n"
         "3. Restricted alerts trigger when someone enters a restricted zone.\n"
         "4. Alerts have severity (low, medium, high), can be acknowledged, and have a 10-second cooldown."),

        # Upload
        ("upload,video,file,upload video",
         "1. Send a file to `POST /upload_video` (admin only).\n"
         "2. The video is stored in `upload.sqlite` as a BLOB with a unique source key (`db_<uuid>`).\n"
         "3. Use that source key with `generate_frames()` or `generate_heatmap()` to process it.\n"
         "4. Old uploads are automatically deleted after 7 days."),

        # Camera
        ("camera,cameras,source,webcam,rtsp,camera source",
         "1. For webcam, pass an index like 0, 1, or 2.\n"
         "2. For uploaded videos, use the source key `db_<id>` returned after upload.\n"
         "3. For RTSP streams, pass the full RTSP URL.\n"
         "4. For local files, pass the file path directly."),

        # Settings
        ("setting,settings,config,threshold,configuration",
         "1. Detection settings: confidence=0.3, iou=0.5, imgSize=640.\n"
         "2. Heatmap settings: decay=0.985, intensity=25, opacity=0.55, blur=15.\n"
         "3. Queue settings: enabled, threshold=4, serviceTimeSec=6.0.\n"
         "4. Crowd threshold: 12. Alert cooldown: 10 seconds.\n"
         "5. Send a POST request to `/update_settings` with the values you want to change."),

        # Reports
        ("report,export,csv,pdf,reports",
         "1. Send a GET request to `/export_report?range=daily&format=csv`.\n"
         "2. Choose CSV for time-series + alerts, or PDF for chart + summary + alerts.\n"
         "3. Set range to `daily` or `weekly` as needed.\n"
         "4. Optionally filter by camera source with the `source` parameter."),

        # Dashboard
        ("analytics,dashboard,chart,stats,analytics dashboard",
         "1. Call `GET /get_stats` to see active detections, unique customers, queue length, crowd level, and FPS.\n"
         "2. Charts are auto-generated by `get_analytics_data()` in `model.py` and cached briefly.\n"
         "3. Peak hours are calculated from the `analytics_history` table.\n"
         "4. Dwell time is estimated from detection patterns over time."),

        # Auth
        ("auth,login,register,password,sign,authentication",
         "1. Viewer accounts are free with read-only access to the dashboard.\n"
         "2. Admin accounts require a $21 Razorpay subscription for full access.\n"
         "3. Registration hashes passwords with bcrypt and uses Flask sessions.\n"
         "4. All actions are logged in `audit_logs` with IP address."),

        # Deploy
        ("deploy,production,run,start,launch,how to run",
         "1. Install dependencies with `pip install -r requirements.txt`.\n"
         "2. For development, run `python app.py` (starts on port 5000).\n"
         "3. For Windows production, use `waitress-serve --host=0.0.0.0 --port=5000 wsgi:app`.\n"
         "4. For Linux production, use `gunicorn -w 2 -b 0.0.0.0:5000 wsgi:app`."),

        # Payment
        ("razorpay,payment,subscribe,subscription,payment flow",
         "1. User registers with the admin role selected.\n"
         "2. Redirected to Razorpay checkout for the $21 subscription.\n"
         "3. Payment is verified via HMAC signature on the webhook.\n"
         "4. Role is upgraded from `admin_pending` to `admin`."),

        # Database
        ("database,databases,db,sqlite,storage,database structure",
         "1. `store_tracker.sqlite` stores users, cameras, analytics, alerts, audit_logs, subscriptions, and alert_thresholds.\n"
         "2. `upload.sqlite` stores video BLOBs with source keys.\n"
         "3. Both databases are auto-created on first run."),

        # Errors
        ("error,bug,fix,issue,problem,not working,bug fix",
         "1. Video not opening - check source key (webcam=0, file=path, db_<id>).\n"
         "2. No detections - lower confidence threshold (default 0.3) via settings panel.\n"
         "3. Model not found - place .pt weights in project dir, or set MODEL_WEIGHTS in .env.\n"
         "4. Blank feed - ensure ultralytics is updated, tracker (bytetrack.yaml) is built-in.\n"
         "5. Multiple feeds clash - call POST /stop before switching sources."),

        # Model
        ("model,yolo model,weights,model file",
         "1. Call `load_model(model_name)` to load weights like yolo11s.pt or yolov8n.pt.\n"
         "2. Models are cached in `MODEL_CACHE` for fast switching between sessions.\n"
         "3. Default model is set via `MODEL_WEIGHTS` env variable in .env file.\n"
         "4. Switch models from the dashboard settings panel (admin only)."),

        # Stop
        ("stop,halt,end,quit,stop processing",
         "1. Call `POST /stop` to set the stop flag.\n"
         "2. The function `stop_processing()` releases the camera safely.\n"
         "3. Wait a moment before starting a new feed to avoid conflicts."),

        # Confidence
        ("confidence,confidence threshold,conf",
         "1. Default value is 0.3 (30% confidence required).\n"
         "2. Lower values detect more objects but may increase false positives.\n"
         "3. Higher values reduce false positives but may miss detections.\n"
         "4. Update via `POST /update_settings` with `confidence` field (admin only)."),

        # IOU
        ("iou,iou threshold,overlap",
         "1. Default value is 0.5 (50% overlap allowed).\n"
         "2. Lower values merge overlapping boxes more aggressively.\n"
         "3. Higher values keep more overlapping detections separate.\n"
         "4. Update via `POST /update_settings` with `iou` field (admin only)."),

        # FPS
        ("fps,frame rate,target fps",
         "1. Default is 30 FPS.\n"
         "2. Lower values reduce CPU usage but may look less smooth.\n"
         "3. Higher values require more processing power.\n"
         "4. Update via `POST /update_settings` with `targetFps` field (admin only)."),

        # Zone
        ("zone,restricted zone,restriction",
         "1. Define a zone at the top of the frame using `RESTRICTED_ZONE_TOP_OFFSET`.\n"
         "2. Enable with `RESTRICTED_ENABLED` flag in model.py.\n"
         "3. Alerts trigger when any person enters the restricted area.\n"
         "4. Configure zone size and position via settings."),

        # Crowd
        ("crowd,crowd detection,crowd level,crowd threshold",
         "1. The system counts active detections each frame.\n"
         "2. When the count exceeds `CROWD_THRESHOLD` (default 12), an alert fires.\n"
         "3. The crowd level is displayed on the dashboard in real-time.\n"
         "4. Adjust the threshold via `POST /update_settings` with `crowdThreshold`."),

        # Trail
        ("trail,trajectory,trail length",
         "1. Each tracked person's centroid positions are stored in `track_history`.\n"
         "2. The `TRAIL_LENGTH` setting (default 30) controls how many points are kept.\n"
         "3. Trails are drawn with `draw_trajectory()` using color-coded lines.\n"
         "4. Adjust trail length via `POST /update_settings` with `trailLength`."),

        # Audit
        ("audit,logs,audit log,activity log",
         "1. Every action (login, upload, settings change) is logged in `audit_logs`.\n"
         "2. Logs include user ID, email, action type, detail, and IP address.\n"
         "3. View logs via `GET /api/audit_logs` (admin only).\n"
         "4. Logs help with security monitoring and debugging."),

        # API endpoints
        ("api,endpoints,routes,api endpoints",
         "1. `GET /video_feed` - Streams live tracking or heatmap as MJPEG.\n"
         "2. `POST /update_settings` - Updates detection and heatmap settings (admin).\n"
         "3. `GET /get_stats` - Returns live analytics data.\n"
         "4. `POST /upload_video` - Uploads a video file (admin)."),

        # Environment variables
        ("env,environment,env variables,.env",
         "1. `MODEL_WEIGHTS` - YOLO model file name (default: yolo11s.pt).\n"
         "2. `STORE_TRACKER_SECRET` - Flask secret key for sessions.\n"
         "3. `FLASK_DEBUG` - Set to 0 for production.\n"
         "4. `OPENROUTER_API_KEY` - API key for the chatbot LLM."),

        # Functions model.py
        ("functions model.py,model functions,model.py functions",
         "1. `generate_frames(video_source)` - Main tracking generator.\n"
         "2. `generate_heatmap(video_source)` - Heatmap generator.\n"
         "3. `load_model(model_name)` - Loads and caches YOLO weights.\n"
         "4. `apply_settings(payload)` - Updates global settings from a dict.\n"
         "5. `stop_processing()` - Stops camera processing and releases resources."),

        # Functions app.py
        ("functions app.py,app functions,app.py functions",
         "1. `video_feed()` - Streams MJPEG video to the dashboard.\n"
         "2. `switch_mode(mode)` - Switches between tracking and heatmap.\n"
         "3. `upload_video()` - Handles video file uploads.\n"
         "4. `get_stats()` - Returns live analytics stats.\n"
         "5. `handle_chat_message()` - Processes chatbot messages."),

        # Analysis state
        ("analysis,state,analysis_state,Analysis class",
         "1. `current_analysis` is the global instance of the `Analysis` class.\n"
         "2. It stores `track_history`, `all_track_ids`, `frame_count`, and `total_detections`.\n"
         "3. It also stores `queue_length`, `crowd_level`, `heatmap_accumulator`, and `alerts`.\n"
         "4. Call `current_analysis.reset()` to clear all state between sessions."),

        # How tracking works
        ("how tracking works,tracking process,tracking workflow",
         "1. `generate_frames()` opens the video source and reads frames in a loop.\n"
         "2. Each frame is processed by `model.track()` which runs YOLO detection + ByteTrack.\n"
         "3. Detected people get unique IDs, bounding boxes, and centroid positions are stored.\n"
         "4. Boxes, IDs, and trajectories are drawn on the frame, which is yielded as JPEG."),

        # How heatmap works
        ("how heatmap works,heatmap process,heatmap workflow",
         "1. `generate_heatmap()` opens the video source and reads frames in a loop.\n"
         "2. Each frame is processed by `model.track()` to detect people.\n"
         "3. Foot positions are extracted (0.15 from bottom of box) and stamped onto a numpy grid.\n"
         "4. The grid decays by 0.985 per frame, gets blurred, colored with `COLORMAP_JET`, and overlaid on the video."),

        # Frontend
        ("frontend,ui,interface,dashboard ui",
         "1. The dashboard is built with HTML, CSS, and vanilla JavaScript.\n"
         "2. It uses a dark theme with glassmorphism design.\n"
         "3. The chatbot widget floats in the bottom-right corner.\n"
         "4. All API calls are made with `fetch()` and responses are rendered dynamically."),

        # Tech stack
        ("tech stack,technologies,stack,technology",
         "1. Backend: Python, Flask, SQLite, OpenCV, Ultralytics YOLO.\n"
         "2. Frontend: HTML, CSS, vanilla JavaScript.\n"
         "3. AI: YOLO object detection, ByteTrack tracking.\n"
         "4. Payments: Razorpay integration for admin subscriptions."),

        # Features
        ("features,what features,feature list",
         "1. Real-time customer tracking with unique IDs.\n"
         "2. Floor traffic heatmap generation.\n"
         "3. Queue detection with wait time estimation.\n"
         "4. Crowd and restricted zone alerts.\n"
         "5. Camera management (webcam, uploaded videos, RTSP).\n"
         "6. Analytics dashboard with charts, peak hours, dwell time.\n"
         "7. CSV and PDF report export.\n"
         "8. User auth with roles (viewer/admin), Razorpay subscription."),

        # Tables
        ("tables,database tables,sqlite tables",
         "1. `users` - id, name, email, role, password_hash, created_at.\n"
         "2. `camera_sources` - id, name, source, default_mode, enabled.\n"
         "3. `analytics_history` - id, captured_at, source_key, mode, frame_count, total_detections, unique_customers.\n"
         "4. `alerts` - id, source_key, alert_type, alert_value, threshold_value, message, severity.\n"
         "5. `audit_logs` - id, created_at, user_id, user_email, action, detail, ip_address."),

        # Roles
        ("roles,viewer,admin,admin_pending",
         "1. Viewer - free account with read-only access to the dashboard.\n"
         "2. Admin - requires a $21 Razorpay subscription for full access.\n"
         "3. Admin_pending - awaiting payment verification after registration.\n"
         "4. Role is stored in the `users` table and checked on every request."),

        # Upload formats
        ("formats,supported formats,video formats",
         "1. Supported video formats: .mp4, .avi, .mov, .mkv, .wmv, .flv.\n"
         "2. Videos are stored as BLOBs in `upload.sqlite`.\n"
         "3. Each upload gets a unique source key in the format `db_<uuid>`.\n"
         "4. Old uploads are automatically deleted after 7 days."),

        # Camera management
        ("manage cameras,camera management,add camera",
         "1. Add a camera via `POST /api/cameras` with name, source, and default_mode.\n"
         "2. List cameras via `GET /api/cameras`.\n"
         "3. Update a camera via `PATCH /api/cameras/<id>`.\n"
         "4. Delete a camera via `DELETE /api/cameras/<id>`."),

        # Switch mode
        ("switch mode,mode switch,tracking heatmap",
         "1. Call `POST /switch_mode/tracking` to switch to tracking mode.\n"
         "2. Call `POST /switch_mode/heatmap` to switch to heatmap mode.\n"
         "3. The function `stop_processing()` is called first to release the current feed.\n"
         "4. The next `video_feed` request will use the new mode."),

        # Heatmap settings
        ("heatmap settings,heatmap config,heatmap parameters",
         "1. `HEATMAP_DECAY` (default 0.985) - how fast the heatmap fades.\n"
         "2. `HEATMAP_INTENSITY` (default 25) - brightness of each stamp.\n"
         "3. `HEATMAP_OPACITY` (default 0.55) - transparency of the overlay.\n"
         "4. `HEATMAP_BLUR` (default 15) - GaussianBlur kernel size."),

        # Queue settings
        ("queue settings,queue config,queue parameters",
         "1. `QUEUE_ENABLED` (default True) - enables queue detection.\n"
         "2. `QUEUE_THRESHOLD` (default 4) - people count to trigger alert.\n"
         "3. `SERVICE_TIME_SEC` (default 6.0) - estimated seconds per person.\n"
         "4. `QUEUE_ZONE_WIDTH` (default 0.45) - width of the queue zone as fraction of frame."),

        # Crowd settings
        ("crowd settings,crowd config,crowd parameters",
         "1. `CROWD_THRESHOLD` (default 12) - people count to trigger crowd alert.\n"
         "2. The threshold can be updated via `POST /update_settings`.\n"
         "3. Crowd level is displayed on the dashboard in real-time.\n"
         "4. Alerts are sent when the threshold is exceeded."),

        # Alert cooldown
        ("alert cooldown,cooldown,alert frequency",
         "1. `ALERT_COOLDOWN_SEC` (default 10) - minimum seconds between repeated alerts.\n"
         "2. Prevents spam alerts from being sent continuously.\n"
         "3. Each alert type (queue, crowd, restricted) has its own cooldown timer.\n"
         "4. Update via `POST /update_settings` with `alertCooldownSec` field."),

        # Image size
        ("img size,image size,imgSize",
         "1. `IMG_SIZE` (default 640) - input size for YOLO model.\n"
         "2. Smaller values (320) are faster but less accurate.\n"
         "3. Larger values (1280) are slower but more accurate.\n"
         "4. Update via `POST /update_settings` with `imgSize` field."),

        # Tracker
        ("tracker,bytetrack,tracking algorithm",
         "1. `TRACKER_TYPE` (default 'bytetrack.yaml') - ByteTrack algorithm config.\n"
         "2. ByteTrack assigns persistent IDs to detected people across frames.\n"
         "3. The tracker config file is bundled with Ultralytics.\n"
         "4. The `persist=True` flag maintains tracker state between frames."),

        # CLAHE
        ("clahe,enhancement,frame enhancement",
         "1. `ENABLE_FRAME_ENHANCEMENT` (default False) - enables CLAHE preprocessing.\n"
         "2. CLAHE (Contrast Limited Adaptive Histogram Equalization) improves low-light detection.\n"
         "3. Applied via `prepare_frame()` before running YOLO.\n"
         "4. Enable by setting `ENABLE_FRAME_ENHANCEMENT=1` in .env."),

        # Model device
        ("device,gpu,cuda,cpu",
         "1. `MODEL_DEVICE` is auto-detected (cuda if available, else cpu).\n"
         "2. Set `MODEL_DEVICE` in .env to force a specific device.\n"
         "3. GPU acceleration significantly improves FPS.\n"
         "4. `MODEL_HALF` (default True) enables FP16 on CUDA for faster inference."),

        # Webcam
        ("webcam,camera index,usb camera",
         "1. For webcam, pass an integer index like 0, 1, or 2.\n"
         "2. The function `open_video_capture()` tries DirectShow first, then MSMF.\n"
         "3. Set `TARGET_FPS` (default 30) to control frame rate.\n"
         "4. Use `POST /stop` before switching to a different webcam."),

        # RTSP
        ("rtsp,stream,ip camera",
         "1. For RTSP streams, pass the full URL like rtsp://user:pass@ip:port/stream.\n"
         "2. The stream is opened via `open_video_capture()` which accepts any OpenCV source.\n"
         "3. RTSP streams may have latency depending on network conditions.\n"
         "4. Use `POST /stop` before switching to a different stream."),

        # File path
        ("file path,video file,local file",
         "1. For local video files, pass the full file path.\n"
         "2. The file is opened via `open_video_capture()` which uses OpenCV.\n"
         "3. Supported formats: .mp4, .avi, .mov, .mkv, .wmv, .flv.\n"
         "4. The video loops automatically when it reaches the end."),

        # Stats endpoint
        ("stats endpoint,get stats,analytics endpoint",
         "1. `GET /get_stats` returns live analytics data.\n"
         "2. Response includes frameCount, totalDetections, uniqueCustomers, duration, fps.\n"
         "3. Also includes queueLength, queueWaitSeconds, crowdLevel.\n"
         "4. The `image` field contains a base64-encoded chart image."),

        # History
        ("history,analytics history,history endpoint",
         "1. `GET /api/analytics_history` returns historical analytics data.\n"
         "2. Filter by camera source with `?source=<source_key>`.\n"
         "3. Limit results with `?limit=100` (default 100, max 1000).\n"
         "4. Data includes captured_at, frame_count, total_detections, unique_customers."),

        # Peak hours
        ("peak hours,peak traffic,busy hours",
         "1. `GET /api/analytics/peak_hours` returns peak hours analysis.\n"
         "2. Calculate from `analytics_history` table over the last N days.\n"
         "3. Response includes hour, avg_customers, max_customers, sample_count.\n"
         "4. Use `?days=7` to change the analysis period."),

        # Dwell time
        ("dwell time,average time,stay duration",
         "1. `GET /api/analytics/dwell_time` returns dwell time estimates.\n"
         "2. Estimated from the ratio of active detections to unique customers.\n"
         "3. Response includes avg_dwell_minutes, peak_active, avg_customers.\n"
         "4. Use `?days=7` to change the analysis period."),

        # Alerts endpoint
        ("alerts endpoint,alert history,get alerts",
         "1. `GET /api/alerts` returns recent alerts.\n"
         "2. Filter by camera source with `?source=<source_key>`.\n"
         "3. Paginate with `?page=1&per_page=50`.\n"
         "4. Acknowledge an alert via `POST /api/alerts/<id>/acknowledge`."),

        # Alert thresholds
        ("alert thresholds,threshold config,threshold settings",
         "1. `GET /api/alert_thresholds?source=<key>` returns current thresholds.\n"
         "2. `POST /api/alert_thresholds` updates thresholds for a camera.\n"
         "3. Fields: max_customers, min_customers, max_queue_length, enabled.\n"
         "4. Each camera can have its own threshold configuration."),

        # Webhook
        ("webhook,razorpay webhook,payment webhook",
         "1. `POST /webhook/razorpay` handles Razorpay subscription events.\n"
         "2. Verifies HMAC signature using `RAZORPAY_WEBHOOK_SECRET`.\n"
         "3. Handles subscription.activated, subscription.cancelled, and invoice.paid events.\n"
         "4. Updates user role and subscription status in the database."),

        # Requirements
        ("requirements,dependencies,pip install",
         "1. Install all dependencies with `pip install -r requirements.txt`.\n"
         "2. Key packages: flask, opencv-python, ultralytics, torch, bcrypt, requests.\n"
         "3. Optional: waitress (Windows production), gunicorn (Linux production).\n"
         "4. Matplotlib is used for generating chart images in reports."),

        # Debug mode
        ("debug,debug mode,flask debug",
         "1. Set `FLASK_DEBUG=1` in .env to enable debug mode.\n"
         "2. Debug mode shows detailed error messages and auto-reloads on code changes.\n"
         "3. Never use debug mode in production (security risk).\n"
         "4. Set `FLASK_DEBUG=0` for production deployment."),

        # Port
        ("port,flask port,server port",
         "1. Default port is 5000.\n"
         "2. Change via `PORT` or `FLASK_PORT` environment variable in .env.\n"
         "3. Change `FLASK_HOST` to bind to a specific interface (default 0.0.0.0).\n"
         "4. Access the app at http://localhost:5000 in your browser."),

        # Security
        ("security,secure,protection",
         "1. Passwords are hashed with bcrypt before storing.\n"
         "2. Sessions are managed by Flask with a secret key.\n"
         "3. All actions are logged in `audit_logs` with IP address.\n"
         "4. Admin endpoints require role-based authentication."),

        # Scaling
        ("scale,scaling,performance,optimization",
         "1. Use GPU (CUDA) for 10x faster YOLO inference.\n"
         "2. Reduce `IMG_SIZE` to 320 for faster processing.\n"
         "3. Lower `TARGET_FPS` to reduce CPU usage.\n"
         "4. Use `MODEL_HALF=True` for FP16 inference on CUDA."),

        # Troubleshooting
        ("troubleshoot,not working,failed,crash",
         "1. Check the console for error messages.\n"
         "2. Ensure all dependencies are installed with `pip install -r requirements.txt`.\n"
         "3. Verify model weights exist in the project directory.\n"
         "4. Check that the camera is not being used by another application."),

        # Chatbot
        ("chatbot,chat,assistant,bot",
         "1. The chatbot is powered by OpenRouter API with a free model.\n"
         "2. It first checks the local database for pre-written responses.\n"
         "3. If no match is found, it calls the LLM API.\n"
         "4. Responses are formatted with numbered steps and backtick-wrapped code."),

        # Switch source
        ("switch source,change camera,change source",
         "1. Call `POST /stop` to stop the current feed.\n"
         "2. Wait a moment for the camera to release.\n"
         "3. Open `/video_feed?source=<new_source>` with the new source.\n"
         "4. The new feed will start with fresh analytics state."),

        ("webhook,slack,discord,notification,alert notification",
         "1. Add a webhook via `POST /api/webhooks` with the URL and alert types.\n"
         "2. Supported platforms: Slack, Discord, or any service with a webhook URL.\n"
         "3. Alert types to subscribe: queue, crowd, restricted.\n"
         "4. Manage webhooks via `GET /api/webhooks` and `DELETE /api/webhooks/<id>`."),

        ("email,report email,scheduled report,email report",
         "1. Configure email schedules via the dashboard settings panel (admin only).\n"
         "2. Reports are sent automatically based on your schedule (daily or weekly).\n"
         "3. Reports include analytics summary, charts, and alert history.\n"
         "4. Requires SMTP settings configured in the `.env` file."),

        ("websocket,realtime,real-time,live updates",
         "1. The dashboard uses WebSocket for real-time stat updates.\n"
         "2. Stats are pushed to the browser instantly when new data arrives.\n"
         "3. No more polling every 2 seconds - lower latency and bandwidth.\n"
         "4. The WebSocket connection auto-reconnects if it drops."),

        ("comparison,compare cameras,split view",
         "1. Open `/video_feed?source=<cam1>&source2=<cam2>` for side-by-side view.\n"
         "2. Each camera stream is displayed in a 50/50 grid layout.\n"
         "3. Stats for both cameras are shown in the comparison panel.\n"
         "4. Useful for comparing different store sections or time periods."),

        ("push notification,mobile notification,firebase",
         "1. Push notifications use Firebase Cloud Messaging (FCM).\n"
         "2. Register your device via the dashboard settings panel.\n"
         "3. Alerts are sent as push notifications even when the app is closed.\n"
         "4. Requires Firebase project configuration in the `.env` file."),
    ]

    with conn:
        conn.executemany(
            "INSERT INTO chatbot_responses (keywords, response) VALUES (?, ?)",
            responses
        )
    conn.close()


# Fix 4: Cache responses in memory at startup
_RESPONSE_CACHE = []
_KEYWORD_INDEX = {}

def _load_responses_to_cache():
    global _RESPONSE_CACHE, _KEYWORD_INDEX
    conn = _get_db_connection()
    rows = conn.execute("SELECT keywords, response FROM chatbot_responses").fetchall()
    conn.close()
    _RESPONSE_CACHE = [(row["keywords"], row["response"]) for row in rows]
    _KEYWORD_INDEX = {}
    for keywords_str, response in _RESPONSE_CACHE:
        for kw in keywords_str.split(","):
            kw = kw.strip().lower()
            if kw not in _KEYWORD_INDEX:
                _KEYWORD_INDEX[kw] = response

def _query_db_response(message: str) -> str | None:
    text = message.strip().lower()
    if not text:
        return None

    # Check cache first - O(1) lookup
    for kw, response in _KEYWORD_INDEX.items():
        if kw in text:
            return response

    return None


def _call_llm(user_message: str, history: list[dict]) -> str | None:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return None
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://deployee.local",
        "X-Title": "Deployee Assistant",
    }
    ROLE_MAP = {"bot": "assistant", "user": "user"}
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in history[-10:]:
        role = ROLE_MAP.get(m.get("role"), m.get("role", "user"))
        messages.append({"role": role, "content": m["text"]})
    messages.append({"role": "user", "content": user_message})
    body = {
        "model": OPENROUTER_MODEL,
        "messages": messages,
        "temperature": 0.5,
        "max_tokens": 1024,
    }
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=30)
        if resp.status_code != 200:
            print(f"OpenRouter API error: {resp.status_code} {resp.text[:300]}")
            return None
        data = resp.json()
        choices = data.get("choices", [])
        if choices:
            content = choices[0].get("message", {}).get("content", "")
            if content:
                return content
    except Exception as exc:
        print(f"OpenRouter call failed: {exc}")
    return None


class SimpleChatbot:
    def __init__(self):
        self.sessions = {}
        init_chatbot_db()
        seed_chatbot_db()
        _load_responses_to_cache()

    def handle_message(self, session_id, message):
        now = time.time()
        sess = self.sessions.setdefault(session_id, {"history": [], "last": now})
        sess["history"].append({"role": "user", "text": message, "ts": now})
        sess["last"] = now

        reply = _query_db_response(message)

        if not reply:
            reply = _call_llm(message, sess["history"])

        if not reply:
            reply = "1. I can help with tracking, heatmaps, cameras, uploads, settings, alerts, analytics, reports, auth, payments, and deployment.\n2. What specifically do you need?"

        sess["history"].append({"role": "assistant", "text": reply, "ts": time.time()})
        if len(sess["history"]) > 50:
            sess["history"] = sess["history"][-50:]
        return reply


bot = SimpleChatbot()


def handle_chat_message(request):
    data = request.get_json(force=True, silent=True) or {}
    session_id = data.get("sessionId") or request.cookies.get("session") or "anon"
    message = data.get("message", "")
    reply = bot.handle_message(session_id, message)
    return jsonify({"reply": reply})