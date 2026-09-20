import signal

from beaver_battle.app import main
from beaver_battle.instance import claim

try:
    instance = claim('game')
except RuntimeError as error:
    raise SystemExit(str(error)) from None
def interrupt(signum, frame):
    raise KeyboardInterrupt

signal.signal(signal.SIGINT, interrupt)
signal.signal(signal.SIGTERM, interrupt)
try:
    main()
except KeyboardInterrupt:
    pass
finally:
    if instance is not None:
        instance.close()
