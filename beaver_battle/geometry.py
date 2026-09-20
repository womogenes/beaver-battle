"""Latest-only geometry preparation; the render/physics thread never waits for a scan."""
from threading import Event, Lock, Thread


class GeometryWorker:
    def __init__(self, config):
        self.config = config
        self.lock = Lock()
        self.ready = Event()
        self.stopping = Event()
        self.generation = 0
        self.pending = None
        self.result = None
        self.last = None
        self.error = ''
        self.thread = Thread(target=self.run, name='board-geometry', daemon=True)
        self.thread.start()

    def reset(self):
        with self.lock:
            self.generation += 1
            self.pending = self.result = self.last = None
            self.error = ''

    def update(self, game, walls):
        with self.lock:
            if walls is not self.last:
                self.last = walls
                self.pending = (self.generation, walls)
                self.ready.set()
            result, self.result = self.result, None
            error = self.error
        if error:
            game.error = f'Board geometry unavailable: {error}'
            game.blocked = True
        if result is None:
            return False
        for name, value in result.items():
            setattr(game, name, value)
        game.shape_art = game.ink_art = None
        game.wall_masks.clear()
        return True

    def run(self):
        from beaver_battle.game import Game
        generation = -1
        builder = None
        while not self.stopping.is_set():
            self.ready.wait(.2)
            self.ready.clear()
            with self.lock:
                job, self.pending = self.pending, None
            if job is None:
                continue
            version, walls = job
            if generation != version:
                builder = Game(self.config)
                builder.width = int(builder.setting('width', 1280))
                builder.height = int(builder.setting('height', 720))
                builder.scale = min(builder.width / 1280, builder.height / 720)
                generation = version
            try:
                builder.build_walls(walls)
                result = {name: getattr(builder, name) for name in (
                    'wall_source', 'walls', 'wall_distance', 'shapes', 'sticks', 'loose_ink')}
                with self.lock:
                    if version == self.generation and not self.stopping.is_set():
                        self.result = result
                        self.error = ''
            except Exception as error:
                with self.lock:
                    if version == self.generation:
                        self.error = str(error)

    def stop(self):
        self.stopping.set()
        self.ready.set()
        self.thread.join(timeout=2)
