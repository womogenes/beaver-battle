"""Feed ESP-NOW controller packets into the game's UDP port.

A laptop cannot speak ESP-NOW, so a third board runs receiver_espnow and writes each packet
it hears to USB serial as a line. This turns those lines back into the UDP datagrams the
game already listens for, which means the bridge, the protocol and the game are untouched
and the radio can be swapped without any of them noticing.

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
        self.target = (host, port)
        self.sockets = {}
        self.counts = {}

    def send(self, line):
        player_id = controller_of(line)
        if player_id is None:
            return None
        if player_id not in self.sockets:
            self.sockets[player_id] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.counts[player_id] = 0
        self.sockets[player_id].sendto(line if isinstance(line, bytes) else line.encode(), self.target)
        self.counts[player_id] += 1
        return player_id

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
    with serial.Serial(args.port, args.baud, timeout=0.2) as link:
        print(f'relaying {args.port} -> {args.host}:{args.udp}; controllers appear as they speak',
              flush=True)
        try:
            while True:
                line = link.readline().strip()
                if line:
                    relay.send(line)
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
