"""Exécution des tâches longues en arrière-plan sans figer l'interface.

Tkinter n'est pas thread-safe : les threads de travail ne touchent jamais aux widgets.
Ils déposent des fonctions dans une file que le thread de l'interface exécute
régulièrement (toutes les 40 ms).
"""

from __future__ import annotations

import logging
import queue
import threading
from concurrent.futures import Future
from typing import Any, Callable

log = logging.getLogger(__name__)


class UiDispatcher:
    POLL_MS = 40

    def __init__(self, root) -> None:
        self.root = root
        self._queue: queue.Queue[tuple[Callable, tuple, dict]] = queue.Queue()
        self._poll()

    def _poll(self) -> None:
        try:
            while True:
                fn, args, kwargs = self._queue.get_nowait()
                try:
                    fn(*args, **kwargs)
                except Exception:
                    log.exception("Erreur dans un rappel de l'interface")
        except queue.Empty:
            pass
        self.root.after(self.POLL_MS, self._poll)

    def call(self, fn: Callable, *args: Any, **kwargs: Any) -> None:
        """Planifie `fn` dans le thread de l'interface (utilisable depuis n'importe quel thread)."""
        self._queue.put((fn, args, kwargs))

    def wrap(self, fn: Callable) -> Callable:
        """Renvoie une version de `fn` qui s'exécute toujours dans le thread de l'interface."""
        def wrapper(*args: Any, **kwargs: Any) -> None:
            self.call(fn, *args, **kwargs)
        return wrapper

    def run(
        self,
        work: Callable[..., Any],
        *args: Any,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
        on_finally: Callable[[], None] | None = None,
    ) -> threading.Thread:
        """Exécute `work(*args)` dans un thread puis appelle on_done / on_error côté interface."""

        def runner() -> None:
            try:
                result = work(*args)
            except BaseException as exc:  # noqa: BLE001 - on remonte tout à l'interface
                log.exception("Tâche en arrière-plan échouée")
                if on_error:
                    self.call(on_error, exc)
            else:
                if on_done:
                    self.call(on_done, result)
            finally:
                if on_finally:
                    self.call(on_finally)

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        return thread

    def watch(
        self,
        future: Future,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
        on_finally: Callable[[], None] | None = None,
    ) -> None:
        """Comme `run`, mais pour un Future existant (ex. service Windows Update)."""

        def done(f: Future) -> None:
            exc = f.exception()
            if exc is not None:
                if on_error:
                    self.call(on_error, exc)
            elif on_done:
                self.call(on_done, f.result())
            if on_finally:
                self.call(on_finally)

        future.add_done_callback(done)
