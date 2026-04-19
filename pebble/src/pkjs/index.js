// PebbleKit JS companion — runs inside the Pebble mobile app (or pypkjs
// in the emulator). Translates AppMessage requests from the watch into
// HTTP calls against the self-hosted proxy, and packs responses back.

var Clay = require('pebble-clay');
var clayConfig = require('./config');
var mk = require('message_keys');
var clay = new Clay(clayConfig, null, { autoHandleEvents: false });

var MAX_VEHICLES = 4;

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
  return { url: String(url).replace(/\/+$/, ''), token: String(token) };
}

function sendError(msg) {
  log('error: ' + msg);
  Pebble.sendAppMessage({ RESP_KIND: 'error', ERROR_MSG: msg });
}

function httpCall(method, path, cb) {
  var cfg = getConfig();
  if (!cfg.url) return cb(new Error('proxy URL not configured'));
  var req = new XMLHttpRequest();
  req.open(method, cfg.url + path, true);
  if (cfg.token) req.setRequestHeader('Authorization', 'Bearer ' + cfg.token);
  req.timeout = 15000;
  req.onload = function () {
    if (req.status >= 200 && req.status < 300) {
      try { return cb(null, JSON.parse(req.responseText)); }
      catch (e) { return cb(new Error('bad JSON')); }
    }
    cb(new Error('HTTP ' + req.status));
  };
  req.onerror = function () { cb(new Error('network error')); };
  req.ontimeout = function () { cb(new Error('timeout')); };
  try { req.send(); }
  catch (e) { cb(new Error(e.message)); }
}

function httpGet(path, cb)  { httpCall('GET', path, cb); }
function httpPost(path, cb) { httpCall('POST', path, cb); }

var PLUG_CODES = { unplugged: 0, ac: 1, dc: 2 };

function parseIsoSeconds(s) {
  if (!s) return 0;
  var t = Date.parse(s);
  return isNaN(t) ? 0 : Math.floor(t / 1000);
}

function handleListRequest() {
  httpGet('/vehicles', function (err, data) {
    if (err) return sendError(err.message);
    var vs = (data && data.vehicles) || [];
    if (vs.length > MAX_VEHICLES) vs = vs.slice(0, MAX_VEHICLES);
    var out = { RESP_KIND: 'list', VEHICLE_COUNT: vs.length };
    for (var i = 0; i < vs.length; i++) {
      out[mk.VEHICLE_ID + i] = String(vs[i].id || '');
      out[mk.VEHICLE_NICK + i] = String(vs[i].nickname || vs[i].model || vs[i].id || '');
    }
    Pebble.sendAppMessage(out, null, function () {
      sendError('watch rejected vehicle list');
    });
  });
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
    UPDATED_AT: parseIsoSeconds(s.updated_at)
  };
}

function handleStatusRequest(vehicleId, force) {
  if (!vehicleId) return sendError('missing vehicle id');
  var go = force ? httpPost : httpGet;
  var path = force
    ? '/vehicles/' + encodeURIComponent(vehicleId) + '/refresh'
    : '/vehicles/' + encodeURIComponent(vehicleId) + '/status';
  go(path, function (err, data) {
    if (err) return sendError(err.message);
    Pebble.sendAppMessage(statusMessage(vehicleId, data), null, function () {
      sendError('watch rejected status');
    });
  });
}

Pebble.addEventListener('ready', function () {
  log('companion ready');
  // Nudge the watch so it knows we're here. The watch kicks off the real
  // list fetch in response to any inbox message, which avoids the race
  // where the watch sends its first request before the companion has
  // connected.
  Pebble.sendAppMessage({ RESP_KIND: 'ready' });
});

Pebble.addEventListener('appmessage', function (e) {
  var p = e.payload || {};
  var kind = p.REQ_KIND;
  var id = p.REQ_ID || '';
  log('req ' + kind + ' ' + id);
  if (kind === 'list') return handleListRequest();
  if (kind === 'status') return handleStatusRequest(id, false);
  if (kind === 'refresh') return handleStatusRequest(id, true);
  sendError('unknown REQ_KIND: ' + kind);
});

Pebble.addEventListener('showConfiguration', function () {
  Pebble.openURL(clay.generateUrl());
});

Pebble.addEventListener('webviewclosed', function (e) {
  if (!e || !e.response) return;
  try {
    // clay.getSettings(response) writes to localStorage['clay-settings']
    // as a side effect — that's the persistence we care about.
    clay.getSettings(e.response);
    log('config saved');
  } catch (err) {
    log('bad config payload: ' + err.message);
  }
});
