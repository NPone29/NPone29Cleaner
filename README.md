# NPone29 Cleaner

Application Windows **gratuite** qui réunit le nettoyage du PC, la mise à jour des pilotes
et des logiciels, et les petits outils d'entretien du quotidien.

## Fonctionnalités

| Page | Ce qu'elle fait |
|---|---|
| **Tableau de bord** | Infos système, utilisation CPU/RAM en direct, espace disque, accès à l'entretien guidé. |
| **Entretien guidé** | Assistant en 5 étapes (fichiers inutiles → nettoyage → pilotes → logiciels → démarrage) qui demande l'accord de l'utilisateur avant chaque action et termine par un bilan. |
| **Mon PC** | Carte interactive du matériel (processeur, mémoire et emplacements libres, carte graphique, disques, réseau, audio, écran, ventilation, batterie) avec explications simples, caractéristiques, conseils et températures en direct. Bouton « Copier la configuration ». |
| **Nettoyage** | Analyse puis supprime : corbeille, fichiers temporaires, miniatures, rapports de plantage, cache Windows Update, Delivery Optimization, shaders DirectX/NVIDIA/AMD, journaux, caches de Chrome / Edge / Brave / Vivaldi / Opera / Firefox, Discord, Steam, Teams, Spotify, VS Code, pip, npm. Prévient si un navigateur ouvert bloque une partie du nettoyage, puis affiche un bilan (espace libre avant → après). |
| **Pilotes** | **Analyse complète** de chaque périphérique avec trois états honnêtes : **À jour (vérifié)**, **Mise à jour disponible**, **Non vérifiable**. Un pilote n'est jamais affiché « à jour » sans source fiable : Windows Update, nvidia.com (cartes NVIDIA) ou le catalogue officiel du fabricant pour les PC **Dell**, **HP** et **Lenovo**. Chaque ligne indique la source utilisée et la date de la vérification. Bouton **Télécharger** vers la page la plus précise : fichier ou page exacte du pilote quand la source la fournit, sinon page de support pré-remplie (modèle du PC / de la carte mère, ID matériel dans le Catalogue Microsoft). Quand un fabricant n'a pas de service public (Intel, AMD, Realtek, ASUS, MSI, Gigabyte, ASRock, Acer…), l'interface le dit au lieu de deviner. Installation automatique via Windows Update ou de l'installateur NVIDIA officiel (signature vérifiée). Point de restauration automatique, inventaire, périphériques en erreur. |
| **Logiciels** | Liste les logiciels à mettre à jour et les met à jour en silence via **winget**. |
| **Démarrage** | Active/désactive les programmes lancés au démarrage (même mécanisme que le Gestionnaire des tâches, 100 % réversible). |
| **Désinstallation** | Liste des programmes installés, recherche, désinstallation via le désinstalleur officiel. |
| **Gros fichiers** | Trouve les plus gros fichiers d'un dossier ; suppression vers la corbeille. |
| **Outils** | Point de restauration, vider le cache DNS, redémarrer l'Explorateur, SFC, DISM, CHKDSK, réinitialisation réseau, raccourcis Windows. |

Thème sombre / clair / système, réglages mémorisés dans `%APPDATA%\NPone29Cleaner`.

### Aide à l'utilisateur

- **Étapes** affichées en haut des pages Nettoyage, Pilotes et Logiciels (① Analyser → ② Vérifier → ③ … → ④ Terminé).
- **Notifications** quand une tâche longue se termine : bannière dans l'application + son ; si la fenêtre est en
  arrière-plan, notification Windows et clignotement dans la barre des tâches.
- **Bilans** en fin de tâche avec chiffres clés, conseils et « Et maintenant ? » (prochaine action proposée).
- **Accueil** au premier lancement et encadrés d'explication sur chaque page.

### Sécurité

- Le nettoyage ne touche **jamais** aux documents, mots de passe, cookies ou historiques.
- Il ne suit jamais les liens symboliques/jonctions et ignore les fichiers en cours d'utilisation.
- Les fichiers temporaires de moins d'une heure sont conservés (programmes en cours d'installation).
- Les pilotes installés automatiquement viennent de Windows Update ou du site officiel du fabricant
  (NVIDIA : téléchargement HTTPS limité aux domaines `*.nvidia.com` + vérification de la signature numérique
  « NVIDIA Corporation » avant le lancement). Aucun site de pilotes tiers.
