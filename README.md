# NPone29 Cleaner

Application Windows **gratuite** qui réunit le nettoyage du PC, la mise à jour des pilotes
et des logiciels, et les petits outils d'entretien du quotidien.

## Fonctionnalités

| Page | Ce qu'elle fait |
|---|---|
| **Tableau de bord** | Infos système, utilisation CPU/RAM en direct, espace disque, accès à l'entretien guidé. |
| **Entretien guidé** | Assistant en 5 étapes (fichiers inutiles → nettoyage → pilotes → logiciels → démarrage) qui demande l'accord de l'utilisateur avant chaque action et termine par un bilan. |
| **Mon PC** | Carte interactive du matériel (processeur, mémoire et emplacements libres, carte graphique, disques, réseau, audio, écran, ventilation, batterie) avec explications simples, caractéristiques, conseils et températures en direct. Bouton « Copier la configuration ». |
| **Sauvegarde** | Copie les dossiers choisis (Documents, Bureau, Images… ou tout autre dossier) sur un disque externe, une clé USB ou un dossier réseau. Sauvegarde **complète**, **différentielle** ou **incrémentale**, au choix. Les fichiers sont copiés **en clair** et restent lisibles directement depuis le disque, même sans l'application. Onglet Restaurer : restauration exacte à la date choisie (dans un dossier ou à l'emplacement d'origine), recherche d'un fichier, ouverture directe, vérification, suppression des anciennes sauvegardes. Prévient si la destination est sur le même disque physique que les données. **Sauvegarde automatique** en arrière-plan, même application fermée : toutes les heures, toutes les 2, 4, 6 ou 12 heures, chaque jour ou chaque semaine, avec le type choisi (et, en option, une complète toutes les N semaines). Si le PC est éteint ou le disque débranché, elle se fait dès que possible ; notification Windows à la fin, rappel si le disque n'a pas été branché depuis 7 jours. |
| **Nettoyage** | Analyse puis supprime : corbeille, fichiers temporaires, miniatures, rapports de plantage, cache Windows Update, Delivery Optimization, shaders DirectX/NVIDIA/AMD, journaux, caches de Chrome / Edge / Brave / Vivaldi / Opera / Firefox, Discord, Steam, Teams, Spotify, VS Code, pip, npm. Prévient si un navigateur ouvert bloque une partie du nettoyage, puis affiche un bilan (espace libre avant → après). |
| **Pilotes** | **Analyse complète** de chaque périphérique avec trois états honnêtes : **À jour (vérifié)**, **Mise à jour disponible**, **Non vérifiable**. Un pilote n'est jamais affiché « à jour » sans source fiable : Windows Update, le **Catalogue Microsoft Update** (pilotes Intel, Realtek, Killer, Acer… publiés pour l'identifiant exact du matériel, classés selon les règles de Windows), la **liste officielle d'Intel** (celle de son outil Intel Driver & Support Assistant : cartes graphiques Intel / Arc, Wi-Fi, Bluetooth, Ethernet, Killer), nvidia.com (cartes NVIDIA) ou le catalogue officiel du fabricant pour les PC **Dell**, **HP** et **Lenovo**. Chaque ligne indique la source utilisée et la date de la vérification. Bouton **Télécharger** vers la page la plus précise : fichier ou page exacte du pilote quand la source la fournit, sinon page de support pré-remplie (modèle du PC / de la carte mère, ID matériel dans le Catalogue Microsoft). Quand aucune source ne connaît un pilote, l'interface le dit au lieu de deviner. Installation automatique via Windows Update, depuis le Catalogue Microsoft (empreinte SHA-256 et signature Microsoft vérifiées, puis `pnputil`) ou des installateurs officiels NVIDIA et Intel (empreinte et signature vérifiées, lancés l'un après l'autre). **La liste s'actualise toute seule** après chaque installation (et à la fermeture de chaque installateur). Point de restauration automatique, inventaire, périphériques en erreur. |
| **Logiciels** | Liste les logiciels à mettre à jour et les met à jour en silence via **winget**, avec un **suivi en direct** : étape en cours, téléchargement (Mo reçus, %, débit), activité de l'installateur, temps écoulé, alerte s'il ne se passe plus rien depuis 1 min 30 et bouton pour afficher une fenêtre d'installateur restée en arrière-plan. |
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
- Intel : la liste publique de l'outil Intel Driver & Support Assistant (`dsadata.intel.com/data/en`, archive
  ZIP) est téléchargée en entier, puis comparée sur le PC ; **seul** son fichier `software-configurations.json`
  est lu (les bibliothèques de l'outil Intel qu'elle contient ne sont ni extraites ni exécutées). Un pilote est
  proposé s'il déclare l'identifiant du périphérique (jokers d'Intel compris), s'il est prévu pour cette version
  de Windows, n'est ni bêta ni réservé à une carte mère ou un produit Intel précis, et si sa version est plus
  récente. Installateur téléchargé en HTTPS depuis `downloadmirror.intel.com` uniquement, empreinte SHA-1
  comparée à celle publiée par Intel, signature « Intel Corporation » vérifiée avant le lancement. Si Windows
  garde malgré tout le pilote du fabricant du PC après une installation réussie, la même version n'est plus
  reproposée.
- Catalogue Microsoft Update : recherche par identifiant de puce sur `www.catalog.update.microsoft.com`,
  fichier téléchargé en HTTPS depuis `*.download.windowsupdate.com`, empreinte SHA-256 comparée à celle
  annoncée par Microsoft et signature « Microsoft Corporation » vérifiée avant l'installation (`pnputil`).
  Seuls sont proposés les pilotes que **Windows choisirait lui-même** : le pilote doit déclarer un identifiant
  du périphérique au moins aussi précis que celui du pilote installé (lu dans le registre), être plus récent
  (date, puis version), venir du même fabricant et être prévu pour cette version de Windows et ce processeur.
  Une version générique plus récente mais moins précise (ex. pilote Intel générique alors qu'Acer fournit le
  sien) est seulement signalée (« Version générique dispo. »). Les composants Intel Smart Sound ne sont jamais
  installés seuls (ils vont avec le pilote audio). Après l'installation, l'application relit la version
  réellement utilisée ; si Windows a gardé l'ancien pilote, il n'est plus reproposé.
- Un point de restauration est créé avant l'installation de pilotes (option cochée par défaut).
- Pilotes Dell, HP et Lenovo : l'application **ouvre** le lien officiel dans le navigateur ; elle ne télécharge
  ni n'exécute elle-même aucun installateur de ces fabricants. Seuls les catalogues (fichiers XML/CAB) sont
  téléchargés, en HTTPS, depuis les domaines officiels : `downloads.dell.com`, `hpia.hpcloud.hp.com`,
  `ftp.hp.com`, `download.lenovo.com`. Toute redirection hors de ces domaines est refusée, et les liens
  proposés sont filtrés par domaine officiel (sauf la recherche Google limitée au site du fabricant).
- Données envoyées : l'identifiant des puces (ex. `PCI\VEN_8086&DEV_06F0&SUBSYS_00748086`, Catalogue
  Microsoft), le modèle de la carte graphique (NVIDIA), l'identifiant de plateforme HP (ex. `8DE2`)
  et le type de machine Lenovo (ex. `20XW`) dans l'adresse du catalogue. Le catalogue Dell est téléchargé en
  entier et filtré sur le PC avec l'identifiant de modèle (SKU). **Jamais** de numéro de série ni de service
  tag. L'inventaire « Mon PC » reste sur le PC.
