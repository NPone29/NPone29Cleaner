# -*- mode: python ; coding: utf-8 -*-
# Configuration PyInstaller : produit dist\NPone29Cleaner.exe (un seul fichier, sans console).
# Utilisation :  pyinstaller --noconfirm --clean NPone29Cleaner.spec   (ou lancez build.bat)

from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("customtkinter") + [("assets/icon.ico", "assets")]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=["win32timezone", "truststore"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "_pytest", "unittest", "pydoc", "test"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="NPone29Cleaner",
    console=False,               # application fenêtrée
    icon="assets/icon.ico",
    version="version_info.txt",  # infos visibles dans Propriétés > Détails
    # Pas de demande administrateur au lancement : l'application s'ouvre normalement (mode limité) et propose
    # « Relancer en administrateur » ; clic droit > « Exécuter en tant qu'administrateur » fonctionne aussi.
    uac_admin=False,
    upx=False,                   # UPX augmente les faux positifs des antivirus
    disable_windowed_traceback=False,
    runtime_tmpdir=None,
)
