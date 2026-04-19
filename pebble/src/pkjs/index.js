// PebbleKit JS companion — runs inside the Pebble mobile app (or pypkjs
// in the emulator). Translates AppMessage requests from the watch into
// HTTP calls against the self-hosted proxy, and packs responses back.
// Also polls the current vehicle while the watchapp is alive and fires
// native Pebble notifications on state transitions (charge start/end,
// lock/unlock, plug/unplug, climate on/off).

var Clay = require('pebble-clay');
var clayConfig = require('./config');
var mk = require('message_keys');
var clay = new Clay(clayConfig, null, { autoHandleEvents: false });

var MAX_VEHICLES = 4;
var POLL_MS = 15000;

function log(msg) { console.log('[kia] ' + msg); }

function getConfig() {
  // Clay persists saved values under localStorage['clay-settings'] as a
  // flat {KEY: value} object (values pre-flattened from its
  // {value, precision, label} wrappers). Defaults come from config.js
  // and keep the emulator useful out-of-the-box before the user opens
  // Settings. Raw localStorage keys are a third fallback so the emulator
  // can be preloaded by tests without running the webview.
  var clayRaw = localStorage.getItem('clay-settings');
  var settings = {};
  if (clayRaw) {
    try { settings = JSON.parse(clayRaw) || {}; } catch (_) {}
  }
  var url = settings.PROXY_URL ||
            localStorage.getItem('proxy_url') ||
            'http://localhost:8000';
  var token = settings.PROXY_TOKEN ||
              localStorage.getItem('proxy_token') ||
              '';
  var unitMiles = settings.UNIT_MILES;
  if (unitMiles === undefined || unitMiles === null) unitMiles = true;
  return {
    url: String(url).replace(/\/+$/, ''),
    token: String(token),
    unitMiles: !!unitMiles
  };
}

function sendError(msg) {
  log('error: ' + msg);
  Pebble.sendAppMessage({ RESP_KIND: 'error', ERROR_MSG: msg });
}

function friendlyHttpError(status) {
  if (status === 401) return 'Bad proxy token';
  if (status === 403) return 'Proxy forbidden';
  if (status === 404) return 'Vehicle not found';
  if (status === 501) return 'Live mode not ready';
  if (status >= 500)  return 'Proxy error ' + status;
  if (status >= 400)  return 'Request rejected ' + status;
  return 'HTTP ' + status;
}

function httpCall(method, path, cb) {
  var cfg = getConfig();
  if (!cfg.url || !cfg.token) {
    return cb(new Error('Open Settings to configure proxy'));
  }
  var req = new XMLHttpRequest();
  req.open(method, cfg.url + path, true);
  req.setRequestHeader('Authorization', 'Bearer ' + cfg.token);
  req.timeout = 15000;
  var timedOut = false;
  req.ontimeout = function () { timedOut = true; };
  req.onloadend = function () {
    if (timedOut) return cb(new Error('Proxy timed out'));
    if (req.status === 0) return cb(new Error("Can't reach proxy"));
    if (req.status >= 200 && req.status < 300) {
      try { return cb(null, JSON.parse(req.responseText)); }
      catch (e) { return cb(new Error('Bad proxy reply')); }
    }
    cb(new Error(friendlyHttpError(req.status)));
  };
  try { req.send(); }
  catch (e) { cb(new Error("Can't reach proxy")); }
}

function httpGet(path, cb)  { httpCall('GET', path, cb); }
function httpPost(path, cb) { httpCall('POST', path, cb); }

var PLUG_CODES = { unplugged: 0, ac: 1, dc: 2 };

function parseIsoSeconds(s) {
  if (!s) return 0;
  var t = Date.parse(s);
  return isNaN(t) ? 0 : Math.floor(t / 1000);
}

function statusMessage(vehicleId, data) {
  var s = (data && data.status) || {};
  return {
    RESP_KIND: 'status',
    STATUS_ID: String(vehicleId),
    SOC_PCT: s.soc_pct | 0,
    RANGE_KM: s.range_km | 0,
    IS_CHARGING: s.is_charging ? 1 : 0,
    CHARGE_KW_X10: Math.round(((s.charge_kw || 0) * 10)) | 0,
    CHARGE_ETA_MIN: s.charge_eta_min | 0,
    PLUG: PLUG_CODES[s.plug] != null ? PLUG_CODES[s.plug] : 0,
    DOORS_LOCKED: s.doors_locked ? 1 : 0,
    CABIN_TEMP_C: s.cabin_temp_c | 0,
    ODO_KM: s.odo_km | 0,
    IS_CLIMATE_ON: s.is_climate_on ? 1 : 0,
    UPDATED_AT: parseIsoSeconds(s.updated_at),
    UNIT_MILES: getConfig().unitMiles ? 1 : 0
  };
}

// --- Notification bookkeeping ----------------------------------------
//
// `prevStatus[id]` holds the last response we saw for a given vehicle
// so we can detect transitions. `vehicleNames[id]` is the nickname for
// the notification title. `currentVehicleId` is what the polling loop
// auto-refreshes — it follows whichever vehicle the watch most recently
// asked about, so switching vehicles with Up/Down immediately redirects
// the poll. We only notify after the first observation for an id so a
// fresh boot doesn't spam "charging started" for an already-charging car.

var prevStatus = {};
var vehicleNames = {};
var currentVehicleId = null;

function notify(title, body) {
  log('notify: ' + title + ' — ' + body);
  try {
    Pebble.showSimpleNotificationOnPebble(title, body);
  } catch (e) {
    // Some runtimes expose the API under a different name; log and move on.
    log('notify failed: ' + e.message);
  }
}

