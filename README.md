python -m pip install pyxlsb pywin32

Exemple d’utilisation :
python chercher_xlsb.py "mon mot" "C:\MesDossiers"

Recherche exacte :
python chercher_xlsb.py "Paris" "C:\MesDossiers" --exact

Recherche sensible à la casse :
python chercher_xlsb.py "Paris" "C:\MesDossiers" --case-sensitive

Pour chercher uniquement dans les formules :
python chercher_xlsb.py "ma_fonction" "C:\MesDossiers" --search-in formulas
