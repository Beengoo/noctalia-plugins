#!/usr/bin/env python3
"""
Carla OSC bridge
"""

import argparse
import json
import os
import socket
import struct
import threading
import time
import signal
import sys

CARLA_HOST = os.environ.get("CARLA_OSC_HOST", "127.0.0.1")
CARLA_PORT = int(os.environ.get("CARLA_OSC_TCP_PORT", "22752"))
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = int(os.environ.get("CARLA_NOCTALIA_LISTEN_PORT", "22802"))
CARLA_NAME = os.environ.get("CARLA_OSC_NAME", "Carla")
STATE = os.environ.get("CARLA_NOCTALIA_STATE", "/tmp/noctalia-carla-bypass.json")
ACTIVE = {"client": None}


def request_refresh():
    client = ACTIVE["client"]
    if client is None:
        return
    try:
        client.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass

def pad4(data):
    return data + b"\0" * ((4 - len(data) % 4) % 4)


def osc_string(value):
    return pad4(value.encode("utf-8") + b"\0")


def osc_message(path, types, *args):
    body = osc_string(path) + osc_string("," + types)
    for typ, value in zip(types, args):
        if typ == "i":
            body += struct.pack(">i", int(value))
        elif typ == "f":
            body += struct.pack(">f", float(value))
        elif typ == "d":
            body += struct.pack(">d", float(value))
        elif typ == "h":
            body += struct.pack(">q", int(value))
        elif typ == "s":
            body += osc_string(str(value))
        else:
            raise ValueError("unsupported OSC type: " + typ)
    return body


def tcp_packet(message):
    return struct.pack(">I", len(message)) + message


def recv_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            return None
        data.extend(chunk)
    return bytes(data)


def recv_packet(sock):
    header = recv_exact(sock, 4)
    if header is None:
        return None
    size = struct.unpack(">I", header)[0]
    if size <= 0 or size > 16 * 1024 * 1024:
        raise ValueError("invalid OSC TCP packet size")
    return recv_exact(sock, size)


def read_padded_string(data, offset):
    end = data.index(b"\0", offset)
    value = data[offset:end].decode("utf-8", "replace")
    return value, (end + 4) & ~3


def parse_osc(data):
    path, offset = read_padded_string(data, 0)
    types, offset = read_padded_string(data, offset)
    if not types.startswith(","):
        raise ValueError("invalid OSC type tag")

    args = []
    for typ in types[1:]:
        if typ == "i":
            args.append(struct.unpack_from(">i", data, offset)[0])
            offset += 4
        elif typ == "f":
            args.append(struct.unpack_from(">f", data, offset)[0])
            offset += 4
        elif typ == "d":
            args.append(struct.unpack_from(">d", data, offset)[0])
            offset += 8
        elif typ == "h":
            args.append(struct.unpack_from(">q", data, offset)[0])
            offset += 8
        elif typ == "s":
            value, offset = read_padded_string(data, offset)
            args.append(value)
        else:
            raise ValueError("unsupported incoming OSC type: " + typ)

    return path, args


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.plugins = {}

    def write(self):
        with self.lock:
            plugins = [
                {
                    "id": pid,
                    "name": plugin["name"],
                    "active": bool(plugin["active"]),
                }
                for pid, plugin in sorted(self.plugins.items())
            ]

            tmp = STATE + ".tmp"
            os.makedirs(os.path.dirname(tmp) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"plugins": plugins}, f, separators=(",", ":"))
            os.replace(tmp, STATE)

    def info(self, args):
        # /info:
        # i i i i h h h s s s s s s s
        if len(args) < 8:
            return

        pid = int(args[0])
        name = str(args[7])

        with self.lock:
            old = self.plugins.get(pid)
            self.plugins[pid] = {
                "name": name or f"Plugin {pid}",
                "active": old["active"] if old else True,
            }
        self.write()

    def clear(self):
        with self.lock:
            if not self.plugins:
                return
            self.plugins.clear()
        self.write()

    def added(self, pid, name):
        with self.lock:
            if pid not in self.plugins:
                self.plugins[pid] = {
                    "name": name or f"Plugin {pid}",
                    "active": True,
                }
        self.write()

    def removed(self, pid):
        # Carla renumbers the plugins after a removed one
        with self.lock:
            self.plugins = {
                (k if k < pid else k - 1): v
                for k, v in self.plugins.items()
                if k != pid
            }
        self.write()

    def rename(self, pid, name):
        with self.lock:
            if pid in self.plugins:
                self.plugins[pid]["name"] = name
        self.write()

    def set_active(self, pid, active):
        with self.lock:
            if pid in self.plugins:
                self.plugins[pid]["active"] = active
        self.write()

    def local_active(self, args):
        if len(args) >= 2:
            self.set_active(int(args[0]), bool(args[1]))

    def iparams(self, args):
        # /iparams: i f f f f f f f  (id, active, drywet, volume, ...)
        if len(args) < 3:
            return
        self.set_active(int(args[0]), float(args[2]) > 0.0)

    def callback(self, args):
        if len(args) < 2:
            return

        action = int(args[0])
        pid = int(args[1])
        value_str = str(args[6]) if len(args) > 6 else ""

        if action == 1:
            self.added(pid, value_str)
        elif action == 2:
            self.removed(pid)
        elif action == 3:
            self.rename(pid, value_str)
        elif action == 4:
            self.set_active(pid, False)
        elif action == 5 and len(args) > 5 and int(args[2]) == -3:
            self.set_active(pid, float(args[5]) > 0.0)
        elif action == 30:
            self.clear()

    def dispatch(self, path, args):
        if path.endswith("/info"):
            self.info(args)
        elif path.endswith("/iparams"):
            self.iparams(args)
        elif path.endswith("/cb"):
            self.callback(args)
        elif path.endswith("/local_active"):
            self.local_active(args)
        elif path.endswith("/refresh"):
            request_refresh()