- Un point de restauration est créé avant l'installation de pilotes (option cochée par défaut).
- Pilotes Dell, HP et Lenovo : l'application **ouvre** le lien officiel dans le navigateur ; elle ne télécharge
  ni n'exécute elle-même aucun installateur de ces fabricants. Seuls les catalogues (fichiers XML/CAB) sont
  téléchargés, en HTTPS, depuis les domaines officiels : `downloads.dell.com`, `hpia.hpcloud.hp.com`,
  `ftp.hp.com`, `download.lenovo.com`. Toute redirection hors de ces domaines est refusée, et les liens
  proposés sont filtrés par domaine officiel (sauf la recherche Google limitée au site du fabricant).
- Données envoyées : le modèle de la carte graphique (NVIDIA), l'identifiant de plateforme HP (ex. `8DE2`)
  et le type de machine Lenovo (ex. `20XW`) dans l'adresse du catalogue. Le catalogue Dell est téléchargé en
  entier et filtré sur le PC avec l'identifiant de modèle (SKU). **Jamais** de numéro de série ni de service
  tag. L'inventaire « Mon PC » reste sur le PC.
- Les catalogues sont gardés 24 h dans `%LOCALAPPDATA%\NPone29Cleaner\cache\drivers`.

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
    driver_analysis.py     état de chaque périphérique + meilleure action (installer / lien officiel)
    driver_sources/        une classe par fabricant : vérification de version et/ou liens officiels
      base.py                contrat commun (DriverSource), versions, cache, téléchargement HTTPS limité
      nvidia_source.py       nvidia.com
      dell.py, hp.py, lenovo.py   catalogues officiels des PC de marque
      chipmakers.py          Intel, AMD, autres fabricants de puces (liens seuls)
      boards.py              ASUS, MSI, Gigabyte, ASRock, Acer… (pages support, liens seuls)
    vendors.py             identifiants fabricants (VEN/VID) -> nom, site, liens de recherche
    nvidia.py              nvidia-smi (températures) + dernier pilote sur nvidia.com
    hardware.py            inventaire « Mon PC » + conseils
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

## Limites connues

- La vitesse des ventilateurs du processeur et du boîtier n'est pas accessible depuis Windows sur la plupart
  des PC (seuls ceux de la carte graphique NVIDIA le sont, via `nvidia-smi`) : la page « Mon PC » oriente
  vers l'outil du fabricant de la carte mère.
- La vérification « chez le fabricant » est automatique pour NVIDIA et pour les PC Dell, HP et Lenovo. Pour
  Intel, AMD, Realtek, ASUS, MSI, Gigabyte, ASRock, Acer, etc., aucun service public fiable n'existe : ces
  pilotes sont « Non vérifiables » et l'application ouvre la bonne page officielle (ou une recherche limitée
  au site). Sur un PC assemblé, la plupart des pilotes seront donc « Non vérifiables » : c'est voulu.
- Dell, HP, Lenovo : seuls les pilotes présents dans le catalogue du modèle sont vérifiés (Dell : uniquement
  les périphériques PCI). Une version que le fabricant n'écrit pas sous forme numérique (ex. « 6.0.9.1 Rev.A »)
  n'est pas comparée : le pilote reste « Non vérifiable ».
- La page Dell d'un pilote refuse les robots (elle s'ouvre normalement dans un navigateur) ; la page MSI
  pré-remplie est déduite du nom de la carte mère et n'a pas pu être testée automatiquement (une recherche
  limitée à msi.com est proposée en secours). HP n'a pas de page de support pré-remplissable sans numéro de
  produit : le fichier du SoftPaq et ses notes de version sont proposés à la place.

## Pistes pour la suite

- Vérification en ligne pour d'autres fabricants (AMD, Intel) si un service officiel apparaît
- Recherche de fichiers en double
- Nettoyage planifié (tâche Windows)
- Installeur (Inno Setup) et signature de l'exe
- Mise à jour automatique de l'application elle-même
