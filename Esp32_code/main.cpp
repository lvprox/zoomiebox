#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <WebServer.h>
#include "SparkFun_BNO080_Arduino_Library.h"
#include <HTTPClient.h>

// wifi
const char* WIFI_SSID = "redmi";
const char* WIFI_PASS = "redmi@@@";
const char* PI_SERVER = "http://192.168.121.130:5000";
WebServer server(80);
void notifyPi(const char* endpoint);

// ultrasonic
const int TRIG_PIN = 16;
const int ECHO_FRONT = 34;
const int ECHO_RIGHT = 35;
const int ECHO_BACK = 36;
const int ECHO_LEFT = 39;

float OBSTACLE_LIMIT_CM = 40.0;

void initUltrasonic() {
  pinMode(TRIG_PIN, OUTPUT);
  digitalWrite(TRIG_PIN, LOW);
  pinMode(ECHO_FRONT, INPUT);
  pinMode(ECHO_RIGHT, INPUT);
  pinMode(ECHO_BACK, INPUT);
  pinMode(ECHO_LEFT, INPUT);
}

float readDistanceCm(int echoPin) {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(3);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  long d = pulseIn(echoPin, HIGH, 20000);
  if (d == 0) return -1;
  return (d * 0.0343f) / 2.0f;
}

bool isObstacleFront() { float d = readDistanceCm(ECHO_FRONT); return (d>0 && d<OBSTACLE_LIMIT_CM); }
bool isObstacleRight() { float d = readDistanceCm(ECHO_RIGHT); return (d>0 && d<OBSTACLE_LIMIT_CM); }
bool isObstacleBack()  { float d = readDistanceCm(ECHO_BACK);  return (d>0 && d<OBSTACLE_LIMIT_CM); }
bool isObstacleLeft()  { float d = readDistanceCm(ECHO_LEFT);  return (d>0 && d<OBSTACLE_LIMIT_CM); }

// imu
BNO080 imu;
const int SDA_PIN = 21;
const int SCL_PIN = 22;

float yawOffset = 0.0f;
float lastYawRaw = 0.0f;
float yawDegCorrected = 0.0f;

// motors
struct Chan { int in1, in2, enPin, enCh; };
Chan FL{13,14,27,0}, FR{25,23,26,1}, RL{4,5,33,2}, RR{18,19,32,3};

const float Lx=0.12f, Ly=0.12f;
const float K = (Lx+Ly);

const int POL_FL=1, POL_FR=-1, POL_RL=1, POL_RR=-1;

const float LEFT_GAIN=1.0f;
const float RIGHT_GAIN=1.0f;

const int PWM_LOW = 190;
const int PWM_MAX = 255;
int basePWM = PWM_LOW;

bool yawCorrectionEnabled = true;
float Kp_yaw = 0.03f;

// modes
enum GameMode { MODE_SINGLE=0, MODE_TWO_PLAYER=1 };
enum Difficulty { DIFF_LOW=0, DIFF_MED=1, DIFF_HIGH=2, DIFF_MANUAL=3 };

GameMode currentMode = MODE_SINGLE;
Difficulty currentDifficulty = DIFF_LOW;

// grid
int gridCols=2, gridRows=2;
unsigned long tileMoveMs=800;
unsigned long tilePauseMs=300;

int tileX=0, tileY=0;
int targetTileX=0, targetTileY=0;
int dirX=0, dirY=0;

bool inPause=false;

// match
bool matchRunning=false;
bool gameRunning=false;
unsigned long gameStartMs=0;

const unsigned long GAME_DURATION_MS=30000;
const unsigned long BETWEEN_SEGMENTS_MS=5000;

int totalRounds=1;
int currentRound=0;
int currentPlayerIndex=0;

bool nextSegmentScheduled=false;
unsigned long nextSegmentStartMs=0;

// motion cmds
float vx=0, vy=0, wz=0;

float strafeLeftScale=0.95f;
float strafeRightScale=1.00f;

unsigned long segEndMs=0;

// helpers
inline void pwmAttach(const Chan& c) {
#if defined(ESP_ARDUINO_VERSION) && (ESP_ARDUINO_VERSION >= ESP_ARDUINO_VERSION_VAL(3,0,0))
  ledcAttachChannel(c.enPin, 20000, 8, c.enCh);
#else
  ledcSetup(c.enCh, 20000, 8);
  ledcAttachPin(c.enPin, c.enCh);
#endif
}