STATE_HOLDER = State()

def client_url():
    return f"osc.tcp://{LISTEN_HOST}:{LISTEN_PORT}/noctalia"

def register():
    message = osc_message("/register", "s", client_url())
    sock = socket.create_connection((CARLA_HOST, CARLA_PORT), timeout=3)
    sock.settimeout(None)
    sock.sendall(tcp_packet(message))
    return sock

def unregister():
    message = osc_message("/unregister", "s", client_url())
    try:
        with socket.create_connection((CARLA_HOST, CARLA_PORT), timeout=2) as sock:
            sock.sendall(tcp_packet(message))
    except OSError as exc:
        print(f"Unregister skipped: {exc}", flush=True)

def listener():
    try:
        server = make_server()
    except OSError as exc:
        print(f"Cannot listen on {LISTEN_HOST}:{LISTEN_PORT}: {exc}", flush=True)
        sys.exit(1)

    threading.Thread(target=accept_loop, args=(server,), daemon=True).start()

    while True:
        try:
            run_session()
        except Exception as exc:
            print(f"Session error: {exc}", flush=True)
        time.sleep(1)

def handle_packet(packet):
    try:
        path, args = parse_osc(packet)
        print(f"OSC: {path} {args}", flush=True)
        STATE_HOLDER.dispatch(path, args)
    except Exception as exc:
        print(f"OSC decode error: {exc}", flush=True)

def read_loop(sock):
    while True:
        packet = recv_packet(sock)
        if packet is None:
            return
        handle_packet(packet)

def read_connection(conn):
    try:
        with conn:
            read_loop(conn)
    except Exception as exc:
        print(f"Callback connection error: {exc}", flush=True)


def accept_loop(server):
    while True:
        try:
            conn, _ = server.accept()
        except OSError:
            return
        threading.Thread(target=read_connection, args=(conn,), daemon=True).start()

def make_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((LISTEN_HOST, LISTEN_PORT))
    server.listen(4)
    return server

def run_session():
    unregister()
    STATE_HOLDER.clear()

    client = register()
    ACTIVE["client"] = client
    try:
        print(f"Registered with Carla {CARLA_HOST}:{CARLA_PORT}", flush=True)
        read_loop(client)
        print("Carla connection closed", flush=True)
    finally:
        ACTIVE["client"] = None
        client.close()
        unregister()
        STATE_HOLDER.clear()

def update_local_active(plugin_id, active):
    with STATE_HOLDER.lock:
        if plugin_id in STATE_HOLDER.plugins:
            STATE_HOLDER.plugins[plugin_id]["active"] = active
    STATE_HOLDER.write()


def send_local(path, types="", *args):
    message = osc_message(path, types, *args)
    try:
        with socket.create_connection((LISTEN_HOST, LISTEN_PORT), timeout=1) as sock:
            sock.sendall(tcp_packet(message))
    except OSError:
        pass


def notify_daemon(plugin_id, active):
    send_local("/noctalia/local_active", "ii", plugin_id, 1 if active else 0)


def send_active(plugin_id, active):
    path = f"/{CARLA_NAME}/{plugin_id}/set_drywet"
    message = osc_message(path, "f", 1.0 if active else 0.0)

    with socket.create_connection((CARLA_HOST, CARLA_PORT), timeout=2) as sock:
        sock.sendall(tcp_packet(message))

    notify_daemon(plugin_id, active)

def run_daemon():
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: sys.exit(0))
    STATE_HOLDER.write()
    try:
        listener()
    except KeyboardInterrupt:
        pass

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--set-active", nargs=2, metavar=("PLUGIN_ID", "ACTIVE"))
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--daemon", action="store_true")
    args = parser.parse_args()

    if args.set_active:
        send_active(int(args.set_active[0]), args.set_active[1] != "0")
    elif args.daemon:
        run_daemon()
    elif args.refresh:
        send_local("/noctalia/refresh")
    elif args.refresh:
        # The daemon receives live state from Carla. This is intentionally a
        # no-op; it exists so the panel can request a harmless refresh.
        pass
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
