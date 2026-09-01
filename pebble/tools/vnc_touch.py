#!/usr/bin/env python3
"""Inject touch gestures into the emery emulator over its VNC server.

Start the emulator with `pebble install --emulator emery --vnc` (and pass
--vnc to every later pebble command in the session — a command without it
respawns the emulator SDL-only, killing the running app). Then:

    python3 tools/vnc_touch.py 165 110 35 110 90     # swipe left
    python3 tools/vnc_touch.py 35 110 165 110 90     # swipe right
    python3 tools/vnc_touch.py 100 50 100 170 300    # drag down

Args are x0 y0 x1 y1 duration_ms [steps] [port], in emery screen pixels
(200x228); they are scaled to the VNC framebuffer. The SetEncodings
message is load-bearing: QEMU's VNC server only switches to
absolute-pointer mode (which the pebble-touch device requires) once the
client has negotiated encodings.
"""
import socket
import struct
import sys
import time

SCREEN_W, SCREEN_H = 200, 228


class Rfb:
    def __init__(self, port=5901):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.s.sendall(self._recv(12))  # mirror the server's RFB version
        types = self._recv(self._recv(1)[0])
        if 1 not in types:  # 1 = no authentication
            raise SystemExit(f"no 'None' VNC security type: {list(types)}")
        self.s.sendall(bytes([1]))
        if struct.unpack(">I", self._recv(4))[0] != 0:
            raise SystemExit("VNC security handshake failed")
        self.s.sendall(bytes([1]))  # ClientInit: shared
        self.fb_w, self.fb_h = struct.unpack(">HH", self._recv(4))
        self._recv(16)  # pixel format
        self._recv(struct.unpack(">I", self._recv(4))[0])  # name
        encs = [0, -257]  # raw, PointerTypeChange
        self.s.sendall(struct.pack(">BBH", 2, 0, len(encs)) +
                       b"".join(struct.pack(">i", e) for e in encs))
        time.sleep(0.1)

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.s.recv(n - len(buf))
            if not chunk:
                raise SystemExit("VNC connection closed")
            buf += chunk
        return buf

    def pointer(self, mask, x, y):
        self.s.sendall(struct.pack(">BBHH", 5, mask,
                                   min(self.fb_w - 1, x * self.fb_w // SCREEN_W),
                                   min(self.fb_h - 1, y * self.fb_h // SCREEN_H)))


def main():
    x0, y0, x1, y1, dur_ms = map(int, sys.argv[1:6])
    steps = int(sys.argv[6]) if len(sys.argv) > 6 else 10
    port = int(sys.argv[7]) if len(sys.argv) > 7 else 5901
    r = Rfb(port)
    r.pointer(0, x0, y0)
    time.sleep(0.05)
    r.pointer(1, x0, y0)
    for i in range(1, steps + 1):
        time.sleep(dur_ms / 1000 / steps)
        r.pointer(1, x0 + (x1 - x0) * i // steps, y0 + (y1 - y0) * i // steps)
    time.sleep(0.02)
    r.pointer(0, x1, y1)
    print(f"gesture sent (fb {r.fb_w}x{r.fb_h})")


if __name__ == "__main__":
    main()
