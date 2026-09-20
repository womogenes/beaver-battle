"""Run from the repository root: .venv/bin/python -m checks.check_relay.

The relay is the only thing standing between an ESP-NOW receiver and the game, so it is
checked against the real bridge rather than against an idea of it.
"""

import json
import socket
import pathlib
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


def check_the_baud_matches_the_receiver():
    """A relay that disagrees with the sketch reads pure noise, with nothing to say why.

    Nothing at runtime can catch this: the bytes arrive, they are simply wrong, so the
    only symptom is every line failing to parse. Worth a check rather than a comment.
    """
    import re
    sketch = pathlib.Path(__file__).resolve().parent.parent / 'firmware' / 'arduino' \
        / 'receiver_espnow' / 'receiver_espnow.ino'
    found = re.search(r'const uint32_t BAUD = (\d+);', sketch.read_text())
    assert found, 'receiver sketch must declare BAUD'
    firmware_baud = int(found.group(1))

    source = (pathlib.Path(__file__).resolve().parent.parent / 'beaver_battle' / 'relay.py').read_text()
    default = re.search(r"'--baud', type=int, default=(\d+)", source)
    assert default, 'relay must declare a default baud'
    assert int(default.group(1)) == firmware_baud, (
        f'relay default {default.group(1)} must match receiver BAUD {firmware_baud}')

    # Two controllers at 50 Hz is the load the link has to carry, with the measured line
    # length; 115200 sat at 86 percent and the receiver dropped frames under backpressure.
    offered = 2 * 50 * 100
    assert offered / (firmware_baud / 10.0) < 0.35, (
        f'baud {firmware_baud} leaves too little headroom for two controllers')


check_lines_it_should_ignore()
check_the_bridge_accepts_what_the_relay_sends()
check_the_baud_matches_the_receiver()
print('Relay checks passed: packet filtering, both controllers reach the bridge on separate '
      'endpoints, both buttons arrive as player input, and the baud matches the receiver', flush=True)


def check_hit_command_return_path():
    from beaver_battle.model import FeedbackEvent
    config = dict(network=dict(bind='127.0.0.1', port=0, timeout=.5),
                  camera=dict(stale_seconds=.5), feedback=dict(enabled=True))
    bridge = ControllerBridge(config)
    bridge.start()
    relay = Relay(*bridge.connection.getsockname())
    try:
        for player_id in (1, 2):
            relay.send(packet(player_id, 1))
        deadline = time.monotonic()+2
        while len(bridge.active_ids(100))<2 and time.monotonic()<deadline:
            bridge.poll(100)
            time.sleep(.005)
        assert len(bridge.active_ids(100)) == 2
        bridge.feedback([FeedbackEvent(2, 1, 200)],100)
        bridge.send(100,force=True)
        received = {}
        deadline = time.monotonic()+2
        while len(received)<2 and time.monotonic()<deadline:
            for line in relay.commands():
                command=json.loads(line)
                received[command['id']]=command
                assert line.endswith(b'\n') and len(line.rstrip())<=250
            time.sleep(.005)
        assert received[1]['feedback_id'] == 0
        assert received[2]['feedback_id'] == 1
        assert received[2]['session'] == bridge.session
        assert received[2]['duration_ms'] == 200
        # A datagram from another socket must never become an actuator command.
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as stranger:
            stranger.sendto(json.dumps(received[2]).encode(),relay.sockets[2].getsockname())
            time.sleep(.01)
            assert relay.commands() == []
        for malformed in (b'[]',b'{bad',json.dumps(dict(received[2],duration_ms=True)).encode()):
            bridge.connection.sendto(malformed,relay.sockets[2].getsockname())
        time.sleep(.01)
        assert relay.commands() == []
    finally:
        relay.close()
        bridge.stop()
    print('Hit return path passed: correct controller, framing, invalid/unrelated command rejection')


check_hit_command_return_path()
