#!/usr/bin/env python3
import time
import json
import os
import fcntl
import sys
import subprocess

# ----------------------------------------------
# SINGLE INSTANCE LOCK
# ----------------------------------------------
LOCKFILE_PATH = "/tmp/sound_daemon.lock"

def acquire_single_instance_lock():
    lock_fd = open(LOCKFILE_PATH, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
        return lock_fd
    except:
        print("sound_daemon.py already running")
        sys.exit(0)

_lock_handle = acquire_single_instance_lock()


# ----------------------------------------------
# PATHS
# ----------------------------------------------
SOUND_DIR = "/home/pi/zoomieBox/sounds"
EVENT_FILE = "/tmp/sound_event.json"

# Background music process
bg_proc = None


# ----------------------------------------------
# PLAY SOUND EFFECT (WAV recommended)
# ----------------------------------------------
def play_sfx(name):
    wav = os.path.join(SOUND_DIR, name + ".wav")
    mp3 = os.path.join(SOUND_DIR, name + ".mp3")

    if os.path.exists(wav):
        subprocess.Popen(["aplay", "-q", wav])
    elif os.path.exists(mp3):
        subprocess.Popen(["mpg123", "-q", mp3])
    else:
        print(f"[WARN] Missing sound:", name)


# ----------------------------------------------
# BACKGROUND MUSIC CONTROL (MPG123)
# ----------------------------------------------
def start_background():
    global bg_proc

    stop_background()

    bg_mp3 = os.path.join(SOUND_DIR, "background.mp3")
    bg_wav = os.path.join(SOUND_DIR, "background.wav")

    if os.path.exists(bg_mp3):
        bg_proc = subprocess.Popen(["mpg123", "-q", "--loop", "-1", bg_mp3])
        print("Background music started (mp3).")

    elif os.path.exists(bg_wav):
        bg_proc = subprocess.Popen(["aplay", "-q", bg_wav])
        print("Background music started (wav).")

    else:
        print("[WARN] No background music file found.")


def stop_background():
    global bg_proc
    if bg_proc and bg_proc.poll() is None:
        bg_proc.terminate()
        try: bg_proc.wait(timeout=1)
        except: bg_proc.kill()
    bg_proc = None
    print("Background music stopped.")


# ----------------------------------------------
# MAIN LOOP
# ----------------------------------------------
last_event = None
print("Sound daemon running (NO-LAG MODE)...")

while True:
    if os.path.exists(EVENT_FILE):
        try:
            with open(EVENT_FILE) as f:
                data = json.load(f)
        except:
            data = None

        try:
            os.remove(EVENT_FILE)
        except:
            pass

        if data:
            event = data.get("event")
            print("Sound event:", event)

            # SCORE should always play — never blocked by last_event
            if event == "score":
                play_sfx("score")
                continue

            if event == "rfid":
                play_sfx("rfid")
                continue

            # START and STOP should not repeat constantly
            if event != last_event:
                last_event = event

                if event == "start":
                    play_sfx("round_start")
                    start_background()

                elif event == "stop":
                    stop_background()
                    play_sfx("game_over")


    time.sleep(0.05)