- Les catalogues sont gardés 24 h (liste Intel comprise) dans `%LOCALAPPDATA%\NPone29Cleaner\cache\drivers` (Catalogue Microsoft :
  recherches 12 h, fiches des pilotes 60 jours).
- PC Dell : la version d'un paquet Dell n'est souvent pas celle du pilote qu'il installe (paquet Intel Ethernet
  20.0.3.28 → pilote 12.19.2.66). L'application lit donc, sur le PC uniquement, les journaux des paquets Dell
  déjà installés (`C:\ProgramData\Dell\UpdatePackage\Log`) : un paquet installé avec succès n'est plus redemandé.
  Si les numérotations diffèrent et qu'aucun journal n'existe, le pilote est « Non vérifiable » plutôt que
  faussement « à mettre à jour ».

### Sauvegarde : organisation sur le disque

```
<disque>\NPone29 Sauvegardes\<NOM-DU-PC>\
    LISEZMOI.txt                      comment restaurer, avec ou sans l'application
    NPone29Cleaner.exe                copie de l'application (version .exe uniquement)
    2026-11-25_14h30_Complete\
        Documents\...  Images\...     les fichiers, tels quels
        _sauvegarde.json              description (écrite en dernier = sauvegarde terminée)
        _inventaire.json.gz           liste de TOUS les fichiers à cette date + où se trouve leur copie
        RESTAURER.bat                 restauration de secours sans l'application (robocopy)
    2026-11-26_09h00_Incrementale\    uniquement les fichiers nouveaux ou modifiés
```

