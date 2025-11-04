import argparse
import json
import signal
import socket
import sys
import time
from collections import defaultdict

# sessions[session_id] = [ { 'name': ..., 'pub_addr': (ip,port), 'private_addr': (ip,port) , 'last_seen': ts } , ... ]
sessions = defaultdict(list)

BIND_DEFAULT_IP = "10.0.0.1"
BIND_DEFAULT_PORT = 1234
MAX_DATAGRAM_SIZE = 4096
CLIENT_ENTRY_TTL = 60.0

def delete_outdated_clients():
    now = time.time()
    cutoff = now - CLIENT_ENTRY_TTL
    for sid in list(sessions.keys()):
        sessions[sid] = [c for c in sessions[sid] if cutoff < c['last_seen']]
        if not sessions[sid]:
            del sessions[sid]


def make_response(peer_info):
    return json.dumps({'type': 'PEER', 'peer': peer_info}).encode()


def handle_register(sock, addr, msg):
    sid = msg.get('session')
    name = msg.get('name', '')
    private = msg.get('private')
    print(f"[{sid}] REGISTER from {addr} name={name} private={private}")
    entry = {
        'name': name,
        'pub_addr': [addr[0], addr[1]],
        'private_addr': private,
        'last_seen': time.time()
    }
    
    sessions[sid] = [c for c in sessions[sid] if c['name'] != name]
    sessions[sid].append(entry)

    if len(sessions[sid]) >= 2:
        a, b = sessions[sid][0], sessions[sid][1]
        
        pa = {'name': b['name'], 'pub': b['pub_addr'], 'private': b['private_addr']}
        pb = {'name': a['name'], 'pub': a['pub_addr'], 'private': a['private_addr']}
        
        sock.sendto(make_response(pa), tuple(a['pub_addr']))
        sock.sendto(make_response(pb), tuple(b['pub_addr']))
        print(f"[{sid}] Exchanged peers: {a['name']} <-> {b['name']}")
    else:
        ack = json.dumps({'type': 'WAIT', 'msg': 'waiting for peer'}).encode()
        sock.sendto(ack, addr)


def handle_keepalive(msg):
    sid = msg.get('session')
    name = msg.get('name')
    for c in sessions.get(sid, []):
        if c['name'] == name:
            c['last_seen'] = time.time()
            break


def run_server(bind_ip, bind_port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((bind_ip, bind_port))
    print(f"Rendezvous server listening on {bind_ip}:{bind_port}")

    def _signal_handler(sig, frame):
        print("Signal " + sig +" received, shutting down...")
        sock.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    while True:
        data, addr = sock.recvfrom(MAX_DATAGRAM_SIZE)
        delete_outdated_clients()
        try:
            msg = json.loads(data.decode())
        except Exception as e:
            print("Invalid msg from", addr, e)
            continue

        msg_type = msg.get('type')

        if msg_type == 'REGISTER':
            handle_register(sock, addr, msg)

        elif msg_type == 'KEEPALIVE':
            handle_keepalive(msg)
            
        else:
            print("Unknown message type from", addr, msg.get('type'))


def parse_args():
    p = argparse.ArgumentParser(description="Simple UDP rendezvous server for P2P NAT hole-punching.")
    p.add_argument("--bind-ip", default=BIND_DEFAULT_IP, help="IP to bind (default 0.0.0.0)")
    p.add_argument("--bind-port", type=int, default=BIND_DEFAULT_PORT, help=f"UDP port to bind (default {BIND_DEFAULT_PORT})")
    return p.parse_args()


if __name__ == '__main__':
    args = parse_args()
    run_server(args.bind_ip, args.bind_port)