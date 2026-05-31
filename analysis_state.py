from collections import defaultdict, deque
import time

TRAIL_LENGTH = 30
STALE_TRACK_TIMEOUT = 300  # 5 minutes

class Analysis:
    """A class to hold the state of a single analysis session."""
    def __init__(self):
        self.reset()

    def reset(self):
        """Resets the analysis state."""
        self.track_history = defaultdict(lambda: deque(maxlen=TRAIL_LENGTH))
        self.all_track_ids = set()
        self.track_last_seen = {}
        self.frame_count = 0
        self.total_detections = 0
        self.start_time = time.time()
        self.duration = 0
        self.heatmap_accumulator = None
        self.active_detections = 0
        self.last_heatmap_path = None
        self.last_heatmap_overlay_path = None
        self.queue_length = 0
        self.queue_wait_seconds = 0.0
        self.crowd_level = 0
        self.alerts = deque(maxlen=100)
        self.alert_counter = 0
        self.alert_last_sent = {}
        self.metric_history = deque(maxlen=3600)

    def update_duration(self):
        self.duration = time.time() - self.start_time

    def get_stats(self):
        """Returns a dictionary of the current stats."""
        self.update_duration()
        fps = 0.0
        if self.duration > 0:
            fps = self.frame_count / self.duration
        return {
            'frameCount': self.frame_count,
            'totalDetections': self.total_detections,
            'uniqueCustomers': len(self.all_track_ids),
            'duration': round(self.duration, 2),
            'activeDetections': self.active_detections,
            'fps': round(fps, 2),
            'queueLength': self.queue_length,
            'queueWaitSeconds': round(self.queue_wait_seconds, 1),
            'crowdLevel': self.crowd_level
        }

    def record_metrics(self, active, queue_length):
        timestamp = time.time()
        self.metric_history.append({
            'ts': timestamp,
            'active': int(active),
            'queue': int(queue_length)
        })
        # Prune stale tracks every 100 frames
        if self.frame_count % 100 == 0:
            self._prune_stale_tracks(timestamp)

    def _prune_stale_tracks(self, now):
        stale_ids = [tid for tid, last_seen in self.track_last_seen.items()
                     if (now - last_seen) > STALE_TRACK_TIMEOUT]
        for tid in stale_ids:
            self.all_track_ids.discard(tid)
            self.track_history.pop(tid, None)
            self.track_last_seen.pop(tid, None)

    def add_alert(self, alert_type, message, level='warning'):
        self.alert_counter += 1
        payload = {
            'id': self.alert_counter,
            'ts': time.time(),
            'type': alert_type,
            'level': level,
            'message': message
        }
        self.alerts.append(payload)
        return payload

# Create a global instance of the Analysis class
current_analysis = Analysis()
