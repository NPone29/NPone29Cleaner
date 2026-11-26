"""Une commande bloquée (ex. installateur) doit toujours pouvoir être interrompue."""

import sys
import threading
import time

import pytest

from app.utils.system import RC_CANCELLED, RC_TIMEOUT, run_cancellable

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows uniquement")

LONG = ["cmd", "/c", "ping -n 30 127.0.0.1"]   # cmd lance un processus enfant, comme winget


def test_normal_output():
    result = run_cancellable(["cmd", "/c", "echo bonjour"], timeout=10)
    assert result.ok
    assert result.stdout.strip() == "bonjour"


def test_cancel_stops_process_tree():
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    start = time.monotonic()
    result = run_cancellable(LONG, cancel=cancel)
    assert result.returncode == RC_CANCELLED
    assert time.monotonic() - start < 15


def test_timeout():
    result = run_cancellable(LONG, timeout=0.5)
    assert result.returncode == RC_TIMEOUT


def test_missing_program():
    assert run_cancellable(["programme-inexistant-npone29.exe"]).returncode == -1