function describePlug(code) {
  if (code === 1) return 'AC';
  if (code === 2) return 'DC';
  return 'unplugged';
}

function formatDistanceKm(km) {
  if (getConfig().unitMiles) {
    // Match the watch-side integer conversion (units.c) so notification
    // text and on-screen range agree.
    return Math.floor((km * 1000 + 804) / 1609) + ' mi';
  }
  return km + ' km';
}

function detectTransitions(vehicleId, msg) {
  var prev = prevStatus[vehicleId];
  prevStatus[vehicleId] = msg;
  if (!prev) return;  // first observation — establish baseline, don't notify

  var name = vehicleNames[vehicleId] || vehicleId;

  if (!prev.IS_CHARGING && msg.IS_CHARGING) {
    var kw = (msg.CHARGE_KW_X10 / 10).toFixed(1);
    var eta = msg.CHARGE_ETA_MIN > 0 ? ' • ETA ' + msg.CHARGE_ETA_MIN + ' min' : '';
    notify(name + ': Charging', kw + ' kW' + eta);
  } else if (prev.IS_CHARGING && !msg.IS_CHARGING) {
    var done = msg.SOC_PCT >= 80 ? 'Charge complete' : 'Charging stopped';
    notify(name + ': ' + done,
           msg.SOC_PCT + '% • ' + formatDistanceKm(msg.RANGE_KM));
  }

  if (prev.PLUG !== msg.PLUG) {
    if (prev.PLUG === 0 && msg.PLUG !== 0) {
      notify(name + ': Plugged in', describePlug(msg.PLUG));
    } else if (prev.PLUG !== 0 && msg.PLUG === 0) {
      notify(name + ': Unplugged',
             msg.SOC_PCT + '% • ' + formatDistanceKm(msg.RANGE_KM));
    }
  }

  if (prev.DOORS_LOCKED !== msg.DOORS_LOCKED) {
    notify(name + ': ' + (msg.DOORS_LOCKED ? 'Locked' : 'Unlocked'), '');
  }

  if (prev.IS_CLIMATE_ON !== msg.IS_CLIMATE_ON) {
    var sub = msg.CABIN_TEMP_C + '°C cabin';
    notify(name + ': Climate ' + (msg.IS_CLIMATE_ON ? 'on' : 'off'), sub);
  }
}

// --- Request handlers ------------------------------------------------

function handleListRequest() {
  httpGet('/vehicles', function (err, data) {
    if (err) return sendError(err.message);
    var vs = (data && data.vehicles) || [];
    if (vs.length > MAX_VEHICLES) vs = vs.slice(0, MAX_VEHICLES);
    var out = {
      RESP_KIND: 'list',
      VEHICLE_COUNT: vs.length,
      UNIT_MILES: getConfig().unitMiles ? 1 : 0
    };
    vehicleNames = {};
    for (var i = 0; i < vs.length; i++) {
      var id = String(vs[i].id || '');
      var nick = String(vs[i].nickname || vs[i].model || id);
      out[mk.VEHICLE_ID + i] = id;
      out[mk.VEHICLE_NICK + i] = nick;
      vehicleNames[id] = nick;
    }
    Pebble.sendAppMessage(out, null, function () {
      sendError('Watch inbox full');
    });
  });
}

function fetchAndDispatch(vehicleId, force, onDone) {
  var go = force ? httpPost : httpGet;
  var path = force
    ? '/vehicles/' + encodeURIComponent(vehicleId) + '/refresh'
    : '/vehicles/' + encodeURIComponent(vehicleId) + '/status';
  go(path, function (err, data) {
    if (err) { if (onDone) onDone(err); return sendError(err.message); }
    var msg = statusMessage(vehicleId, data);
    detectTransitions(vehicleId, msg);
    Pebble.sendAppMessage(msg, null, function () {
      sendError('Watch inbox full');
    });
    if (onDone) onDone(null);
  });
}

function handleStatusRequest(vehicleId, force) {
  if (!vehicleId) return sendError('No vehicle selected');
  currentVehicleId = vehicleId;
  fetchAndDispatch(vehicleId, force);
}

// --- Polling loop ----------------------------------------------------
//
// Kicks off once on ready and then every POLL_MS while the companion is
// alive. Only polls the current vehicle — polling all is overkill for
// the demo and would increase battery drain on a real link. Error
// handling in fetchAndDispatch already suppresses duplicate error
// messages during a stretch of failures.

var pollTimer = null;

function pollTick() {
  if (!currentVehicleId) return;
  fetchAndDispatch(currentVehicleId, false);
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(pollTick, POLL_MS);
}

// --- Pebble event wiring ---------------------------------------------

Pebble.addEventListener('ready', function () {
  log('companion ready');
  Pebble.sendAppMessage({ RESP_KIND: 'ready' });
  startPolling();
});

Pebble.addEventListener('appmessage', function (e) {
  var p = e.payload || {};
  var kind = p.REQ_KIND;
  var id = p.REQ_ID || '';
  log('req ' + kind + ' ' + id);
  if (kind === 'list') return handleListRequest();
  if (kind === 'status') return handleStatusRequest(id, false);
  if (kind === 'refresh') return handleStatusRequest(id, true);
  sendError('Bad request from watch');
});

Pebble.addEventListener('showConfiguration', function () {
  Pebble.openURL(clay.generateUrl());
});

Pebble.addEventListener('webviewclosed', function (e) {
  if (!e || !e.response) return;
  try {
    clay.getSettings(e.response);
    log('config saved');
  } catch (err) {
    log('bad config payload: ' + err.message);
  }
});
