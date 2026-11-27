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


def _use_windows_certificates(log: logging.Logger) -> None:
    """Fait vérifier les certificats HTTPS par Windows plutôt que par Python.

    Python ne connaît que les certificats racines déjà présents sur le PC ; Windows télécharge ceux qui manquent
    (ex. le certificat récent de dsadata.intel.com : « CERTIFICATE_VERIFY_FAILED » sans cela). Même niveau de
    sécurité : c'est la vérification du système, celle du navigateur.
    """
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception as exc:  # paquet absent ou incompatible : vérification standard de Python
        log.warning("Vérification des certificats par Windows indisponible (%s)", exc)


def main() -> int:
    if sys.platform != "win32":
        print("NPone29 Cleaner fonctionne uniquement sous Windows.")
        return 1

    from app.config import APP_NAME, APP_VERSION
    from app.utils.logging_setup import setup_logging

    setup_logging(debug="--debug" in sys.argv)
    log = logging.getLogger("main")
    log.info("Démarrage de %s %s", APP_NAME, APP_VERSION)
    _use_windows_certificates(log)

    if "--backup" in sys.argv:
        # Sauvegarde automatique (tâche planifiée) : aucune fenêtre. --force : sauvegarde même si non due.
        from app.core.backup_schedule import run_scheduled

        outcome = run_scheduled(force="--force" in sys.argv)
        log.info("Sauvegarde automatique : %s %s", outcome.action, outcome.message)
        return outcome.code

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
