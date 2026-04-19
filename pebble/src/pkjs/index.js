// PebbleKit JS companion — runs inside the Pebble mobile app (or pypkjs
// in the emulator). Translates AppMessage requests from the watch into
// HTTP calls against the self-hosted proxy, packs responses back, and
// keeps the watch UI live while the app is open.
//
// Notifications are driven from the proxy via ntfy + OS notification
// bridging, NOT from here — that way pushes reach the watch even when
// this companion isn't running. Keeping them here too would duplicate.

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

var currentVehicleId = null;

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
    for (var i = 0; i < vs.length; i++) {
      var id = String(vs[i].id || '');
      var nick = String(vs[i].nickname || vs[i].model || id);
      out[mk.VEHICLE_ID + i] = id;
      out[mk.VEHICLE_NICK + i] = nick;
    }
    Pebble.sendAppMessage(out, null, function () {
      sendError('Watch inbox full');
    });
  });
}

function fetchAndDispatch(vehicleId, force) {
  var go = force ? httpPost : httpGet;
  var path = force
    ? '/vehicles/' + encodeURIComponent(vehicleId) + '/refresh'
    : '/vehicles/' + encodeURIComponent(vehicleId) + '/status';
  go(path, function (err, data) {
    if (err) return sendError(err.message);
    Pebble.sendAppMessage(statusMessage(vehicleId, data), null, function () {
      sendError('Watch inbox full');
    });
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
// alive. Only polls the current vehicle and only updates the UI —
// notifications come from the proxy's own detector via ntfy, so there
// is no diff/notify logic here.

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
