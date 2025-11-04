import argparse
import json
import signal
import socket
import sys
import threading
import time
from collections import deque

PEER_WAIT_TIMEOUT = 20.0
PUNCH_INTERVAL = 0.2
PUNCH_TIMEOUT = 15.0
KEEPALIVE_INTERVAL = 20.0
RECV_TIMEOUT = 0.5
MAX_DATAGRAM_SIZE = 4096

def parse_server(addr_str):
    ip, port = addr_str.split(":")
    return (ip, int(port))


def detect_local_ip_for_peer(server_addr):
    temp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    temp.connect(server_addr)
    local_ip = temp.getsockname()[0]
    temp.close()
    return local_ip


def create_bound_socket(bind_ip, bind_port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((bind_ip, bind_port))
    return sock


def send_register(sock, server, session, name):
    local = sock.getsockname()
    private = [local[0], local[1]]
    msg = {"type": "REGISTER", "session": session, "name": name, "private": private}
    sock.sendto(json.dumps(msg).encode(), server)
    print(f"[local={local}] Sent REGISTER to {server}")


def send_keepalive_loop(sock, server, session, name, stop_event):
    while not stop_event.wait(KEEPALIVE_INTERVAL):
        msg = {"type": "KEEPALIVE", "session": session, "name": name}
        sock.sendto(json.dumps(msg).encode(), server)
           

def wait_for_peer_info(sock, server, timeout=PEER_WAIT_TIMEOUT):
    end_time = time.time() + timeout
    while time.time() < end_time:
        data, addr = sock.recvfrom(MAX_DATAGRAM_SIZE)
    
        if addr != server:
            continue
        
        msg = json.loads(data.decode())
       
        if msg.get("type") == "PEER":
            return msg["peer"]
        if msg.get("type") == "WAIT":
            print("Server:", msg.get("msg"))
    return None


def punch_and_establish_connection(sock, peer_info, connected_event, peer_addr_holder):
    candidates = []
    if peer_info.get("pub"):
        candidates.append(tuple(peer_info["pub"]))
    if peer_info.get("private"):
        candidates.append(tuple(peer_info["private"]))

    print("Punch candidates:", candidates)
    end_time = time.time() + PUNCH_TIMEOUT
    last_punch = 0.0

    sock.setblocking(False)

    while time.time() < end_time:
        now = time.time()
        if now - last_punch >= PUNCH_INTERVAL:
            last_punch = now
            if connected_event.is_set():
                send_msg = b"__got_your_punch__"
            else:
                send_msg = b"__punch__"
            for c in candidates:
                sock.sendto(send_msg, c)
            
        try:
            data, addr = sock.recvfrom(MAX_DATAGRAM_SIZE)
        except Exception:
            time.sleep(0.05)
            continue
      
        if data.startswith(b"__punch__"):
            print(f"Received punch from {addr}")
            peer_addr_holder['addr'] = addr
            connected_event.set()
        
        if data.startswith(b"__got_your_punch__"):
            print(f"Received punch from {addr}; connection established.")
            peer_addr_holder['addr'] = addr
            connected_event.set()
            break

    if not connected_event.is_set():
        print("Punch timeout: direct connection not established within timeout.")


def stdin_reader_thread(stop_event, connected_event, peer_addr_holder, sock):
    while not stop_event.is_set():
        line = sys.stdin.readline()
        if not line:
            stop_event.set()
            break
        text = line.rstrip("\n")
        if not text:
            continue
        if connected_event.is_set() and peer_addr_holder.get('addr'):
            sock.sendto(text.encode(), peer_addr_holder['addr'])
            
        else:
            print("Connection not yet established.")


def chat_receive_loop(sock, stop_event, server):
    sock.setblocking(False)
    while not stop_event.is_set():
        try:
            data, addr = sock.recvfrom(MAX_DATAGRAM_SIZE)
        except Exception:
            time.sleep(0.05)
            continue

        print(f"[{addr}] {data.decode(errors='replace')}")


def run_client(server_str, session, name, port=None, bind_ip=None):
    server = parse_server(server_str)

    if bind_ip is None:
        bind_ip = detect_local_ip_for_peer(server)

    sock = create_bound_socket(bind_ip, port)
    local_bound = sock.getsockname()
    print(f"Socket bound to local {local_bound} (bind_ip={bind_ip})")

    stop_event = threading.Event()

    def _signal_handler(sig, frame):
        print("Signal " + sig +" received, shutting down...")
        stop_event.set()
        sock.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    send_register(sock, server, session, name)

    ka_thread = threading.Thread(target=send_keepalive_loop, args=(sock, server, session, name, stop_event), daemon=True)
    ka_thread.start()

    peer_info = wait_for_peer_info(sock, server)
    if not peer_info:
        print("No peer info received from rendezvous within timeout. Exiting.")
        stop_event.set()
        sock.close()
        return

    connected_event = threading.Event()
    peer_addr_holder = {'addr': None}

    punch_and_establish_connection(sock, peer_info, connected_event, peer_addr_holder)

    input_thread = threading.Thread(target=stdin_reader_thread,
                                    args=(stop_event, connected_event, peer_addr_holder, sock),
                                    daemon=True)

    if connected_event.is_set():
        print("Connected to peer:", peer_addr_holder['addr'])

        input_thread.start()
        chat_receive_loop(sock, stop_event, server)
        
    else:
        print("No direct connection established; exiting.")

    stop_event.set()
    sock.close()


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--server', required=True, help='rendezvous server ip:port')
    p.add_argument('--session', required=True, help='session id (both clients must use same)')
    p.add_argument('--name', required=True, help='your name')
    p.add_argument('--bind-ip', default=None, help='local interface ip to bind (default: auto-detected)')
    p.add_argument('--bind-port', type=int, default=0, help='local UDP port to bind (default: 0 -> random)')
    return p.parse_args()


if __name__ == '__main__':
    args = parse_args()
    run_client(args.server, args.session, args.name, args.port, args.bind_ip)