// state
let player1Uid = null, player2Uid = null;
let player1Name = null, player2Name = null;
let pendingNewUid = null;
let lastRfidTsSeen = 0;

// tiny logger
function log(msg) {
  const box = document.getElementById("log-box");
  const now = new Date().toLocaleTimeString();
  box.textContent = `[${now}] ${msg}\n` + box.textContent;
}

// start/stop rfid
async function startRfidScan() {
  await fetch("/api/rfid/start", { method: "POST" });
  log("RFID on");
}

async function stopRfidScan() {
  await fetch("/api/rfid/stop", { method: "POST" });
  log("RFID off");
}

// clear players
function clearPlayers() {
  player1Uid = player2Uid = null;
  player1Name = player2Name = null;

  document.getElementById("player1-name").textContent = "(no card yet)";
  document.getElementById("player1-uid").textContent = "";
  document.getElementById("player2-name").textContent = "(optional)";
  document.getElementById("player2-uid").textContent = "";

  updateStartButtonState();
  log("players cleared");
}

// popup (new player)
function showNewPlayerPopup(uid) {
  pendingNewUid = uid;
  document.getElementById("new-player-uid").value = uid;
  document.getElementById("new-player-name").value = "";
  document.getElementById("new-player-box").style.display = "block";
  log("new card: " + uid);
}

function cancelNewPlayer() {
  pendingNewUid = null;
  document.getElementById("new-player-box").style.display = "none";
  log("cancelled");
}

async function saveNewPlayer() {
  const name = document.getElementById("new-player-name").value.trim();
  if (!name) {
    log("enter name");
    return;
  }
  const uid = pendingNewUid;

  const res = await fetch("/api/players", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ uid, name })
  });

  const data = await res.json();
  if (!res.ok) {
    log("save failed");
    return;
  }

  log("saved player " + name);
  pendingNewUid = null;
  document.getElementById("new-player-box").style.display = "none";

  handleRfidScan({
    uid: data.player.rfid_uid,
    exists: true,
    player: data.player,
    ts: Date.now() / 1000
  });
}

// handle RFID scan
function handleRfidScan(data) {
  const uid = data.uid;

  if (uid === player1Uid || uid === player2Uid) return;

  if (data.exists) {
    const name = data.player.name;

    if (!player1Uid) {
      player1Uid = uid;
      player1Name = name;
      document.getElementById("player1-name").textContent = name;
      document.getElementById("player1-uid").textContent = uid;
    } else if (!player2Uid) {
      player2Uid = uid;
      player2Name = name;
      document.getElementById("player2-name").textContent = name;
      document.getElementById("player2-uid").textContent = uid;
    }

    updateStartButtonState();
    log("card: " + name);
    return;
  }

  // new card
  showNewPlayerPopup(uid);
}

// update start button display
function updateStartButtonState() {
  const count = (player1Uid ? 1 : 0) + (player2Uid ? 1 : 0);
  document.getElementById("status-players").textContent = count;

  const vs = document.getElementById("vs-settings");
  const btn = document.getElementById("start-game-btn");

  if (count === 0) {
    btn.disabled = true;
    vs.style.display = "none";
    document.getElementById("status-mode").textContent = "none";
  } else if (count === 1) {
    btn.disabled = false;
    vs.style.display = "none";
    document.getElementById("status-mode").textContent = "single";
  } else {
    btn.disabled = false;
    vs.style.display = "block";
    document.getElementById("status-mode").textContent = "vs";
  }
}

// send names to backend
async function sendPlayerNamesToServer() {
  await fetch("/api/save_players", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      player1_name: player1Name,
      player2_name: player2Name
    })
  });
}

// start game
async function startGame() {
  const players = (player1Uid ? 1 : 0) + (player2Uid ? 1 : 0);
  if (players === 0) {
    log("need players");
    return;
  }

  await sendPlayerNamesToServer();
  let mode = players === 1 ? "single" : "vs";

  let difficulty = "low";
  let rounds = 1;
  let grid = { cols: 2, rows: 2 };

  if (mode === "vs") {
    difficulty = document.getElementById("vs-difficulty").value;
    rounds = parseInt(document.getElementById("vs-rounds").value);
    grid = {
      cols: parseInt(document.getElementById("grid-cols").value),
      rows: parseInt(document.getElementById("grid-rows").value)
    };
  }

  const payload = {
    mode,
    difficulty,
    rounds,
    grid,
    player1_uid: player1Uid,
    player2_uid: player2Uid
  };

  log("start payload: " + JSON.stringify(payload));

  const res = await fetch("/api/esp/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  const data = await res.json();
  log("start response: " + JSON.stringify(data));

  if (!data.ok) {
    log("start failed");
  } else {
    log("game started id=" + data.game_id);
  }
}

async function stopGame() {
  await fetch("/api/esp/stop", { method: "POST" });
  log("game stopped");
}

async function resetYaw() {
  await fetch("/api/esp/resetyaw", { method: "POST" });
  log("yaw reset");
}

// poll rfid
async function pollRfid() {
  const res = await fetch("/api/rfid/last");
  if (res.status === 204) return;

  const data = await res.json();
  if (!data.ts) return;

  if (data.ts <= lastRfidTsSeen) return;
  lastRfidTsSeen = data.ts;

  handleRfidScan(data);
}

// poll state
async function pollGameState() {
  try {
    const res = await fetch("/api/state");
    const data = await res.json();

    const pill = document.getElementById("game-state-pill");
    if (data.running) {
      pill.textContent = "running";
      pill.className = "pill green";
    } else {
      pill.textContent = "stopped";
      pill.className = "pill yellow";
    }

    document.getElementById("statusRound").textContent =
      `Round: ${data.currentRound ?? "-"}`;

    document.getElementById("statusScore").textContent =
      `Score → P1 ⚡ ${data.score1 ?? 0} | P2 ⚡ ${data.score2 ?? 0}`;

    const banner = document.getElementById("winner-banner");
    if (data.winner) {
      banner.style.display = "block";
      banner.textContent = `Winner: ${data.winner}`;
    } else {
      banner.style.display = "none";
    }
  } catch {}
}

// init
document.addEventListener("DOMContentLoaded", () => {
  setInterval(pollGameState, 500);
  setInterval(pollRfid, 500);
  updateStartButtonState();
});