inline void pwmWrite(const Chan& c, int v) {
  int duty = constrain(v,0,255);
#if defined(ESP_ARDUINO_VERSION) && (ESP_ARDUINO_VERSION >= ESP_ARDUINO_VERSION_VAL(3,0,0))
  ledcWrite(c.enPin, duty);
#else
  ledcWrite(c.enCh, duty);
#endif
}

void drive(const Chan& c, int pwm, int dir) {
  digitalWrite(c.in1, dir>0);
  digitalWrite(c.in2, dir<0);
  pwmWrite(c, dir==0 ? 0 : pwm);
}

void stopAll() { drive(FL,0,0); drive(FR,0,0); drive(RL,0,0); drive(RR,0,0); }

// mecanum
void applyMecanum() {
  float fl = vy - vx - K*wz;
  float fr = vy + vx + K*wz;
  float rl = vy + vx - K*wz;
  float rr = vy - vx + K*wz;

  float m = max(max(fabs(fl),fabs(fr)), max(fabs(rl),fabs(rr)));
  if (m < 1e-3) { stopAll(); return; }
  if (m > 1.0) { fl/=m; fr/=m; rl/=m; rr/=m; }

  fl*=LEFT_GAIN; rl*=LEFT_GAIN; fr*=RIGHT_GAIN; rr*=RIGHT_GAIN;

  auto cmd=[&](const Chan& c,float v){
    int dir=(v>0)?1:(v<0)?-1:0;
    int pwm=int(fabs(v)*basePWM);
    drive(c,pwm,dir);
  };

  cmd(FL, fl*POL_FL);
  cmd(FR, fr*POL_FR);
  cmd(RL, rl*POL_RL);
  cmd(RR, rr*POL_RR);
}

// imu update
float normalizeAngleRad(float a){
  while(a>PI) a-=2*PI;
  while(a<-PI)a+=2*PI;
  return a;
}
void resetYaw(){ yawOffset=lastYawRaw; Serial.println("yaw reset"); }
void updateIMU(){
  if(imu.dataAvailable()){
    float r=imu.getYaw();
    lastYawRaw=r;
    float c=normalizeAngleRad(r-yawOffset);
    yawDegCorrected=c*180.0f/PI;
    if(isnan(yawDegCorrected)) yawDegCorrected=0;
  }
}

// tile logic
void clampGrid(){ if(gridCols<2)gridCols=2; if(gridRows<2)gridRows=2; }

void chooseRandomDirLow(){
  int dirs[4][2]={{1,0},{-1,0},{0,1},{0,-1}};
  int cand[4][2],n=0;
  for(int i=0;i<4;i++){
    int nx=tileX+dirs[i][0], ny=tileY+dirs[i][1];
    if(nx>=0&&nx<gridCols&&ny>=0&&ny<gridRows){
      cand[n][0]=dirs[i][0]; cand[n][1]=dirs[i][1]; n++;
    }
  }
  if(n==0){ dirX=dirY=0; return; }
  int idx=random(n);
  dirX=cand[idx][0]; dirY=cand[idx][1];
}

void planNextTileLow(){
  int dirs[4][2]={{1,0},{-1,0},{0,1},{0,-1}};
  int cand[4][2],n=0;
  for(int i=0;i<4;i++){
    int dx=dirs[i][0], dy=dirs[i][1];
    int nx=tileX+dx, ny=tileY+dy;
    if(nx<0||nx>=gridCols||ny<0||ny>=gridRows) continue;
    bool blocked=false;
    if(dx==1 && isObstacleRight()) blocked=true;
    if(dx==-1&& isObstacleLeft()) blocked=true;
    if(dy==1 && isObstacleFront()) blocked=true;
    if(dy==-1&& isObstacleBack()) blocked=true;
    if(!blocked){ cand[n][0]=dx; cand[n][1]=dy; n++; }
  }
  if(n==0){
    Serial.println("no safe tile");
    dirX=dirY=0;
    targetTileX=tileX; targetTileY=tileY;
    return;
  }
  int idx=random(n);
  dirX=cand[idx][0];
  dirY=cand[idx][1];
  targetTileX=tileX+dirX;
  targetTileY=tileY+dirY;
}

