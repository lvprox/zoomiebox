#!/usr/bin/env python3
import RPi.GPIO as GPIO
import time, json, requests, os, fcntl, sys

# single instance
LOCK = "/tmp/laser_sensors.lock"
def lock_instance():
    fd = open(LOCK, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(str(os.getpid()))
        fd.flush()
        return fd
    except:
        print("laser_sensors.py already running")
        sys.exit(0)

_lock = lock_instance()

# send highlight timestamp + trigger sound daemon
def notify_score_event():
    try:
        requests.post(
            "http://127.0.0.1:5000/api/camera/shot",
            json={"ts": time.time()},
            timeout=0.25
        )
        print("→ highlight timestamp sent")
    except Exception as e:
        print("Failed /api/camera/shot:", e)

# config
SENSOR_PINS = [5, 6, 23, 27]
SAMPLE_INTERVAL = 0.01
DEBOUNCE_SAMPLES = 1
COOLDOWN_SEC = 0.5
CLEAR_RESET_SEC = 0.2
STATE_FILE = "/tmp/zoomie_state.json"

GPIO.setmode(GPIO.BCM)
for p in SENSOR_PINS:
    GPIO.setup(p, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# state vars
zone_state = 0
zone_counter = 0
last_score_time = 0
ball_active = False
clear_start = 0

# helpers
def read_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {"running": False, "score": 0, "stateVersion": 0}

def write_state(**kv):
    st = read_state()
    st["stateVersion"] = st.get("stateVersion", 0) + 1
    st.update(kv)
    with open(STATE_FILE, "w") as f:
        json.dump(st, f)
        f.flush()
        os.fsync(f.fileno())

print("Laser scoring running...")

try:
    while True:
        any_broken = 1 if any(GPIO.input(p) == 1 for p in SENSOR_PINS) else 0

        # debounce
        if any_broken != zone_state:
            zone_counter += 1
            if zone_counter >= DEBOUNCE_SAMPLES:
                zone_state = any_broken
                zone_counter = 0

                # ball enters
                if zone_state == 1:
                    now = time.monotonic()

                    if not ball_active:
                        ball_active = True
                        st = read_state()
                        running = st.get("running", False)
                        score = int(st.get("score", 0))

                        if running and (now - last_score_time) > COOLDOWN_SEC:
                            new_score = score + 1
                            last_score_time = now
                            write_state(score=new_score)
                            print("SCORE →", new_score)

                            with open("/tmp/sound_event.json", "w") as f:
                                json.dump({"event": "score"}, f)

                            notify_score_event()

                        else:
                            print("Ignored beam (cooldown/not running)")

                    else:
                        print("Repeated beam ignored")

                # ball leaves
                else:
                    clear_start = time.monotonic()
                    print("Beams restored")

        else:
            zone_counter = 0

        # ready for next ball
        if zone_state == 0 and ball_active:
            if (time.monotonic() - clear_start) >= CLEAR_RESET_SEC:
                ball_active = False

        time.sleep(SAMPLE_INTERVAL)

except KeyboardInterrupt:
    print("Stopping laser scoring")

finally:
    GPIO.cleanup()
    print("GPIO cleaned up")
