import json
import time

from beaver_battle.model import FeedbackEvent, VisionSnapshot
from beaver_battle.network import ControllerBridge, IdentityScheduler, newer


def main():
    config = dict(network=dict(bind='127.0.0.1', port=0, timeout=0.5),
                  camera=dict(stale_seconds=0.5, identity_interval=1.5, identity_window=0.18, identity_settle=0.08),
                  feedback=dict(enabled=True))
    bridge = ControllerBridge(config)
    packet = dict(v=1, type='input', id=1, boot=20, seq=0, buttons=3, command_seq=0, laser=False)
    endpoint = ('127.0.0.1', 12345)
    now = time.monotonic()
    assert bridge.accept(json.dumps(packet), endpoint, now)
    assert not bridge.accept(json.dumps(packet), endpoint, now + 0.01)
    assert not bridge.accept('[]', endpoint, now)
    assert not bridge.accept('{', endpoint, now)
    packet['seq'] = 1
    assert not bridge.accept(json.dumps(packet), ('127.0.0.1', 12346), now + 0.01)
    assert bridge.accept(json.dumps(packet), endpoint, now + 0.01)
    snapshot = VisionSnapshot(timestamp=now, aims={1: (100, 200)}, confidence={1: 1.0})
    assert bridge.inputs(snapshot, now + 0.02)[1].fire
    assert bridge.inputs(snapshot, now + 0.02)[1].aim == (100, 200)
    stale = bridge.inputs(snapshot, now + 1)[1]
    assert not stale.connected and not stale.fire and not stale.special and stale.aim is None
    bridge.feedback([FeedbackEvent(1, 1)], now + 0.03)
    assert bridge.controllers[1].feedback_id == 1
    bridge.feedback([FeedbackEvent(1, 2)], now + 1)
    assert bridge.controllers[1].feedback_id == 1
    packet.update(boot=21, seq=0, buttons=0)
    assert bridge.accept(json.dumps(packet), endpoint, now + 0.04)
    packet.update(boot=20, seq=2)
    assert not bridge.accept(json.dumps(packet), endpoint, now + 0.05)
    assert newer(0, 0xFFFFFFFF) and not newer(0xFFFFFFFF, 0)
    assert not newer(3, 3)
    bridge.start()
    bridge.set_target(1, now)
    bridge.send(now, force=True)
    assert bridge.controllers[1].desired[0]
    bridge.stop()
    print('Network checks passed: malformed/reordered packets, conflicts, restart, timeout, feedback and wrapping')


main()

# Exercise actual UDP delivery and the laser scheduler across stale acknowledgements,
# lease expiry, and a controller joining during a supposedly solo observation.
import socket


class RecordingVision:
    identity = None
    ready_since = float('inf')

    def set_identity(self, identity, ready_since):
        self.identity, self.ready_since = identity, ready_since


def check_identity():
    config = dict(network=dict(bind='127.0.0.1', port=0, timeout=0.5),
                  camera=dict(stale_seconds=0.5, identity_interval=1.5, identity_window=0.18, identity_settle=0.08),
                  feedback=dict(enabled=True))
    bridge, vision = ControllerBridge(config), RecordingVision()
    scheduler = IdentityScheduler(config)
    peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    peer.bind(('127.0.0.1', 0))
    peer.settimeout(1)
    bridge.start()
    try:
        now = 100.0
        packet = dict(v=1, type='input', id=1, boot=20, seq=0, buttons=0, command_seq=0, laser=True)
        peer.sendto(json.dumps(packet).encode(), bridge.connection.getsockname())
        bridge.poll(now)
        scheduler.update(now, bridge, vision, True)
        assert vision.identity == 1 and vision.ready_since == float('inf')
        assert not bridge.acknowledged(now)  # No command has been sent yet.
        bridge.send(now, force=True)
        command = json.loads(peer.recv(2048))
        assert command['laser'] and command['type'] == 'command'
        packet.update(seq=1)
        bridge.accept(json.dumps(packet), peer.getsockname(), now + 0.01)
        assert not bridge.acknowledged(now + 0.01)  # Old acknowledgement, right boolean.
        packet.update(seq=2, command_seq=command['seq'])
        bridge.accept(json.dumps(packet), peer.getsockname(), now + 0.02)
        scheduler.update(now + 0.02, bridge, vision, True)
        assert abs(vision.ready_since - 100.10) < 1e-6
        newcomer = dict(packet, id=2, boot=21, seq=0, laser=True, command_seq=0)
        bridge.accept(json.dumps(newcomer), ('127.0.0.1', 12346), now + 0.03)
        scheduler.update(now + 0.03, bridge, vision, True)
        assert vision.ready_since == float('inf')  # Rejoin cancels prior readiness.
        bridge.controllers[2].last_on_sent = now + 0.40
        packet.update(seq=3)
        bridge.accept(json.dumps(packet), peer.getsockname(), now + 0.60)
        assert not bridge.acknowledged(now + 0.60)  # Inactive laser could still be on.
        packet.update(seq=4)
        bridge.accept(json.dumps(packet), peer.getsockname(), now + 1.00)
        assert bridge.acknowledged(now + 1.00)
        scheduler.update(now + 1.00, bridge, vision, False)
        assert bridge.target == 0 and vision.ready_since == float('inf')
    finally:
        bridge.stop()
        peer.close()
    print('Identity checks passed: UDP, command acknowledgements, mid-window rejoin and dropout lease')


check_identity()


def check_all_on():
    config = dict(network=dict(bind='127.0.0.1', port=0, timeout=0.5),
                  camera=dict(stale_seconds=0.5, identity_interval=1.5, identity_window=0.18, identity_settle=0.08),
                  feedback=dict(enabled=True))
    bridge, vision = ControllerBridge(config), RecordingVision()
    scheduler = IdentityScheduler(config)
    bridge.start()
    try:
        for player_id in (1, 2):
            packet = dict(v=1, type='input', id=player_id, boot=player_id, seq=0, buttons=0, command_seq=0, laser=False)
            bridge.accept(json.dumps(packet), ('127.0.0.1', 12340 + player_id), 100.0)
        scheduler.update(100.0, bridge, vision, True)
        bridge.send(100.0, force=True)
        for player_id, controller in bridge.controllers.items():
            packet = dict(v=1, type='input', id=player_id, boot=player_id, seq=1, buttons=0,
                          command_seq=controller.outgoing_seq, laser=player_id == 1)
            bridge.accept(json.dumps(packet), controller.endpoint, 100.01)
        scheduler.update(100.01, bridge, vision, True)
        assert abs(vision.ready_since - 100.09) < 1e-6
        scheduler.update(100.28, bridge, vision, True)
        assert vision.identity is None and vision.ready_since == float('inf')
        bridge.send(100.28, force=True)
        scheduler.update(100.29, bridge, vision, True)
        assert vision.ready_since == float('inf')  # Player 2 has not acknowledged all-on.
        for player_id, controller in bridge.controllers.items():
            packet = dict(v=1, type='input', id=player_id, boot=player_id, seq=2, buttons=0,
                          command_seq=controller.outgoing_seq, laser=True)
            bridge.accept(json.dumps(packet), controller.endpoint, 100.30)
        scheduler.update(100.30, bridge, vision, True)
        assert abs(vision.ready_since - 100.38) < 1e-6
        assert scheduler.next_probe >= vision.ready_since + .15
    finally:
        bridge.stop()
    print('All-on checks passed: restore acknowledgements and minimum observation interval')


check_all_on()
