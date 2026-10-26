"""llamamon's small, dependency-free entry point: ``python llamamon/llamamon.py``.

Run it from a source checkout — ``python llamamon/llamamon.py status`` — or use
``python -m llamamon``. It puts the folder that holds this package on the import
path (so a plain script run finds ``llamamon.cli``), then hands over to the
command line. The optional terminal-UI packages (rich, textual) are checked and
imported only by the ``monitor`` command, so ``status`` and ``config`` need
nothing but the standard library.

Settings live in ``~/.config/llamamon/config.toml``.
"""

import os
import sys

if not __package__:                                   # run as a plain script, not -m
    _HOLDING = os.path.dirname(os.path.abspath(__file__))          # the llamamon/ folder
    _PARENT = os.path.dirname(_HOLDING)                            # the folder that holds it
    if _PARENT not in sys.path:
        sys.path.insert(0, _PARENT)


def _main():
    from llamamon.cli import main
    return main()


if __name__ == "__main__":
    raise SystemExit(_main())
