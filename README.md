# NPone29 Cleaner

Application Windows **gratuite** qui réunit le nettoyage du PC, la mise à jour des pilotes
et des logiciels, et les petits outils d'entretien du quotidien.

## Fonctionnalités

| Page | Ce qu'elle fait |
|---|---|
| **Tableau de bord** | Infos système (Windows, CPU, RAM, modèle), utilisation CPU/RAM en direct, espace disque, raccourcis. |
| **Nettoyage** | Analyse puis supprime : corbeille, fichiers temporaires, miniatures, rapports de plantage, cache Windows Update, Delivery Optimization, shaders DirectX/NVIDIA/AMD, journaux, caches de Chrome / Edge / Brave / Vivaldi / Opera / Firefox, Discord, Steam, Teams, Spotify, VS Code, pip, npm. |
| **Pilotes** | Recherche **et installe** les pilotes via Windows Update (pilotes officiels signés), avec point de restauration automatique. Inventaire des pilotes installés, liste des périphériques en erreur, liens vers les outils officiels des fabricants détectés (NVIDIA, AMD, Intel, Dell, HP…). |
| **Logiciels** | Liste les logiciels à mettre à jour et les met à jour en silence via **winget**. |
| **Démarrage** | Active/désactive les programmes lancés au démarrage (même mécanisme que le Gestionnaire des tâches, 100 % réversible). |
| **Désinstallation** | Liste des programmes installés, recherche, désinstallation via le désinstalleur officiel. |
| **Gros fichiers** | Trouve les plus gros fichiers d'un dossier ; suppression vers la corbeille. |
| **Outils** | Point de restauration, vider le cache DNS, redémarrer l'Explorateur, SFC, DISM, CHKDSK, réinitialisation réseau, raccourcis Windows. |

Thème sombre / clair / système, réglages mémorisés dans `%APPDATA%\NPone29Cleaner`.

### Sécurité

- Le nettoyage ne touche **jamais** aux documents, mots de passe, cookies ou historiques.
- Il ne suit jamais les liens symboliques/jonctions et ignore les fichiers en cours d'utilisation.
- Les fichiers temporaires de moins d'une heure sont conservés (programmes en cours d'installation).
- Les pilotes viennent uniquement de Windows Update (aucun site tiers).
- Un point de restauration est créé avant l'installation de pilotes (option cochée par défaut).

## Lancer en développement

Prérequis : Windows 10/11 et Python 3.10+.

```bat
run.bat
```

ou manuellement :

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python main.py
```

Sans droits administrateur, l'application fonctionne en **mode limité** (un bandeau propose de la
relancer en administrateur). Option `--debug` pour un journal détaillé.

## Créer le .exe

Double-cliquez sur **`build.bat`** (ou `powershell -ExecutionPolicy Bypass -File build.ps1`).
Le script installe les dépendances, lance les tests puis produit :

```
dist\NPone29Cleaner.exe   (≈ 15 Mo, un seul fichier, aucune installation nécessaire)
```

Ce fichier se copie et se lance sur n'importe quel PC Windows 10/11 64 bits, sans Python.
Il demande les droits administrateur au démarrage (nécessaires pour les pilotes et les fichiers système).

> **Antivirus / SmartScreen** : les .exe créés avec PyInstaller et non signés sont parfois signalés
> à tort. Pour une distribution publique, il faudra signer l'exécutable (certificat de signature de code)
> ou le soumettre à Microsoft comme faux positif.

Vérification automatique de l'exe : `NPone29Cleaner.exe --selftest` ouvre chaque page puis se ferme.

## Tests

```bat
.venv\Scripts\python -m pytest
```

## Structure du projet

```
main.py                  Point d'entrée
app/
  config.py              Nom, version
  core/                  Logique métier (aucune interface) :
    cleaner.py             cibles de nettoyage, analyse, suppression
    drivers.py             WMI + API Windows Update (recherche/installation)
    software.py            winget
    startup.py             programmes au démarrage
    uninstaller.py         programmes installés
    bigfiles.py            recherche des gros fichiers
    maintenance.py         outils (DNS, SFC, DISM…)
    sysinfo.py             infos système
  ui/                    Interface CustomTkinter
    app.py                 fenêtre + navigation
    pages/                 une page par fonctionnalité
    widgets.py             tableau triable, boutons, cartes…
    tasks.py               exécution en arrière-plan sans figer l'interface
  utils/                 Réglages, journaux, API Windows (ctypes), commandes
tests/                   Tests pytest
tools/make_icon.py       Génère assets/icon.ico
NPone29Cleaner.spec      Configuration PyInstaller
```

Journal de l'application : `%LOCALAPPDATA%\NPone29Cleaner\logs\app.log` (bouton dans la page Outils).

## Ajouter un emplacement à nettoyer

Ajoutez une entrée `CleanTarget(...)` dans `build_targets()` (`app/core/cleaner.py`) :
elle apparaît automatiquement dans l'interface.

## Pistes pour la suite

- Recherche de fichiers en double
- Nettoyage planifié (tâche Windows)
- Installeur (Inno Setup) et signature de l'exe
- Mise à jour automatique de l'application elle-même
