#!/usr/bin/env python3
import os
import time
import subprocess
import shutil
import signal

# dirs
BASE_DIR = "/home/pi/zoomieBox"
FULL_DIR = os.path.join(BASE_DIR, "full_recordings")
SHOTS_DIR = os.path.join(BASE_DIR, "shots")
HIGHLIGHT_DIR = os.path.join(BASE_DIR, "highlights")

os.makedirs(FULL_DIR, exist_ok=True)
os.makedirs(SHOTS_DIR, exist_ok=True)
os.makedirs(HIGHLIGHT_DIR, exist_ok=True)

class CameraRecorder:
    def __init__(self):
        self.proc = None
        self.record_start_ts = None
        self.current_game_id = None
        self.full_h264 = None
        self.full_mp4 = None

    # start full rec
    def start_full_record(self, game_id: int):
        if self.proc:
            print("[Camera] already running")
            return

        self.current_game_id = game_id
        self.record_start_ts = time.time()

        self.full_h264 = os.path.join(FULL_DIR, f"game_{game_id}.h264")
        self.full_mp4 = os.path.join(FULL_DIR, f"game_{game_id}.mp4")

        print(f"[Camera] start → {self.full_h264}")

        cmd = [
            "rpicam-vid",
            "--width", "1280",
            "--height", "720",
            "--framerate", "30",
            "--codec", "h264",
            "--inline",
            "-t", "0",
            "--rotation", "180",
            "-o", self.full_h264
        ]

        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT
        )

    # stop + convert
    def stop_full_record(self):
        if not self.proc:
            print("[Camera] not running")
            return

        print("[Camera] stopping")

        try:
            self.proc.send_signal(signal.SIGINT)
        except Exception as e:
            print("[Camera] SIGINT fail:", e)

        try:
            self.proc.wait(timeout=3)
        except Exception:
            print("[Camera] force kill")
            try:
                self.proc.kill()
                self.proc.wait()
            except Exception as e:
                print("[Camera] kill err:", e)

        self.proc = None
        print("[Camera] saved:", self.full_h264)

        # wait stable
        for _ in range(50):
            if os.path.exists(self.full_h264) and os.path.getsize(self.full_h264) > 0:
                s1 = os.path.getsize(self.full_h264)
                time.sleep(0.1)
                s2 = os.path.getsize(self.full_h264)
                if s1 == s2:
                    print("[Camera] stable")
                    break
            time.sleep(0.1)
        else:
            print("[Camera] no stable file")
            return

        cmd = [
            "ffmpeg",
            "-y",
            "-i", self.full_h264,
            "-c", "copy",
            self.full_mp4
        ]

        print("[Camera] converting")
        subprocess.call(cmd)

        if not os.path.exists(self.full_mp4) or os.path.getsize(self.full_mp4) < 20000:
            print("[Camera] mp4 fail")
            return

        print("[Camera] mp4 OK:", self.full_mp4)

    # cut highlight
    def merge_highlight(self, game_id: int, timestamps: list):
        if not timestamps:
            print("[Camera] no timestamps")
            return None

        if not self.full_mp4 or not os.path.exists(self.full_mp4):
            print("[Camera] missing full mp4")
            return None

        print(f"[Camera] highlight cut {timestamps}")

        game_shots = os.path.join(SHOTS_DIR, f"game_{game_id}")
        os.makedirs(game_shots, exist_ok=True)

        if self.record_start_ts is None:
            print("[Camera] no start ts")
            return None

        normalized = [(t - self.record_start_ts) for t in timestamps]
        normalized = [max(0, t) for t in normalized]

        merged_segments = self._merge_segments(normalized)
        print("[Camera] segments:", merged_segments)

        clips = []
        idx = 1

        # cut clips
        for seg_start, seg_end in merged_segments:
            out_path = os.path.join(game_shots, f"clip_{idx}.mp4")
            dur = seg_end - seg_start

            print(f"[Camera] clip {idx}: {seg_start:.2f}-{seg_end:.2f}")

            cmd = [
                "ffmpeg", "-y",
                "-ss", str(seg_start),
                "-i", self.full_mp4,
                "-t", str(dur),
                "-c", "copy",
                out_path
            ]

            subprocess.call(cmd)

            if os.path.exists(out_path) and os.path.getsize(out_path) > 10000:
                clips.append(out_path)
            else:
                print("[Camera] bad clip → del")
                try:
                    os.remove(out_path)
                except:
                    pass

            idx += 1

        if not clips:
            print("[Camera] no clips")
            return None

        list_file = os.path.join(game_shots, "list.txt")
        with open(list_file, "w") as f:
            for c in clips:
                f.write(f"file '{c}'\n")

        highlight_path = os.path.join(HIGHLIGHT_DIR, f"game_{game_id}.mp4")

        print(f"[Camera] merging {len(clips)} clips")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            highlight_path
        ]

        subprocess.call(cmd)
        print("[Camera] highlight:", highlight_path)
        return highlight_path

    # merge timestamp windows
    def _merge_segments(self, times):
        WIN = 1.5
        if not times:
            return []

        segs = []
        for t in times:
            segs.append((t - WIN, t + WIN))

        segs.sort(key=lambda x: x[0])

        merged = []
        cs, ce = segs[0]

        for s, e in segs[1:]:
            if s <= ce:
                ce = max(ce, e)
            else:
                merged.append((max(0, cs), ce))
                cs, ce = s, e

        merged.append((max(0, cs), ce))
        return merged
