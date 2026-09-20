"""Run from the repository root: .venv/bin/python -m checks.check_relay.

The relay is the only thing standing between an ESP-NOW receiver and the game, so it is
checked against the real bridge rather than against an idea of it.
"""

import json
import socket
import time

from beaver_battle.model import VisionSnapshot
from beaver_battle.network import ControllerBridge
from beaver_battle.relay import Relay, controller_of


def packet(player_id, seq, buttons=0, boot=7):
    return json.dumps({'v': 1, 'type': 'input', 'id': player_id, 'boot': boot, 'seq': seq,
                       'buttons': buttons, 'command_seq': 0, 'laser': False})


def check_lines_it_should_ignore():
    assert controller_of(packet(1, 0)) == 1
    assert controller_of(packet(2, 0)) == 2
    assert controller_of('{"receiver":"ready","channel":1}') is None, 'the banner is not a packet'
    assert controller_of('not json at all') is None
    assert controller_of('[]') is None, 'a list is not a packet'
    assert controller_of(json.dumps({'v': 1, 'type': 'command'})) is None
    assert controller_of(packet(3, 0)) is None, 'this is a two player game'
    assert controller_of(packet('1', 0)) is None, 'the id must be an integer'


def check_the_bridge_accepts_what_the_relay_sends():
    """Both controllers must reach the bridge and keep separate identities."""
    config = dict(network=dict(bind='127.0.0.1', port=0, timeout=0.5),
                  camera=dict(stale_seconds=0.5), feedback=dict(enabled=True))
    bridge = ControllerBridge(config)
    bridge.start()
    try:
        host, port = bridge.connection.getsockname()
        relay = Relay(host, port)
        try:
            for seq in range(3):
                assert relay.send(packet(1, seq, buttons=1)) == 1
                assert relay.send(packet(2, seq, buttons=2)) == 2
            deadline = time.monotonic() + 2.0
            now = 100.0
            while time.monotonic() < deadline:
                bridge.poll(now)
                if sorted(bridge.active_ids(now)) == [1, 2]:
                    break
                time.sleep(0.005)
            assert sorted(bridge.active_ids(now)) == [1, 2], 'both controllers must arrive'
            # Each controller keeps its own port, so neither looks like an impostor.
            assert len({id(s) for s in relay.sockets.values()}) == 2
            endpoints = {i: bridge.controllers[i].endpoint for i in (1, 2)}
            assert endpoints[1] != endpoints[2], 'controllers must not share an endpoint'
            inputs = bridge.inputs(VisionSnapshot(timestamp=now), now)
            assert inputs[1].fire and not inputs[1].special, 'button one reaches the game'
            assert inputs[2].special and not inputs[2].fire, 'button two reaches the game'
        finally:
            relay.close()
    finally:
        bridge.stop()


check_lines_it_should_ignore()
check_the_bridge_accepts_what_the_relay_sends()
print('Relay checks passed: packet filtering, both controllers reach the bridge on separate '
      'endpoints, and both buttons arrive as player input', flush=True)