void startMoveSegment(unsigned long now){
  if(dirX==1 && isObstacleRight()) return;
  if(dirX==-1&& isObstacleLeft()) return;
  if(dirY==1 && isObstacleFront()) return;
  if(dirY==-1&& isObstacleBack()) return;

  int sx=targetTileX-tileX;
  int sy=targetTileY-tileY;

  if(sx>0){ vx=0; vy=1; }
  else if(sx<0){ vx=0; vy=-1; }
  else if(sy>0){ vy=0; vx=1; }
  else if(sy<0){ vy=0; vx=-1; }
  else { vx=vy=0; }

  if(fabs(vx)<0.01 && fabs(vy)>0.01){
    if(vy>0) vy=1.0f*strafeRightScale;
    else vy=-1.0f*strafeLeftScale;
  }

  wz=0;
  basePWM=PWM_LOW;
  inPause=false;
  segEndMs=now+tileMoveMs;

  Serial.printf("Move → (%d,%d)\n",targetTileX,targetTileY);
}

void resetTiles(unsigned long now){
  clampGrid();
  tileX=0; tileY=0;
  targetTileX=0; targetTileY=0;
  inPause=false;
  planNextTileLow();
  startMoveSegment(now);
}

// mode parsing
GameMode parseMode(const String& s){
  String ls=s; ls.toLowerCase();
  if(ls=="two"||ls=="two_player"||ls=="2"||ls=="vs"||ls=="versus")
    return MODE_TWO_PLAYER;
  return MODE_SINGLE;
}

Difficulty parseDifficulty(const String& s){
  String ls=s; ls.toLowerCase();
  if(ls=="medium"||ls=="med") return DIFF_MED;
  if(ls=="high"||ls=="hard") return DIFF_HIGH;
  if(ls=="manual") return DIFF_MANUAL;
  return DIFF_LOW;
}

const char* modeToString(GameMode m){
  switch(m){
    case MODE_SINGLE:return "single";
    case MODE_TWO_PLAYER:return "two_player";
  }
  return "unknown";
}

const char* diffToString(Difficulty d){
  switch(d){
    case DIFF_LOW:return "low";
    case DIFF_MED:return "medium";
    case DIFF_HIGH:return "high";
    case DIFF_MANUAL:return "manual";
  }
  return "unknown";
}

// segment logic
void startNewSegment(unsigned long now){
  if(!matchRunning) return;
  gameRunning=true;
  gameStartMs=now;
  yawOffset=lastYawRaw;
  resetTiles(now);
  notifyPi("/api/esp/game_start");
  Serial.printf("=== START P%d R%d\n",currentPlayerIndex+1,currentRound);
}

void endCurrentSegment(unsigned long now){
  if(!gameRunning) return;

  gameRunning=false;
  vx=vy=wz=0;
  stopAll();
  Serial.printf("=== END P%d R%d\n",currentPlayerIndex+1,currentRound);
  nextSegmentScheduled=false;

  if(currentMode==MODE_SINGLE){
    if(currentRound<totalRounds){
      int nr=currentRound+1;
      int np=1;
      notifyPi(("/api/esp/segment_end?next_player="+String(np)+
               "&next_round="+String(nr)).c_str());
      currentRound=nr;
      nextSegmentScheduled=true;
      nextSegmentStartMs=now+BETWEEN_SEGMENTS_MS;
    } else {
      matchRunning=false;
      notifyPi("/api/esp/game_end");
    }
    return;
  }

  if(currentPlayerIndex==0){
    int np=2; int nr=currentRound;
    notifyPi(("/api/esp/segment_end?next_player="+String(np)+
             "&next_round="+String(nr)).c_str());
    currentPlayerIndex=1;
    nextSegmentScheduled=true;
    nextSegmentStartMs=now+BETWEEN_SEGMENTS_MS;
    return;
  }

  if(currentRound<totalRounds){
    int np=1; int nr=currentRound+1;
    notifyPi(("/api/esp/segment_end?next_player="+String(np)+
             "&next_round="+String(nr)).c_str());
    currentRound=nr;
    currentPlayerIndex=0;
    nextSegmentScheduled=true;
    nextSegmentStartMs=now+BETWEEN_SEGMENTS_MS;
  } else {
    matchRunning=false;
    currentRound=1;
    currentPlayerIndex=0;
    notifyPi("/api/esp/game_end");
  }
}

// http handlers
void handle_start(){
  matchRunning=false;
  gameRunning=false;
  nextSegmentScheduled=false;
  vx=vy=wz=0;
  currentRound=1;
  currentPlayerIndex=0;

  if(server.hasArg("mode")) currentMode=parseMode(server.arg("mode"));
  else currentMode=MODE_SINGLE;

  if(server.hasArg("difficulty")) currentDifficulty=parseDifficulty(server.arg("difficulty"));
  else currentDifficulty=DIFF_LOW;

  if(server.hasArg("rounds")){
    int r=server.arg("rounds").toInt();
    if(r<1) r=1;
    if(r>20) r=20;
    totalRounds=r;
  } else totalRounds=1;

  matchRunning=true;
  nextSegmentScheduled=true;

  unsigned long now=millis();
  nextSegmentStartMs=now+1500;

  server.send(200,"text/plain","Game starting...");
}