- Un fichier est considéré comme modifié si sa taille ou sa date de modification a changé.
- Chaque inventaire décrit l'état complet des dossiers : une restauration à une date donnée ne fait pas
  réapparaître les fichiers supprimés entre-temps.
- Une sauvegarde interrompue (arrêt, disque plein, débranché) n'est jamais utilisée comme base ; elle est
  signalée et peut être supprimée.
- Fichiers ignorés : fichiers temporaires (`~$*`, `*.tmp`, `Thumbs.db`, `desktop.ini`…), liens et jonctions,
  fichiers OneDrive « disponibles en ligne uniquement » (déjà dans le cloud).
- Les fichiers ouverts en écriture par un programme (ex. fichier `.pst` d'Outlook ouvert) ne peuvent pas être
  copiés : ils sont listés à la fin et l'ancienne copie éventuelle est conservée.

### Sauvegarde automatique

- Activée depuis la page Sauvegarde (étape 4), qui crée la tâche « NPone29 Cleaner - Sauvegarde » du
  Planificateur de tâches Windows (application lancée **en administrateur**, une seule fois). Désactiver la
  sauvegarde automatique supprime la tâche.
- La tâche lance `NPone29Cleaner.exe --backup` à l'ouverture de session puis **toutes les heures**, sans
  fenêtre. Ce mode relit les réglages à chaque fois (dossiers, destination, type, conservation : rien à
  recréer quand ils changent) et ne fait rien si la sauvegarde n'est pas encore due, si le disque est absent
  ou si une autre sauvegarde est en cours (verrou partagé avec la sauvegarde manuelle).
- Une heure manquée (PC éteint) est rattrapée au démarrage ; aucune restriction sur batterie. La tâche
  s'exécute dans la session de l'utilisateur avec les droits les plus élevés (le .exe les demande).
- Résultat dans `%APPDATA%\NPone29Cleaner\backup_status.json` (dernier essai, dernier succès, « disque absent
  depuis »…), affiché dans la page. Notification Windows : succès, fichiers non copiés, échec (une même erreur
  au plus une fois par jour), disque absent depuis 7 jours (au plus un rappel par jour). Code de sortie 1 en
  cas d'échec.
- Lecteur réseau : une tâche avec droits administrateur ne voit pas les lettres de lecteurs réseau (`Z:`) ;
  l'application propose de les remplacer par le chemin réseau complet (`\\NAS\partage`).
- Si l'application est déplacée, la page le détecte et propose de réparer la tâche.
- Test manuel : `NPone29Cleaner.exe --backup --force` (ou `python main.py --backup --force`) sauvegarde tout de
  suite, même si la sauvegarde n'est pas due.

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
      microsoft_catalog.py   Catalogue Microsoft Update (tous fabricants, règles de classement de Windows)
      dell.py, hp.py, lenovo.py   catalogues officiels des PC de marque
      intel_dsa.py           Intel (liste publique d'Intel Driver & Support Assistant)
      chipmakers.py          AMD, autres fabricants de puces (liens seuls)
      boards.py              ASUS, MSI, Gigabyte, ASRock, Acer… (pages support, liens seuls)
    vendors.py             identifiants fabricants (VEN/VID) -> nom, site, liens de recherche
    nvidia.py              nvidia-smi (températures) + dernier pilote sur nvidia.com
    hardware.py            inventaire « Mon PC » + conseils
    software.py            winget (recherche, mise à jour, suivi en direct)
    startup.py             programmes au démarrage
    uninstaller.py         programmes installés
    bigfiles.py            recherche des gros fichiers
    backup.py              sauvegarde complète/différentielle/incrémentale, restauration, vérification
    backup_schedule.py     sauvegarde automatique : échéance, tâche planifiée, verrou, mode --backup
    packages.py            installateurs officiels (NVIDIA, Intel) : téléchargement, empreinte, signature
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
- La vérification « chez le fabricant » est automatique pour NVIDIA, Intel (cartes graphiques, Wi-Fi,
  Bluetooth, Ethernet : Intel ne publie dans la liste de son outil ni le chipset, ni l'audio, ni le stockage)
  et pour les PC Dell, HP et Lenovo. Pour AMD, Realtek, Acer, ASUS, MSI, etc., c'est le Catalogue Microsoft
  Update qui sert de référence : il contient les pilotes publiés pour Windows, mais pas toujours les toutes
  dernières versions génériques du site du fabricant de la puce (AMD…). AMD ne publie aucune liste exploitable ;
  ASUS, MSI, Gigabyte et ASRock n'ont que des listes par modèle, sans identifiant matériel, donc impossibles à
  relier de façon fiable à un périphérique. Un pilote absent de toutes les sources reste « Non vérifiable » et
  l'application ouvre la bonne page officielle.
- La liste d'Intel n'est pas une API documentée : c'est celle que télécharge l'outil officiel d'Intel. Si Intel
  en change le format, la source Intel est simplement ignorée (les autres sources continuent).
- Pilotes Intel génériques : sur certains PC portables, le fabricant du PC recommande sa propre version du
  pilote graphique ; l'application le signale.
- Le catalogue n'a pas d'API officielle : l'application lit ses pages web (première page de résultats, 25
  lignes par recherche). Pour une puce très répandue (ex. Intel UHD), la recherche sans SUBSYS est dominée par
  les pilotes d'autres marques de PC : la recherche avec l'identifiant exact du PC est donc faite en premier.
- Dell, HP, Lenovo : seuls les pilotes présents dans le catalogue du modèle sont vérifiés (Dell : uniquement
  les périphériques PCI). Une version que le fabricant n'écrit pas sous forme numérique (ex. « 6.0.9.1 Rev.A »)
  n'est pas comparée : le pilote reste « Non vérifiable ».
- La page Dell d'un pilote refuse les robots (elle s'ouvre normalement dans un navigateur) ; la page MSI
  pré-remplie est déduite du nom de la carte mère et n'a pas pu être testée automatiquement (une recherche
  limitée à msi.com est proposée en secours). HP n'a pas de page de support pré-remplissable sans numéro de
  produit : le fichier du SoftPaq et ses notes de version sont proposés à la place.

## Pistes pour la suite

- Vérification en ligne pour d'autres fabricants (AMD) si un service officiel apparaît
- Recherche de fichiers en double
- Nettoyage planifié (tâche Windows)
- État de la sauvegarde automatique sur le Tableau de bord
- Sauvegarde des fichiers ouverts (clichés instantanés VSS, droits administrateur)
- Installeur (Inno Setup) et signature de l'exe
- Mise à jour automatique de l'application elle-même
