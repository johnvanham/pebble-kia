// PebbleKit JS companion — runs inside the Pebble mobile app.
//
// Phase 1 (demo mode): this file is intentionally a no-op stub. The watchapp
// currently sources all data from an on-watch demo module (demo_data.c) so
// the UI can be iterated on without a phone or proxy.
//
// Phase 2 (real data) will add:
//   - a Clay-based configuration page for proxy URL + bearer token
//   - appmessage listener that forwards status requests to the proxy
//   - response translator that packs Kia state into the AppMessage dictionary
//     declared in package.json's `messageKeys`

Pebble.addEventListener('ready', function () {
  console.log('pebble-kia companion ready (phase 1 stub, demo mode)');
});

Pebble.addEventListener('appmessage', function (e) {
  console.log('pebble-kia appmessage (ignored in demo mode): ' +
              JSON.stringify(e.payload));
});