void handle_stop(){
  matchRunning=false;
  gameRunning=false;
  nextSegmentScheduled=false;
  vx=vy=wz=0;
  stopAll();
  notifyPi("/api/esp/game_end");
  server.send(200,"text/plain","Game stopped");
}

void handle_resetyaw_http(){
  resetYaw();
  server.send(200,"text/plain","Yaw reset");
}

void handle_setgrid(){
  if(server.hasArg("cols")) gridCols=server.arg("cols").toInt();
  if(server.hasArg("rows")) gridRows=server.arg("rows").toInt();
  clampGrid();
  String msg="Grid set ";
  msg+=gridCols; msg+=" x "; msg+=gridRows;
  server.send(200,"text/plain",msg);
}

void handle_setcalib(){
  if(server.hasArg("tileMs")){
    unsigned long v=server.arg("tileMs").toInt();
    if(v>=200 && v<=3000) tileMoveMs=v;
  }
  if(server.hasArg("pauseMs")){
    unsigned long v=server.arg("pauseMs").toInt();
    if(v>=0 && v<=3000) tilePauseMs=v;
  }
  String msg="Calib: ";
  msg+=tileMoveMs; msg+=" "; msg+=tilePauseMs;
  server.send(200,"text/plain",msg);
}

void handle_settrim(){
  if(server.hasArg("left")) strafeLeftScale=server.arg("left").toFloat();
  if(server.hasArg("right")) strafeRightScale=server.arg("right").toFloat();
  String msg="Trim ";
  msg+=strafeLeftScale; msg+=" "; msg+=strafeRightScale;
  server.send(200,"text/plain",msg);
}

void handle_status(){
  String j="{";
  j+="\"gameRunning\":";j+=(gameRunning?"true":"false");j+=",";
  j+="\"matchRunning\":";j+=(matchRunning?"true":"false");j+=",";
  j+="\"mode\":\"";j+=modeToString(currentMode);j+="\",";
  j+="\"difficulty\":\"";j+=diffToString(currentDifficulty);j+="\",";
  j+="\"currentRound\":";j+=currentRound;j+=",";
  j+="\"totalRounds\":";j+=totalRounds;j+=",";
  j+="\"currentPlayer\":";j+=currentPlayerIndex+1;j+=",";
  j+="\"tileX\":";j+=tileX;j+=",";
  j+="\"tileY\":";j+=tileY;j+=",";
  j+="\"gridCols\":";j+=gridCols;j+=",";
  j+="\"gridRows\":";j+=gridRows;j+=",";
  j+="\"inPause\":";j+=(inPause?"true":"false");j+=",";
  j+="\"yaw\":";j+=yawDegCorrected;j+=",";
  j+="\"vx\":";j+=vx;j+=",";
  j+="\"vy\":";j+=vy;j+=",";
  j+="\"wz\":";j+=wz;j+=",";
  j+="\"tileMoveMs\":";j+=tileMoveMs;j+=",";
  j+="\"tilePauseMs\":";j+=tilePauseMs;j+=",";
  j+="\"strafeLeft\":";j+=strafeLeftScale;j+=",";
  j+="\"strafeRight\":";j+=strafeRightScale;
  j+="}";
  server.send(200,"application/json",j);
}

void registerWithPi(){
  if(WiFi.status()!=WL_CONNECTED){
    Serial.println("no wifi");
    return;
  }
  HTTPClient http;
  String url=String(PI_SERVER)+"/api/register_esp?ip="+WiFi.localIP().toString();
  Serial.println("reg "+url);
  if(!http.begin(url)){ Serial.println("fail"); return; }
  int code=http.GET();
  Serial.print("code "); Serial.println(code);
  http.end();
}

void notifyPi(const char* endpoint){
  if(WiFi.status()!=WL_CONNECTED){
    Serial.println("np: no wifi");
    return;
  }
  String url=String(PI_SERVER)+endpoint;
  Serial.println("np "+url);
  HTTPClient http;
  if(http.begin(url)){
    int code=http.GET();
    Serial.print("np code "); Serial.println(code);
    http.end();
  }
  delay(5);
}

