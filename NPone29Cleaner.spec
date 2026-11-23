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
    hiddenimports=["win32timezone"],
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
    uac_admin=True,              # demande les droits admin au lancement (pilotes, fichiers système)
    upx=False,                   # UPX augmente les faux positifs des antivirus
    disable_windowed_traceback=False,
    runtime_tmpdir=None,
)
