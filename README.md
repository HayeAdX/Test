# README

Script Python pour rechercher un texte dans tous les fichiers `.xlsb` d'un dossier et de ses sous-dossiers, sur toutes les feuilles.

## Prérequis
- Python 3
- Windows + Excel installés pour la recherche dans les formules
- `pywin32`
- `pyxlsb` (fallback sans Excel)

## Installation
```bash
python -m pip install pywin32 pyxlsb
```

## Usage
```bash
python chercher_xlsb.py "ma_fonction" "C:\MesDossiers"
```

## Options utiles
- `--search-in formulas` : cherche uniquement dans les vraies formules Excel
- `--search-in values` : cherche uniquement dans les valeurs
- `--search-in both` : cherche dans les deux
- `--engine excel` : force le moteur Excel
- `--excel-lang fr` : génère un CSV compatible Excel FR

Exemple :
```bash
python chercher_xlsb.py "ma_fonction" "C:\MesDossiers" --engine excel --search-in formulas
```

## Résultat
Le script génère un fichier `resultats_recherche_xlsb.csv`.
La colonne `fichier` contient un lien cliquable pour ouvrir le classeur directement sur la feuille et la cellule trouvées.

## Notes
- Le mode `excel` est le plus fiable pour trouver des formules comme `=ma_fonction()`.
- Le mode `pyxlsb` ne lit pas le texte complet des formules, seulement les valeurs calculées.