// setup
void setup(){
  Serial.begin(115200);
  delay(500);

  for(auto c:{FL,FR,RL,RR}){
    pinMode(c.in1,OUTPUT);
    pinMode(c.in2,OUTPUT);
    pwmAttach(c);
  }
  stopAll();

  Wire.begin(SDA_PIN,SCL_PIN);
  Wire.setClock(400000);

  Serial.println("IMU...");
  if(!imu.begin()){
    Serial.println("no IMU");
    while(1) delay(1000);
  }
  imu.enableRotationVector(100);
  Serial.println("IMU ok");
  Serial.println("HTTP /resetyaw");

  delay(200);
  if(imu.dataAvailable()){
    lastYawRaw=imu.getYaw();
    yawOffset=lastYawRaw;
  }

  randomSeed(esp_random());

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID,WIFI_PASS);
  Serial.print("WiFi");
  while(WiFi.status()!=WL_CONNECTED){
    delay(300);
    Serial.print(".");
  }
  Serial.print("\nIP ");
  Serial.println(WiFi.localIP());

  registerWithPi();
  initUltrasonic();
  Serial.println("ultra OK");

  server.on("/start",handle_start);
  server.on("/stop",handle_stop);
  server.on("/resetyaw",handle_resetyaw_http);
  server.on("/status",handle_status);
  server.on("/setgrid",handle_setgrid);
  server.on("/setcalib",handle_setcalib);
  server.on("/settrim",handle_settrim);

  server.on("/",[](){
    String s="ESP32 Bin Game\n";
    s+="use /start /stop /status /setgrid /setcalib /settrim /resetyaw\n";
    server.send(200,"text/plain",s);
  });

  server.begin();
}

// loop
void loop(){
  server.handleClient();
  unsigned long now=millis();

  if(Serial.available()){
    char c=Serial.read();
    if(c=='r'||c=='R') resetYaw();
  }

  updateIMU();

  if(matchRunning){
    if(gameRunning){
      if(now-gameStartMs>=GAME_DURATION_MS){
        endCurrentSegment(now);
      } else {
        if(currentDifficulty==DIFF_LOW||
           currentDifficulty==DIFF_MED||
           currentDifficulty==DIFF_HIGH){
          if(now>=segEndMs){
            if(!inPause){
              tileX=targetTileX;
              tileY=targetTileY;
              vx=vy=wz=0;
              inPause=true;
              segEndMs=now+tilePauseMs;
            } else {
              inPause=false;
              planNextTileLow();
              startMoveSegment(now);
            }
          }
        } else if(currentDifficulty==DIFF_MANUAL){
          vx=vy=wz=0;
        }

        basePWM=PWM_LOW;
        bool moving=(fabs(vx)>0.01||fabs(vy)>0.01);

        if(yawCorrectionEnabled && moving){
          float err=-yawDegCorrected;
          float w=Kp_yaw*err;
          if(w>0.5f)w=0.5f;
          if(w<-0.5f)w=-0.5f;
          wz=w;
        } else wz=0;

        applyMecanum();
      }
    } else {
      vx=vy=wz=0;
      stopAll();
      if(nextSegmentScheduled && now>=nextSegmentStartMs){
        nextSegmentScheduled=false;
        startNewSegment(now);
      }
    }
  } else {
    gameRunning=false;
    vx=vy=wz=0;
    stopAll();
  }

  static unsigned long lastPrint=0;
  if(now-lastPrint>600){
    lastPrint=now;
    Serial.print("Match="); Serial.print(matchRunning?"ON ":"OFF ");
    Serial.print(" seg="); Serial.print(gameRunning?"PLAY ":"WAIT ");
    Serial.print(" P"); Serial.print(currentPlayerIndex+1);
    Serial.print(" R"); Serial.print(currentRound); Serial.print("/");
    Serial.print(totalRounds);
    Serial.print(" mode="); Serial.print(modeToString(currentMode));
    Serial.print(" diff="); Serial.print(diffToString(currentDifficulty));
    Serial.print(" Tile("); Serial.print(tileX); Serial.print(",");
    Serial.print(tileY); Serial.print(") Grid ");
    Serial.print(gridCols); Serial.print("x"); Serial.print(gridRows);
    Serial.print(" yaw="); Serial.print(yawDegCorrected,1);
    Serial.print(" vx="); Serial.print(vx,2);
    Serial.print(" vy="); Serial.print(vy,2);
    Serial.print(" wz="); Serial.print(wz,3);
    Serial.print(" tileMs="); Serial.print(tileMoveMs);
    Serial.print(" pauseMs="); Serial.print(tilePauseMs);
    Serial.print(" trimL="); Serial.print(strafeLeftScale,3);
    Serial.print(" trimR="); Serial.println(strafeRightScale,3);
  }

  delay(2);
}
