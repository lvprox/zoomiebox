#!/usr/bin/env python3
import pigpio
import time
import atexit
import threading

# pin + angles
SERVO_PIN = 12
START_ANGLE = 21
GAME_OVER_ANGLE = 170
AUTO_RESET_DELAY = 5

# pigpio init
pi = pigpio.pi()
if not pi.connected:
    raise SystemExit("pigpio not running")

pi.set_servo_pulsewidth(SERVO_PIN, 0)
time.sleep(0.1)

# map angle→pulse
def angle_to_pulse(angle: float):
    angle = max(0, min(180, angle))
    return int(500 + (angle / 180.0) * 2000)

# smooth servo move
def smooth_move(angle_from, angle_to, step=2, delay=0.015):
    if angle_from < angle_to:
        seq = range(angle_from, angle_to + 1, step)
    else:
        seq = range(angle_from, angle_to - 1, -step)
    for a in seq:
        pi.set_servo_pulsewidth(SERVO_PIN, angle_to_pulse(a))
        time.sleep(delay)

# read current angle
def get_current_angle():
    try:
        pw = pi.get_servo_pulsewidth(SERVO_PIN)
    except pigpio.error:
        return START_ANGLE
    if pw < 500 or pw > 2500:
        return START_ANGLE
    ang = int((pw - 500) / 2000 * 180)
    return max(0, min(180, ang))

# lower flag
def flag_reset():
    print("[SERVO] reset")
    cur = get_current_angle()
    smooth_move(cur, START_ANGLE)
    pi.set_servo_pulsewidth(SERVO_PIN, angle_to_pulse(START_ANGLE))
    print(f"[SERVO] at {START_ANGLE}")

# raise flag
def flag_game_over():
    print("[SERVO] game over")
    cur = get_current_angle()
    smooth_move(cur, GAME_OVER_ANGLE)
    threading.Thread(target=_auto_reset_thread, daemon=True).start()

# auto reset thread
def _auto_reset_thread():
    print(f"[SERVO] wait {AUTO_RESET_DELAY}s")
    time.sleep(AUTO_RESET_DELAY)
    print("[SERVO] auto reset")
    flag_reset()

# cleanup
def cleanup():
    print("[SERVO] cleanup")
    pi.set_servo_pulsewidth(SERVO_PIN, 0)
    pi.stop()

atexit.register(cleanup)

# simple test
if __name__ == "__main__":
    print("raising…")
    flag_game_over()
    time.sleep(AUTO_RESET_DELAY + 2)
    print("done")
