#!/usr/bin/env python3
import time, json, os, fcntl, sys
import board, digitalio
from PIL import Image, ImageDraw, ImageFont
from adafruit_rgb_display import st7789

STATE_FILE = "/tmp/zoomie_state.json"
LOCKFILE_PATH = "/tmp/zoomie_display.lock"

# lock so only 1 runs
def acquire_single_instance_lock():
    fd = open(LOCKFILE_PATH, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(str(os.getpid()))
        fd.flush()
        return fd
    except OSError:
        print("display running, exit")
        sys.exit(0)

_lock = acquire_single_instance_lock()

# screen pins
cs_pin    = digitalio.DigitalInOut(board.CE1)
dc_pin    = digitalio.DigitalInOut(board.D25)
reset_pin = digitalio.DigitalInOut(board.D26)
backlight = digitalio.DigitalInOut(board.D24)
backlight.direction = digitalio.Direction.OUTPUT
backlight.value = True
spi = board.SPI()

HEIGHT, WIDTH = 320, 240

display = st7789.ST7789(
    spi,
    rotation=90,
    cs=cs_pin,
    dc=dc_pin,
    rst=reset_pin,
    baudrate=32000000,
    width=WIDTH,
    height=HEIGHT,
)

if display.rotation % 180 == 90:
    width, height = HEIGHT, WIDTH
else:
    width, height = WIDTH, HEIGHT

# fonts
try:
    big_font    = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 80)
    medium_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
    small_font  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
except:
    big_font = medium_font = small_font = ImageFont.load_default()

# load state
def load_state():
    try:
        with open(STATE_FILE,"r") as f:
            d = json.load(f)
    except:
        return {
            "running":False,
            "waiting":False,
            "next_player":None,
            "next_player_name":None,
            "score1":0,
            "score2":0,
            "player1_name":"PLAYER 1",
            "player2_name":"PLAYER 2",
            "winner":None,
            "currentPlayer":1,
            "currentRound":1,
            "totalRounds":1,
            "round_summary":None,
            "stateVersion":0,
            "force_hold":False,
        }
    return {
        "running":bool(d.get("running",False)),
        "waiting":bool(d.get("waiting",False)),
        "next_player":d.get("next_player"),
        "next_player_name":d.get("next_player_name"),
        "score1":int(d.get("score1",0)),
        "score2":int(d.get("score2",0)),
        "player1_name":d.get("player1_name","PLAYER 1"),
        "player2_name":d.get("player2_name","PLAYER 2"),
        "winner":d.get("winner"),
        "currentPlayer":int(d.get("currentPlayer",1)),
        "currentRound":int(d.get("currentRound",1)),
        "totalRounds":int(d.get("totalRounds",1)),
        "round_summary":d.get("round_summary"),
        "stateVersion":int(d.get("stateVersion",0)),
        "force_hold":bool(d.get("force_hold",False)),
    }

# text helper
def draw_centered(draw,text,y,font,c):
    box = draw.textbbox((0,0),text,font=font)
    w = box[2]-box[0]
    draw.text(((width-w)//2, y),text,font=font,fill=c)

heartbeat = 0

# screens
def draw_welcome_screen():
    global heartbeat
    img = Image.new("RGB",(width,height),(0,0,0))
    dr = ImageDraw.Draw(img)
    draw_centered(dr,"WELCOME!",40,medium_font,(0,180,255))
    draw_centered(dr,"Tap your card",120,small_font,(255,255,255))
    draw_centered(dr,"to begin",150,small_font,(255,255,255))
    draw_centered(dr,"ZOOMIE BOX",height-60,small_font,(0,255,100))
    heartbeat = (heartbeat+1)%2
    dr.rectangle((5,5,15,15), fill=(255,0,0) if heartbeat==0 else (0,0,255))
    display.image(img)

def draw_game_screen(st):
    global heartbeat
    img = Image.new("RGB",(width,height),(0,0,0))
    dr = ImageDraw.Draw(img)
    cp = st["currentPlayer"]
    sc = st["score1"] if cp==1 else st["score2"]
    nm = st["player1_name"] if cp==1 else st["player2_name"]
    draw_centered(dr,nm,10,medium_font,(0,180,255))
    draw_centered(dr,f"ROUND {st['currentRound']} / {st['totalRounds']}",55,small_font,(200,200,200))
    s = str(sc)
    box = dr.textbbox((0,0),s,font=big_font)
    hh = box[3]-box[1]
    draw_centered(dr,s,(height-hh)//2,big_font,(0,255,0))
    draw_centered(dr,"PLAYING",height-40,small_font,(0,255,0))
    heartbeat=(heartbeat+1)%2
    dr.rectangle((5,5,15,15), fill=(255,0,0) if heartbeat==0 else (0,0,255))
    display.image(img)

def draw_winner_screen(name):
    img = Image.new("RGB",(width,height),(0,0,0))
    dr = ImageDraw.Draw(img)
    draw_centered(dr,"WINNER",20,medium_font,(255,215,0))
    maxw = width-20
    sz = 80
    while sz>25:
        try:
            f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",sz)
        except:
            f = small_font
        box = dr.textbbox((0,0),name,font=f)
        if (box[2]-box[0])<=maxw: break
        sz -= 5
    draw_centered(dr,name,110,f,(0,255,0))
    display.image(img)

def draw_waiting_screen(st):
    img = Image.new("RGB",(width,height),(0,0,0))
    dr = ImageDraw.Draw(img)
    nm = st.get("next_player_name","PLAYER")
    r  = st.get("currentRound",1)
    draw_centered(dr,"NEXT UP",30,medium_font,(0,180,255))
    draw_centered(dr,nm,90,medium_font,(255,255,0))
    draw_centered(dr,f"ROUND {r}",150,small_font,(200,200,255))
    draw_centered(dr,"Starting soon...",height-60,small_font,(0,255,100))
    display.image(img)

def draw_round_summary_screen(st):
    img = Image.new("RGB",(width,height),(0,0,0))
    dr = ImageDraw.Draw(img)
    s = st.get("round_summary","")
    if not s:
        return draw_waiting_screen(st)
    y=20
    for ln in s.split("\n"):
        draw_centered(dr,ln,y,small_font,(255,255,0))
        y+=40
    draw_centered(dr,"Next Round Starting...",height-50,small_font,(0,255,100))
    display.image(img)

# loop
def main():
    print("DISPLAY running")
    last_v=-1
    while True:
        try:
            st = load_state()
            v = st["stateVersion"]
            if v!=last_v:
                print("update",v,st)
                last_v=v
            run  = st.get("running",False)
            wait = st.get("waiting",False)
            win  = st.get("winner")
            hold = st.get("force_hold",False)

            if win:
                draw_winner_screen(win)
                time.sleep(0.03)
                continue

            if hold:
                if run: draw_game_screen(st)
                elif wait:
                    if st.get("round_summary"): draw_round_summary_screen(st)
                    else: draw_waiting_screen(st)
                time.sleep(0.03)
                continue

            if wait:
                if st.get("round_summary"): draw_round_summary_screen(st)
                else: draw_waiting_screen(st)
                time.sleep(0.03)
                continue

            if run:
                draw_game_screen(st)
                time.sleep(0.03)
                continue

            draw_welcome_screen()

        except Exception as e:
            print("DISPLAY ERR",e)

        time.sleep(0.03)

if __name__=="__main__":
    main()
