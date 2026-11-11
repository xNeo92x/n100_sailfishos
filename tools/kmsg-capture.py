#!/usr/bin/env python3
"""Connect to the phone's busybox telnetd, run `cat /dev/kmsg`, record until the link dies."""
import socket
import sys
import time

host, port, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240


def strip(sock, data):
    """Refuse every telnet option and return the plain payload."""
    plain, i = bytearray(), 0
    while i < len(data):
        b = data[i]
        if b == IAC and i + 1 < len(data):
            cmd = data[i + 1]
            if cmd in (DO, DONT, WILL, WONT) and i + 2 < len(data):
                sock.sendall(bytes([IAC, WONT if cmd in (DO, DONT) else DONT, data[i + 2]]))
                i += 3; continue
            if cmd == SB:
                end = data.find(bytes([IAC, SE]), i)
                i = len(data) if end < 0 else end + 2; continue
            i += 2; continue
        plain.append(b); i += 1
    return bytes(plain)


deadline = time.time() + 3600
while time.time() < deadline:
    try:
        sock = socket.create_connection((host, port), timeout=5)
        break
    except OSError:
        time.sleep(2)
else:
    sys.exit('no connection')
sock.settimeout(None)
time.sleep(1.5)
sock.sendall(b'cat /dev/kmsg\r\n')
with open(out, 'ab', buffering=0) as stream:
    stream.write(('\n### capture started %s\n' % time.strftime('%H:%M:%S')).encode())
    while True:
        try:
            data = sock.recv(65536)
        except OSError as exc:
            stream.write(('\n### link error %s %s\n' % (exc, time.strftime('%H:%M:%S'))).encode()); break
        if not data:
            stream.write(('\n### link closed %s\n' % time.strftime('%H:%M:%S')).encode()); break
        stream.write(strip(sock, data))
