"""Point d'entrée de NPone29 Cleaner.

Lancement en développement :   python main.py
Options :  --debug  (journal détaillé)
"""

from __future__ import annotations

import ctypes
import logging
import sys


def _enable_dpi_awareness() -> None:
    """Affichage net sur les écrans haute résolution."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _set_app_user_model_id() -> None:
    """Permet à Windows d'afficher notre icône (et non celle de Python) dans la barre des tâches."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NPone29.Cleaner")
    except Exception:
        pass


def main() -> int:
    if sys.platform != "win32":
        print("NPone29 Cleaner fonctionne uniquement sous Windows.")
        return 1

    from app.config import APP_NAME, APP_VERSION
    from app.utils.logging_setup import setup_logging

    setup_logging(debug="--debug" in sys.argv)
    log = logging.getLogger("main")
    log.info("Démarrage de %s %s", APP_NAME, APP_VERSION)

    _enable_dpi_awareness()
    _set_app_user_model_id()

    from app.ui.app import MainWindow

    window = MainWindow()
    if "--selftest" in sys.argv:
        # Vérification automatique du .exe : ouvre chaque page puis se ferme.
        from app.ui.app import NAVIGATION

        for delay, (page_id, *_rest) in enumerate(NAVIGATION, start=1):
            window.after(400 * delay, lambda p=page_id: window.show_page(p))
        window.after(400 * (len(NAVIGATION) + 2), window.on_close)
    window.mainloop()
    log.info("Fermeture de l'application")
    return 0


if __name__ == "__main__":
    sys.exit(main())
