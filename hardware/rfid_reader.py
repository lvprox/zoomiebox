#!/usr/bin/env python3
import time, requests, RPi.GPIO as GPIO, os, fcntl, sys
from mfrc522 import MFRC522

# single instance
LOCK = "/tmp/zoomie_rfid_reader.lock"
def lock_instance():
    fd = open(LOCK, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(str(os.getpid()))
        fd.flush()
        return fd
    except:
        print("rfid_reader.py already running")
        sys.exit(0)

_lock = lock_instance()

SERVER = "http://127.0.0.1:5000"

def send_uid(uid):
    try:
        r = requests.get(
            f"{SERVER}/api/rfid_scan",
            params={"uid": uid},
            timeout=(0.3, 0.7)
        )
        print("RFID HTTP status:", r.status_code)
        r.close()
    except Exception as e:
        print("Send UID error:", e)

def uid_to_num(u):
    n = 0
    for i in range(5):
        n = n * 256 + u[i]
    return n

def main():
    reader = MFRC522(
        bus=1,
        device=0,
        spd=1_000_000,
        pin_mode=GPIO.BCM,
        pin_rst=22,
    )

    print("RFID service running on SPI1...")

    last_uid = None
    last_ts = 0
    COOLDOWN = 1.0

    try:
        while True:
            status, _ = reader.MFRC522_Request(reader.PICC_REQIDL)

            if status == reader.MI_OK:
                status, uid = reader.MFRC522_Anticoll()
                if status == reader.MI_OK and uid and len(uid) >= 5:

                    now = time.time()
                    uid_str = str(uid_to_num(uid))

                    if uid_str != last_uid or (now - last_ts) > COOLDOWN:
                        last_uid = uid_str
                        last_ts = now

                        print("\n=== CARD ===")
                        print("Raw:", uid, "ID:", uid_str)
                        send_uid(uid_str)

                    time.sleep(0.1)
                else:
                    time.sleep(0.05)
            else:
                time.sleep(0.05)

    finally:
        try:
            reader.Close_MFRC522()
        except:
            GPIO.cleanup()
        print("RFID reader stopped.")

if __name__ == "__main__":
    main()
