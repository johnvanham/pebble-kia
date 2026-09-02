// PebbleKit JS companion — runs inside the Pebble mobile app (or pypkjs
// in the emulator). Translates AppMessage requests from the watch into
// HTTP calls against the self-hosted proxy, packs responses back, and
// keeps the watch UI live while the app is open.

var Clay = require('pebble-clay');
var clayConfig = require('./config');
var mk = require('message_keys');
var clay = new Clay(clayConfig, null, { autoHandleEvents: false });

var MAX_VEHICLES = 4;
var POLL_MS = 15000;
// A wake takes about 30 s on a CCS2 car: the Kia library triggers it,
// waits for the car to report, then reads the result. An ordinary
// status read can turn into one too — the proxy upgrades a stale poll
// while the car is charging, and a read that lands during someone
// else's wake waits for that wake's answer. Long enough for all of
// that; short enough that a black-holed proxy still surfaces.
var HTTP_TIMEOUT_MS = 60000;

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

function friendlyHttpError(status, body) {
  // The proxy already says exactly what went wrong and what to do about
  // it ("Kia needs consent - open the Kia app and accept"), so prefer
  // that over anything restated here. Only Kia's failures are worth
  // spelling out; the rest are the watch's own fault and stay generic.
  try {
    var detail = JSON.parse(body).detail;
    if (typeof detail === 'string' && detail) return detail;
  } catch (e) { /* not JSON, fall through */ }
  if (status === 401) return 'Bad proxy token';
  if (status === 403) return 'Proxy forbidden';
  if (status === 404) return 'Vehicle not found';
  if (status === 429) return 'Kia rate limited';
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
  req.timeout = HTTP_TIMEOUT_MS;
  var timedOut = false;
  req.ontimeout = function () { timedOut = true; };
  req.onloadend = function () {
    if (timedOut) return cb(new Error('Proxy timed out'));
    if (req.status === 0) return cb(new Error("Can't reach proxy"));
    if (req.status >= 200 && req.status < 300) {
      try { return cb(null, JSON.parse(req.responseText)); }
      catch (e) { return cb(new Error('Bad proxy reply')); }
    }
    cb(new Error(friendlyHttpError(req.status, req.responseText)));
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
    OUTSIDE_TEMP_C: s.outside_temp_c | 0,
    ODO_KM: s.odo_km | 0,
    IS_CLIMATE_ON: s.is_climate_on ? 1 : 0,
    AUX_BATTERY_PCT: s.aux_battery_pct | 0,
    CHARGE_LIM_AC: s.charge_limit_ac | 0,
    CHARGE_LIM_DC: s.charge_limit_dc | 0,
    DOORS_OPEN: s.doors_open | 0,
    WINDOWS_OPEN: s.windows_open | 0,
    TRUNK_OPEN: s.trunk_open ? 1 : 0,
    HOOD_OPEN: s.hood_open ? 1 : 0,
    SUNROOF_OPEN: s.sunroof_open ? 1 : 0,
    EFF_KMPKWH_X10: Math.round(((s.efficiency_kmpkwh || 0) * 10)) | 0,
    // -128 is the "no reading" sentinel: 0 degC is a real temperature.
    BATT_TEMP_C: s.batt_temp_c == null ? -128 : s.batt_temp_c | 0,
    UPDATED_AT: parseIsoSeconds(s.updated_at),
    // Whether the car itself was woken for this reading. The watch uses
    // it two ways: to skip its launch wake when the reply it is
    // answering already came from the car, and to know that an ordinary
    // poll reply is not the wake it is still waiting on.
    FORCED: data && data.forced ? 1 : 0,
    UNIT_MILES: getConfig().unitMiles ? 1 : 0
  };
}

var currentVehicleId = null;
// Status requests currently waiting on the proxy. The poll loop stays
// out while this is non-zero: a wake holds a request for tens of
// seconds, and a new poll every 15 s on top of it would only queue
// duplicates behind the same answer. Requests the watch makes itself
// are never held back by it.
var statusInFlight = 0;

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

// force wakes the car (POST /refresh); fresh makes one ordinary read
// skip the proxy's cache window without waking anything, charging or
// not. Neither: the proxy's copy, or Kia's, whichever the proxy's own
// rules pick — including a wake if the car is charging and the last one
// has aged out.
function fetchStatus(vehicleId, opts) {
  var force = !!(opts && opts.force);
  var fresh = !!(opts && opts.fresh);
  var path = '/vehicles/' + encodeURIComponent(vehicleId) +
    (force ? '/refresh' : '/status' + (fresh ? '?fresh=1' : ''));
  statusInFlight++;
  (force ? httpPost : httpGet)(path, function (err, data) {
    statusInFlight--;
    if (err) return sendError(err.message);
    Pebble.sendAppMessage(statusMessage(vehicleId, data), null, function () {
      sendError('Watch inbox full');
    });
  });
}

function handleStatusRequest(vehicleId, force) {
  if (!vehicleId) return sendError('No vehicle selected');
  currentVehicleId = vehicleId;
  fetchStatus(vehicleId, { force: force });
}

function handleActionRequest(vehicleId, action) {
  if (!vehicleId) return sendError('No vehicle selected');
  if (!action) return sendError('No action given');
  var path = '/vehicles/' + encodeURIComponent(vehicleId) +
             '/actions/' + encodeURIComponent(action);
  httpPost(path, function (err) {
    if (err) return sendError(err.message);
    Pebble.sendAppMessage({ RESP_KIND: 'action_ok', ACTION: action },
      null, function () {
        sendError('Watch inbox full');
      });
    // The car reports its new state to Kia moments after executing a
    // command, so one cache-skipping read shows the outcome without the
    // half-minute a wake would cost. fresh=1 is exempt from the
    // charging upgrade for exactly this reason: stopping a charge must
    // not be followed by waking the car to ask about it.
    setTimeout(function () {
      if (currentVehicleId !== vehicleId) return;
      fetchStatus(vehicleId, { fresh: true });
    }, 8000);
  });
}

// --- Polling loop ----------------------------------------------------
//
// Kicks off once on ready and then every POLL_MS while the companion is
// alive. Only polls the current vehicle and only updates the UI. The
// proxy serves its own copy until LIVE_REFRESH_MIN_SECONDS is up —
// except while the car is charging, when a poll arriving
// LIVE_CHARGING_REFRESH_SECONDS after the last wake becomes one itself,
// so this loop is what keeps the charge rate and ETA moving on the
// wrist.

var pollTimer = null;

function pollTick() {
  if (!currentVehicleId || statusInFlight > 0) return;
  fetchStatus(currentVehicleId, {});
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
  if (kind === 'action') return handleActionRequest(id, p.ACTION);
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
