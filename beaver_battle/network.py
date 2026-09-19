import json
import secrets
import socket
import time
from dataclasses import dataclass, field

from beaver_battle.model import PlayerInput


def newer(sequence, previous):
    return 0 < ((sequence - previous) & 0xFFFFFFFF) < 0x80000000


def unsigned(value):
    return type(value) is int and 0 <= value <= 0xFFFFFFFF


@dataclass
class Controller:
    player_id: int
    endpoint: tuple
    boot: int
    seq: int
    received: float
    buttons: int = 0
    laser: bool = False
    command_seq: int = 0
    retired_boots: list = field(default_factory=list)
    feedback_id: int = 0
    duration_ms: int = 200
    outgoing_seq: int = 0
    desired: tuple | None = None
    last_on_sent: float = float('-inf')


@dataclass
class ControllerBridge:
    config: dict
    controllers: dict = field(default_factory=dict)
    session: int = field(default_factory=lambda: secrets.randbits(32))
    connection: socket.socket | None = None
    target: int | None = 0
    changed: float = 0.0
    last_send: float = 0.0
    feedback_sequence: int = 0
    rejected: int = 0

    def start(self):
        settings = self.config['network']
        self.connection = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.connection.bind((settings['bind'], settings['port']))
        self.connection.setblocking(False)

    def stop(self):
        if self.connection:
            self.set_target(0)
            self.send(time.monotonic(), force=True)
            self.connection.close()
            self.connection = None

    def accept(self, data, endpoint, now):
        try:
            packet = json.loads(data)
            if not isinstance(packet, dict):
                return False
            player_id = packet.get('id')
            valid = (type(packet.get('v')) is int and packet['v'] == 1 and packet.get('type') == 'input'
                     and type(player_id) is int and 1 <= player_id <= 3
                     and all(unsigned(packet.get(key)) for key in ('boot', 'seq', 'command_seq'))
                     and type(packet.get('buttons')) is int and 0 <= packet['buttons'] <= 3
                     and type(packet.get('laser')) is bool)
            if not valid:
                return False
            current = self.controllers.get(player_id)
            if current:
                live = now - current.received < self.config['network']['timeout']
                if current.endpoint != endpoint and live:
                    return False
                if packet['boot'] in current.retired_boots:
                    return False
                if current.boot == packet['boot'] and not newer(packet['seq'], current.seq):
                    return False
                if current.boot != packet['boot']:
                    current.retired_boots = (current.retired_boots + [current.boot])[-8:]
                    current.boot = packet['boot']
                    current.desired = None
                current.endpoint = endpoint
            else:
                current = Controller(player_id, endpoint, packet['boot'], packet['seq'], now)
                self.controllers[player_id] = current
            current.seq = packet['seq']
            current.buttons = packet['buttons']
            current.command_seq = packet['command_seq']
            current.laser = packet['laser']
            current.received = now
            return True
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return False

    def poll(self, now):
        if not self.connection:
            return
        for count in range(64):
            try:
                data, endpoint = self.connection.recvfrom(2049)
            except BlockingIOError:
                break
            if len(data) > 2048 or not self.accept(data, endpoint, now):
                self.rejected += 1

    def active_ids(self, now):
        timeout = self.config['network']['timeout']
        return sorted(key for key, value in self.controllers.items() if now - value.received < timeout)

    def inputs(self, snapshot, now):
        result = {}
        for player_id, controller in self.controllers.items():
            connected = now - controller.received < self.config['network']['timeout']
            age = now - snapshot.timestamp
            valid = age < self.config['camera']['stale_seconds'] and snapshot.confidence.get(player_id, 0) >= 0.5
            result[player_id] = PlayerInput(
                player_id, snapshot.aims.get(player_id) if valid else None,
                bool(controller.buttons & 1) if connected else False,
                bool(controller.buttons & 2) if connected else False,
                connected, age,
            )
        return result

    def set_target(self, target, now=None):
        if self.target != target:
            self.target = target
            self.changed = time.monotonic() if now is None else now

    def acknowledged(self, now):
        ids = self.active_ids(now)
        expired = all(now - controller.last_on_sent >= 0.5 + self.config['camera']['identity_settle']
                      for player_id, controller in self.controllers.items() if player_id not in ids)
        return bool(ids) and expired and all(
            self.controllers[player_id].received >= self.changed
            and self.controllers[player_id].desired is not None
            and self.controllers[player_id].desired[0] == (self.target is None or self.target == player_id)
            and self.controllers[player_id].command_seq == self.controllers[player_id].outgoing_seq
            and self.controllers[player_id].laser == (self.target is None or self.target == player_id)
            for player_id in ids
        )

    def feedback(self, events, now):
        if not self.config['feedback']['enabled']:
            return
        for event in events:
            if event.player_id in self.active_ids(now):
                self.feedback_sequence = (self.feedback_sequence + 1) & 0xFFFFFFFF or 1
                controller = self.controllers[event.player_id]
                controller.feedback_id = self.feedback_sequence
                controller.duration_ms = max(0, min(500, event.duration_ms))

    def send(self, now, force=False):
        if not self.connection or (not force and now - self.last_send < 0.05):
            return
        self.last_send = now
        for player_id in self.active_ids(now):
            controller = self.controllers[player_id]
            laser = self.target is None or self.target == player_id
            desired = (laser, controller.feedback_id, controller.duration_ms)
            if desired != controller.desired:
                controller.outgoing_seq = (controller.outgoing_seq + 1) & 0xFFFFFFFF
                controller.desired = desired
            packet = dict(v=1, type='command', session=self.session, seq=controller.outgoing_seq,
                          laser=laser, feedback_id=controller.feedback_id, duration_ms=controller.duration_ms)
            try:
                self.connection.sendto(json.dumps(packet, separators=(',', ':')).encode(), controller.endpoint)
                if laser:
                    controller.last_on_sent = now
            except OSError:
                pass


@dataclass
class IdentityScheduler:
    config: dict
    target: int | None = None
    started: float = 0.0
    ready: float | None = None
    next_probe: float = 0.0
    position: int = 0

    def update(self, now, bridge, vision, enabled):
        ids = bridge.active_ids(now)
        if not enabled or not ids:
            self.target = None
            self.ready = None
            bridge.set_target(0, now)
            vision.set_identity(None, float('inf'))
            return
        camera = self.config['camera']
        if self.target is None and now >= self.next_probe:
            self.target = ids[self.position % len(ids)]
            self.position += 1
            self.started = now
            self.ready = None
            bridge.set_target(self.target, now)
            vision.set_identity(self.target, float('inf'))
        if self.target is not None:
            acknowledged = self.target in ids and bridge.acknowledged(now)
            if self.ready is not None and not acknowledged:
                self.ready = None
                vision.set_identity(self.target, float('inf'))
            if self.ready is None and acknowledged:
                self.ready = now + camera['identity_settle']
                vision.set_identity(self.target, self.ready)
            finished = self.ready is not None and now >= self.ready + camera['identity_window']
            if finished or now - self.started > 1.0 or self.target not in ids:
                self.target = None
                self.ready = None
                bridge.set_target(None, now)
                vision.set_identity(None, float('inf'))
                self.next_probe = max(now + 0.05, self.started + camera['identity_interval'] / len(ids))
        else:
            bridge.set_target(None, now)
            if bridge.acknowledged(now):
                if self.ready is None:
                    self.ready = now + camera['identity_settle']
                    self.next_probe = max(self.next_probe, self.ready + 0.15)
                    vision.set_identity(None, self.ready)
            else:
                self.ready = None
                vision.set_identity(None, float('inf'))
