"""Feed ESP-NOW controller packets into the game's UDP port.

A third ESP32 bridges ESP-NOW packets and newline-delimited USB serial. Input packets
become UDP datagrams for the game; validated game replies return over serial with the
controller ID selected by its socket. Hit feedback requires the bidirectional ESP-IDF
receiver and controller firmware.

Each controller gets its own socket, and so its own stable source port. The bridge treats a
controller's endpoint as its identity and drops packets that appear to come from somewhere
new while the old one is still live, so sharing one socket between controllers would make
them look like an impostor of each other.

    uv run python -m beaver_battle.relay --port /dev/cu.usbserial-0001
"""

import argparse
import json
import socket
import sys
import time


def controller_of(line):
    """The controller a line belongs to, or None if it is not an input packet."""
    try:
        packet = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(packet, dict) or packet.get('type') != 'input':
        return None
    player_id = packet.get('id')
    return player_id if type(player_id) is int and 1 <= player_id <= 2 else None


class Relay:
    """One socket per controller, so each keeps a stable endpoint from the bridge's view."""

    def __init__(self, host, port):
        self.target = (socket.gethostbyname(host), port)
        self.sockets = {}
        self.counts = {}

    def send(self, line):
        player_id = controller_of(line)
        if player_id is None:
            return None
        if player_id not in self.sockets:
            self.sockets[player_id] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sockets[player_id].setblocking(False)
            self.counts[player_id] = 0
        self.sockets[player_id].sendto(line if isinstance(line, bytes) else line.encode(), self.target)
        self.counts[player_id] += 1
        return player_id

    def commands(self):
        """Route replies from the game's socket back to their controller over serial."""
        lines = []
        for player_id, connection in self.sockets.items():
            latest = None
            for count in range(64):
                try:
                    data, endpoint = connection.recvfrom(2049)
                except BlockingIOError:
                    break
                if endpoint != self.target:
                    continue
                try:
                    packet = json.loads(data)
                    integer_keys = ('session', 'seq', 'feedback_id', 'duration_ms')
                    valid = (isinstance(packet, dict) and type(packet.get('v')) is int
                             and packet['v'] == 1 and packet.get('type') == 'command'
                             and type(packet.get('laser')) is bool
                             and all(type(packet.get(key)) is int and 0 <= packet[key] <= 0xFFFFFFFF
                                     for key in integer_keys)
                             and packet['duration_ms'] <= 500)
                    if not valid:
                        continue
                    # The socket, not a supplied id, determines the destination.
                    packet = {key: packet[key] for key in ('v', 'type', 'laser', *integer_keys)}
                    packet['id'] = player_id
                    line = json.dumps(packet, separators=(',', ':')).encode()
                    if len(line) <= 250:
                        latest = line + b'\n'
                except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                    continue
            if latest is not None:
                lines.append(latest)
        return lines

    def close(self):
        for connection in self.sockets.values():
            connection.close()
        self.sockets.clear()


def main():
    parser = argparse.ArgumentParser(description='Relay ESP-NOW controller packets to the game')
    parser.add_argument('--port', required=True, help='serial port of the receiver board')
    parser.add_argument('--baud', type=int, default=460800)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--udp', type=int, default=4210)
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args()
    try:
        import serial
    except ImportError:
        parser.error('pyserial is needed: uv run --with pyserial python -m beaver_battle.relay ...')
    relay = Relay(args.host, args.udp)
    reported = time.monotonic()
    with serial.Serial(args.port, args.baud, timeout=0.005, write_timeout=0.1) as link:
        print(f'relaying {args.port} -> {args.host}:{args.udp}; controllers appear as they speak',
              flush=True)
        pending = bytearray()
        try:
            while True:
                pending.extend(link.read(min(4096, max(1, link.in_waiting))))
                while b'\n' in pending:
                    line, _, rest = pending.partition(b'\n')
                    pending = bytearray(rest)
                    if len(line) <= 250:
                        relay.send(bytes(line).strip())
                if len(pending) > 250:
                    pending.clear()
                for command in relay.commands():
                    link.write(command)
                if not args.quiet and time.monotonic() - reported >= 2.0:
                    reported = time.monotonic()
                    seen = ', '.join(f'{k}:{v}' for k, v in sorted(relay.counts.items())) or 'nothing yet'
                    print(f'packets relayed  {seen}', flush=True)
        except KeyboardInterrupt:
            pass
        finally:
            relay.close()


if __name__ == '__main__':
    main()
