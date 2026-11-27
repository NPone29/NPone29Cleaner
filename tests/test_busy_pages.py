"""Détection des pages occupées (bloque la mise à jour de l'application)."""

import tkinter

from app.ui.app import page_is_busy


class TkPage(tkinter.Misc):
    """Page sans attribut `busy` à elle : hérite de la méthode Tk `busy` (Python 3.13+)."""


def make_page(**flags):
    page = TkPage.__new__(TkPage)     # pas besoin de fenêtre Tk pour lire les attributs
    for name, value in flags.items():
        setattr(page, name, value)
    return page


def test_page_without_flag_is_not_busy():
    # Régression 1.3.0 : la méthode `busy` de tkinter faisait passer le Tableau de bord pour occupé.
    assert not page_is_busy(make_page())


def test_flags_set_to_false_are_not_busy():
    assert not page_is_busy(make_page(busy=False, running=False, busy_pending=False))


def test_each_flag_marks_the_page_busy():
    assert page_is_busy(make_page(busy=True))
    assert page_is_busy(make_page(running=True))
    assert page_is_busy(make_page(busy=False, busy_pending=True))
