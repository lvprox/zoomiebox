#!/usr/bin/env python3
import os
import time
import subprocess
import json
import requests
import sqlite3
from flask import Flask, request, jsonify, render_template, send_from_directory
from servo_flag import flag_game_over, flag_reset
from camera_recorder import CameraRecorder

# start recorder
camera_recorder = CameraRecorder()
try:
    camera_recorder.stop_full_record()
except:
    pass

DB_PATH = "/home/pi/zoomieBox/zoomie.db"
STATE_FILE = "/tmp/zoomie_state.json"
HIGHLIGHTS_DIR = "/home/pi/zoomieBox/highlights"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static")
)

# db connect
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# init db tables
def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rfid_uid TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player1_id INTEGER NOT NULL,
            player2_id INTEGER NOT NULL,
            start_ts REAL NOT NULL,
            end_ts REAL,
            difficulty TEXT,
            final_winner INTEGER,
            video_path TEXT,
            FOREIGN KEY(player1_id) REFERENCES players(id),
            FOREIGN KEY(player2_id) REFERENCES players(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER NOT NULL,
            round_number INTEGER NOT NULL,
            p1_score INTEGER NOT NULL,
            p2_score INTEGER NOT NULL,
            winner INTEGER,
            FOREIGN KEY(game_id) REFERENCES games(id)
        )
    """)

    conn.commit()
    conn.close()

init_db()

LASER_PROC = None
DISPLAY_PROC = None
RFID_PROC = None

# start sound
def start_sound_daemon():
    subprocess.call(["pkill", "-9", "-f", "sound_daemon.py"])
    log = open("/tmp/zoomie_sound.log", "w")
    p = subprocess.Popen(
        ["/home/pi/zoomieBox/.venv/bin/python",
         "/home/pi/zoomieBox/hardware/sound_daemon.py"],
        stdout=log,
        stderr=subprocess.STDOUT
    )
    return p

# start laser
def start_laser_process():
    global LASER_PROC
    subprocess.call(["pkill", "-9", "-f", "laser_sensors.py"])
    LASER_PROC = subprocess.Popen(
        ["/home/pi/zoomieBox/.venv/bin/python",
         "/home/pi/zoomieBox/hardware/laser_sensors.py"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

# start display
def start_display_process():
    global DISPLAY_PROC
    subprocess.call(["pkill", "-9", "-f", "display.py"])
    log = open("/tmp/zoomie_display.log", "w")
    DISPLAY_PROC = subprocess.Popen(
        ["/home/pi/zoomieBox/.venv/bin/python",
         "/home/pi/zoomieBox/hardware/display.py"],
        stdout=log,
        stderr=subprocess.STDOUT
    )

def stop_display_process():
    global DISPLAY_PROC
    if DISPLAY_PROC and DISPLAY_PROC.poll() is None:
        DISPLAY_PROC.terminate()
        try:
            DISPLAY_PROC.wait(timeout=2)
        except:
            DISPLAY_PROC.kill()
    DISPLAY_PROC = None

# start rfid
def start_rfid_process():
    global RFID_PROC
    subprocess.call(["pkill", "-9", "-f", "rfid_reader.py"])
    log = open("/tmp/zoomie_rfid.log", "w")
    RFID_PROC = subprocess.Popen(
        ["/home/pi/zoomieBox/.venv/bin/python",
         "/home/pi/zoomieBox/hardware/rfid_reader.py"],
        stdout=log,
        stderr=subprocess.STDOUT
    )

def stop_rfid_process():
    global RFID_PROC
    if RFID_PROC and RFID_PROC.poll() is None:
        RFID_PROC.terminate()
        try:
            RFID_PROC.wait(timeout=2)
        except:
            RFID_PROC.kill()
    subprocess.call(["pkill", "-9", "-f", "rfid_reader.py"])
    RFID_PROC = None

# read state
def read_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {"running": False, "score": 0}

# write state
def write_state(**kwargs):
    st = read_state()
    if "winner" in kwargs:
        st["winner"] = kwargs["winner"]
    if "force_hold" in kwargs:
        st["force_hold"] = kwargs["force_hold"]

    for k, v in kwargs.items():
        if k not in ("winner", "force_hold"):
            st[k] = v

    st["stateVersion"] = int(st.get("stateVersion", 0)) + 1

    with open(STATE_FILE, "w") as f:
        json.dump(st, f)
        f.flush()
        os.fsync(f.fileno())

# quick sound event
def play_sound(event):
    try:
        with open("/tmp/sound_event.json", "w") as f:
            json.dump({"event": event}, f)
        os.sync()
    except:
        pass

ESP32_BASE_URL = None
try:
    with open("/tmp/zoomie_esp_ip.txt") as f:
        ESP32_BASE_URL = f.read().strip()
except:
    pass

@app.route("/")
def index():
    return render_template("index.html")

def esp_get(path, params=None):
    if not ESP32_BASE_URL:
        return {"ok": False, "error": "ESP not registered"}
    try:
        r = requests.get(ESP32_BASE_URL + path, params=params, timeout=1.2)
        r.raise_for_status()
        if "json" in r.headers.get("Content-Type", ""):
            return {"ok": True, "json": r.json()}
        return {"ok": True, "text": r.text}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.route("/api/state")
def api_state():
    return jsonify(read_state())

@app.route("/api/register_esp")
def register_esp():
    global ESP32_BASE_URL
    ip = request.args.get("ip")
    if not ip:
        return "Missing IP", 400
    if not ip.startswith("http://"):
        ip = "http://" + ip
    ESP32_BASE_URL = ip
    with open("/tmp/zoomie_esp_ip.txt", "w") as f:
        f.write(ip)
    return "OK"

score_timestamps = {}

@app.route("/api/camera/shot", methods=["POST"])
def api_camera_shot():
    data = request.get_json(force=True) or {}
    ts = float(data.get("ts", time.time()))

    st = read_state()
    game_id = st.get("current_game_id")
    if not game_id:
        return jsonify({"ok": False, "error": "no game"}), 400

    score_timestamps.setdefault(game_id, []).append(ts)

    cur_player = int(st.get("currentPlayer", 1))
    cur_round = int(st.get("currentRound", 1))
    play_sound("score")

    roundScores = st.get("roundScores", {})
    if str(cur_round) not in roundScores:
        roundScores[str(cur_round)] = {"p1": 0, "p2": 0}

    if cur_player == 1:
        roundScores[str(cur_round)]["p1"] += 1
        write_state(score1=roundScores[str(cur_round)]["p1"])
    else:
        roundScores[str[cur_round]]["p2"] += 1
        write_state(score2=roundScores[str[cur_round]]["p2"])

    write_state(roundScores=roundScores)

    return jsonify({"ok": True})

@app.route("/api/esp/start", methods=["POST"])
def api_start():
    data = request.get_json(force=True) or {}
    mode = data.get("mode", "two")
    diff = data.get("difficulty", "low")
    rounds = int(data.get("rounds", 1))

    st = read_state()
    p1_name = st.get("player1_name", "Player 1")
    p2_name = st.get("player2_name", "Player 2")

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT id FROM players WHERE name = ?", (p1_name,))
    row = cur.fetchone()
    p1_id = row["id"] if row else 0

    cur.execute("SELECT id FROM players WHERE name = ?", (p2_name,))
    row = cur.fetchone()
    p2_id = row["id"] if row else 0

    cur.execute("""
        INSERT INTO games (player1_id, player2_id, start_ts, difficulty)
        VALUES (?, ?, ?, ?)
    """, (p1_id, p2_id, time.time(), diff))

    game_id = cur.lastrowid
    conn.commit()
    conn.close()

    score_timestamps[game_id] = []

    write_state(
        running=True,
        waiting=False,
        score1=0,
        score2=0,
        roundScores={},
        player1_name=p1_name,
        player2_name=p2_name,
        player1_id=p1_id,
        player2_id=p2_id,
        currentPlayer=1,
        currentRound=1,
        totalRounds=rounds,
        difficulty=diff,
        current_game_id=game_id,
        winner=None,
        round_summary=None
    )

    time.sleep(0.05)
    camera_recorder.start_full_record(game_id)
    play_sound("start")

    esp_get("/start", params={"mode": mode, "difficulty": diff, "rounds": rounds})

    return jsonify({"ok": True, "game_id": game_id})

@app.route("/api/esp/stop", methods=["POST"])
def api_stop():
    esp_get("/stop")
    write_state(running=False)
    return jsonify({"ok": True})

@app.route("/api/esp/game_start", methods=["GET"])
def api_esp_game_start():
    st = read_state()
    current_player = st.get("currentPlayer", 1)
    current_round = st.get("currentRound", 1)

    is_first_segment = (current_player == 1 and current_round == 1)

    updates = {
        "running": True,
        "waiting": False,
        "currentPlayer": current_player,
        "currentRound": current_round,
        "round_summary": None
    }

    if is_first_segment:
        flag_reset()

    write_state(**updates)
    return jsonify({"ok": True})

@app.route("/api/esp/segment_end", methods=["GET"])
def api_esp_segment_end():
    next_player = request.args.get("next_player", type=int)
    next_round = request.args.get("next_round", type=int)

    st = read_state()
    cur_player = st.get("currentPlayer", 1)
    cur_round = st.get("currentRound", 1)
    p1_name = st.get("player1_name", "Player 1")
    p2_name = st.get("player2_name", "Player 2")
    game_id = st.get("current_game_id")

    # p1 done → wait p2
    if cur_player == 1:
        write_state(
            running=False,
            waiting=True,
            currentPlayer=2,
            currentRound=cur_round,
            next_player=2,
            next_player_name=p2_name,
            round_summary=None
        )
        return jsonify({"ok": True, "waiting_for_player_2": True,
                        "next_player": 2, "next_round": cur_round})

    # p2 done → calc winner
    score1 = int(st.get("score1", 0))
    score2 = int(st.get("score2", 0))

    if score1 > score2:
        round_winner = 1
        winner_name = p1_name
    elif score2 > score1:
        round_winner = 2
        winner_name = p2_name
    else:
        round_winner = 0
        winner_name = "Tie"

    txt = (f"Round {cur_round} Result:\n"
           f"{p1_name} {score1} – {score2} {p2_name}\n"
           f"Winner: {winner_name}")

    conn = get_db()
    conn.execute("""
        INSERT INTO rounds (game_id, round_number, p1_score, p2_score, winner)
        VALUES (?, ?, ?, ?, ?)
    """, (game_id, cur_round, score1, score2, round_winner))
    conn.commit()
    conn.close()

    write_state(score1=0, score2=0)
    next_name = p1_name if next_player == 1 else p2_name

    write_state(
        running=False,
        waiting=True,
        currentPlayer=next_player,
        currentRound=next_round,
        next_player=next_player,
        next_player_name=next_name,
        round_summary=txt
    )

    return jsonify({"ok": True, "round_summary": txt,
                    "next_player": next_player, "next_round": next_round})

@app.route("/api/esp/game_end", methods=["GET"])
def api_esp_game_end():
    write_state(running=False)
    play_sound("stop")
    flag_game_over()

    st = read_state()
    game_id = st.get("current_game_id")
    if not game_id:
        return jsonify({"ok": False, "error": "missing game_id"}), 400

    try:
        camera_recorder.stop_full_record()
    except:
        pass

    timestamps = score_timestamps.get(game_id, [])
    highlight_path = camera_recorder.merge_highlight(game_id, timestamps)

    conn = get_db()
    cur = conn.cursor()

    cur_round = int(st.get("currentRound", 1))
    score1 = int(st.get("score1", 0))
    score2 = int(st.get("score2", 0))

    cur.execute("SELECT COUNT(*) AS c FROM rounds WHERE game_id = ? AND round_number = ?",
                (game_id, cur_round))
    exists = cur.fetchone()["c"]

    if exists == 0:
        if score1 > score2:
            w = 1
        elif score2 > score1:
            w = 2
        else:
            w = 0
        cur.execute("""
            INSERT INTO rounds (game_id, round_number, p1_score, p2_score, winner)
            VALUES (?, ?, ?, ?, ?)
        """, (game_id, cur_round, score1, score2, w))
        conn.commit()

    cur.execute("SELECT winner FROM rounds WHERE game_id = ?", (game_id,))
    rows = cur.fetchall()

    p1_rounds = sum(1 for r in rows if r["winner"] == 1)
    p2_rounds = sum(1 for r in rows if r["winner"] == 2)

    if p1_rounds > p2_rounds:
        final_winner = 1
        win_name = st.get("player1_name", "Player 1")
    elif p2_rounds > p1_rounds:
        final_winner = 2
        win_name = st.get("player2_name", "Player 2")
    else:
        final_winner = 0
        win_name = "Tie"

    cur.execute("""
        UPDATE games SET end_ts=?, final_winner=?, video_path=?
        WHERE id=?
    """, (time.time(), final_winner, highlight_path, game_id))
    conn.commit()
    conn.close()

    write_state(
        running=False,
        waiting=False,
        winner=win_name,
        next_player=None,
        next_player_name=None,
        currentPlayer=1,
        currentRound=1,
        score1=0,
        score2=0,
        round_summary=None
    )

    return jsonify({
        "ok": True,
        "highlight": highlight_path,
        "winner": win_name,
        "p1_rounds": p1_rounds,
        "p2_rounds": p2_rounds
    })

@app.route("/api/rfid/start", methods=["POST"])
def api_rfid_start():
    start_rfid_process()
    st = read_state()
    st.pop("lastScan", None)
    st.pop("player1_name", None)
    st.pop("player2_name", None)
    write_state(**st)
    return jsonify({"ok": True})

@app.route("/api/rfid/stop", methods=["POST"])
def api_rfid_stop():
    stop_rfid_process()
    return jsonify({"ok": True})

@app.route("/api/rfid_scan")
def api_rfid_scan():
    uid = request.args.get("uid")
    if not uid:
        return jsonify({"error": "uid required"}), 400

    ts = time.time()
    st = read_state()
    play_sound("rfid")

    if st.get("winner") and not st.get("running") and not st.get("waiting"):
        write_state(
            player1_name=None,
            player2_name=None,
            lastScan=None,
            running=False,
            waiting=False,
            winner=None
        )
        st = read_state()

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, rfid_uid, name FROM players WHERE rfid_uid=?", (uid,))
    row = cur.fetchone()
    exists = bool(row)
    player = dict(row) if row else None

    p1 = st.get("player1_name")
    p2 = st.get("player2_name")

    updates = {"lastScan": {"uid": uid, "ts": ts, "exists": exists, "player": player}}

    if exists:
        if p1 is None:
            updates["player1_name"] = player["name"]
        elif p2 is None:
            updates["player2_name"] = player["name"]

    write_state(**updates)
    st = read_state()

    return jsonify({
        "uid": uid,
        "ts": ts,
        "exists": exists,
        "player": player,
        "player1_name": st.get("player1_name"),
        "player2_name": st.get("player2_name")
    })

@app.route("/api/save_players", methods=["POST"])
def api_save_players():
    data = request.get_json(force=True) or {}
    p1 = data.get("player1_name")
    p2 = data.get("player2_name")

    st = read_state()
    if p1:
        st["player1_name"] = p1
    if p2:
        st["player2_name"] = p2

    write_state(
        player1_name=st.get("player1_name"),
        player2_name=st.get("player2_name")
    )
    return jsonify({"ok": True})

@app.route("/api/rfid/last")
def api_rfid_last():
    st = read_state()
    last = st.get("lastScan")
    if not last:
        return ("", 204)
    return jsonify(last)

@app.route("/api/esp/round_end", methods=["GET"])
def api_esp_round_end():
    st = read_state()

    cur_player = int(st.get("currentPlayer", 1))
    cur_round = int(st.get("currentRound", 1))
    total_rounds = int(st.get("totalRounds", 1))

    # p1 done → wait
    if cur_player == 1:
        next_player = 2
        next_round = cur_round
        next_name = st.get("player2_name", "Player 2")

        write_state(
            running=False,
            waiting=True,
            currentPlayer=next_player,
            currentRound=next_round,
            next_player=next_player,
            next_player_name=next_name
        )
        return jsonify({"ok": True, "next_player": next_player,
                        "next_round": next_round, "waiting": True})

    # p2 done → maybe end
    if cur_round >= total_rounds:
        write_state(running=False, waiting=True, round_summary=None)
        return jsonify({"ok": True, "match_complete": True})

    # next round
    next_player = 1
    next_round = cur_round + 1
    next_name = st.get("player1_name", "Player 1")

    write_state(
        running=False,
        waiting=True,
        currentPlayer=next_player,
        currentRound=next_round,
        next_player=next_player,
        next_player_name=next_name
    )

    return jsonify({"ok": True, "next_player": next_player,
                    "next_round": next_round, "waiting": True})

@app.route("/highlights")
def highlights_page():
    if not os.path.exists(HIGHLIGHTS_DIR):
        os.makedirs(HIGHLIGHTS_DIR)

    files = sorted(
        [f for f in os.listdir(HIGHLIGHTS_DIR) if f.endswith(".mp4")],
        reverse=True
    )

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT g.id, g.start_ts, g.end_ts, g.final_winner, g.video_path,
               p1.name AS p1name, p2.name AS p2name
        FROM games g
        LEFT JOIN players p1 ON g.player1_id = p1.id
        LEFT JOIN players p2 ON g.player2_id = p2.id
        ORDER BY g.id DESC
        LIMIT 50
    """)
    games = cur.fetchall()

    history = []
    for g in games:
        cur.execute("""
            SELECT round_number, p1_score, p2_score, winner
            FROM rounds
            WHERE game_id=?
            ORDER BY round_number ASC
        """, (g["id"],))
        rounds = cur.fetchall()

        basename = os.path.basename(g["video_path"]) if g["video_path"] else None

        history.append({
            "id": g["id"],
            "p1": g["p1name"],
            "p2": g["p2name"],
            "winner": g["final_winner"],
            "rounds": rounds,
            "video": basename
        })

    conn.close()
    return render_template("highlights.html", files=files, history=history)

@app.route("/video/<path:filename>")
def serve_video(filename):
    return send_from_directory(HIGHLIGHTS_DIR, filename)

@app.route("/api/players", methods=["POST"])
def api_players_create():
    data = request.get_json(force=True) or {}
    uid = data.get("uid")
    name = data.get("name", "").strip()

    if not uid or not name:
        return jsonify({"ok": False, "error": "Missing uid or name"}), 400

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO players (rfid_uid, name, created_at)
        VALUES (?, ?, ?)
    """, (uid, name, time.time()))
    conn.commit()

    cur.execute("SELECT id, rfid_uid, name FROM players WHERE rfid_uid=?", (uid,))
    player = dict(cur.fetchone())
    conn.close()

    st = read_state()
    if st.get("player1_name") is None:
        write_state(player1_name=name)
    elif st.get("player2_name") is None:
        write_state(player2_name=name)

    return jsonify({"ok": True, "player": player})

if __name__ == "__main__":
    start_laser_process()
    start_display_process()
    start_rfid_process()
    start_sound_daemon()
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
