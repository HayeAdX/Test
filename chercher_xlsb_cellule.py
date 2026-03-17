from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Dict, Iterable

from pyxlsb import open_workbook


def numero_vers_colonne_excel(numero: int) -> str:
    """Convertit 1 -> A, 27 -> AA, etc."""
    resultat = ""
    while numero > 0:
        numero, reste = divmod(numero - 1, 26)
        resultat = chr(65 + reste) + resultat
    return resultat


def normaliser_texte(valeur: object) -> str:
    if valeur is None:
        return ""
    return str(valeur).strip()



def correspond(
    texte_cellule: str,
    mot_recherche: str,
    exact: bool = False,
    ignore_case: bool = True,
) -> bool:
    if ignore_case:
        texte_cellule = texte_cellule.casefold()
        mot_recherche = mot_recherche.casefold()

    if exact:
        return texte_cellule == mot_recherche

    return mot_recherche in texte_cellule



def iterer_fichiers_xlsb(racine: Path) -> Iterable[Path]:
    for fichier in racine.rglob("*.xlsb"):
        if not fichier.name.startswith("~$"):
            yield fichier



def echapper_formule_excel(texte: str) -> str:
    """Double les guillemets pour une formule Excel."""
    return texte.replace('"', '""')



def echapper_reference_excel(texte: str) -> str:
    """Double les apostrophes pour une référence Excel externe."""
    return texte.replace("'", "''")



def creer_reference_excel_vers_cellule(fichier: Path, feuille: str, cellule: str) -> str:
    """
    Construit une référence Excel externe vers une cellule précise.

    Exemple visé :
    'C:\\Dossier\\SousDossier\\[MonFichier.xlsb]Feuille 1'!B12
    """
    fichier_absolu = fichier.resolve()
    base = str(fichier_absolu.parent / f"[{fichier_absolu.name}]{feuille}")
    base = echapper_reference_excel(base)
    return f"'{base}'!{cellule}"



def creer_lien_excel_local(fichier: Path) -> str:
    """
    Crée une formule Excel cliquable ouvrant directement le fichier local.
    Exemple : =HYPERLINK("file:///C:/dossier/fichier.xlsb","C:/dossier/fichier.xlsb")
    """
    chemin_absolu = fichier.resolve()
    texte_affiche = echapper_formule_excel(str(chemin_absolu))

    try:
        cible = echapper_formule_excel(chemin_absolu.as_uri())
    except ValueError:
        # Repli rare si as_uri() ne fonctionne pas sur la plateforme/le chemin.
        cible = echapper_formule_excel(str(chemin_absolu))

    return f'=HYPERLINK("{cible}","{texte_affiche}")'



def creer_lien_excel_vers_cellule(fichier: Path, feuille: str, cellule: str) -> str:
    """
    Crée une formule Excel cliquable ouvrant le classeur directement
    sur la feuille et la cellule ciblées.

    Exemple :
    =HYPERLINK("'C:\\Dossier\\[MonFichier.xlsb]Feuille1'!A10","C:\\Dossier\\MonFichier.xlsb")
    """
    chemin_absolu = fichier.resolve()
    texte_affiche = echapper_formule_excel(str(chemin_absolu))
    cible_excel = creer_reference_excel_vers_cellule(fichier, feuille, cellule)
    cible_excel = echapper_formule_excel(cible_excel)
    return f'=HYPERLINK("{cible_excel}","{texte_affiche}")'



def chercher_dans_fichier(
    fichier: Path,
    mot_recherche: str,
    exact: bool = False,
    ignore_case: bool = True,
) -> Iterable[Dict[str, str]]:
    try:
        with open_workbook(str(fichier)) as wb:
            for nom_feuille in wb.sheets:
                try:
                    with wb.get_sheet(nom_feuille) as sheet:
                        for index_ligne, ligne in enumerate(sheet.rows(), start=1):
                            for index_colonne, cell in enumerate(ligne, start=1):
                                valeur = getattr(cell, "v", cell)
                                texte = normaliser_texte(valeur)

                                if not texte:
                                    continue

                                if correspond(texte, mot_recherche, exact=exact, ignore_case=ignore_case):
                                    num_ligne = getattr(cell, "r", None)
                                    num_colonne = getattr(cell, "c", None)

                                    if isinstance(num_ligne, int):
                                        num_ligne += 1
                                    else:
                                        num_ligne = index_ligne

                                    if isinstance(num_colonne, int):
                                        num_colonne += 1
                                    else:
                                        num_colonne = index_colonne

                                    adresse = f"{numero_vers_colonne_excel(num_colonne)}{num_ligne}"
                                    chemin_absolu = str(fichier.resolve())
                                    reference_excel = creer_reference_excel_vers_cellule(
                                        fichier=fichier,
                                        feuille=str(nom_feuille),
                                        cellule=adresse,
                                    )

                                    yield {
                                        "fichier": creer_lien_excel_vers_cellule(
                                            fichier=fichier,
                                            feuille=str(nom_feuille),
                                            cellule=adresse,
                                        ),
                                        "chemin_fichier": chemin_absolu,
                                        "feuille": str(nom_feuille),
                                        "cellule": adresse,
                                        "reference_excel": reference_excel,
                                        "valeur": texte,
                                        "erreur": "",
                                    }

                except Exception as e:
                    chemin_absolu = str(fichier.resolve())
                    yield {
                        "fichier": creer_lien_excel_local(fichier),
                        "chemin_fichier": chemin_absolu,
                        "feuille": str(nom_feuille),
                        "cellule": "",
                        "reference_excel": "",
                        "valeur": "",
                        "erreur": f"Erreur lecture feuille: {e}",
                    }

    except Exception as e:
        chemin_absolu = str(fichier.resolve())
        yield {
            "fichier": creer_lien_excel_local(fichier),
            "chemin_fichier": chemin_absolu,
            "feuille": "",
            "cellule": "",
            "reference_excel": "",
            "valeur": "",
            "erreur": f"Erreur ouverture fichier: {e}",
        }



def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recherche un mot dans tous les fichiers .xlsb d'un dossier et de ses sous-dossiers."
    )
    parser.add_argument("mot", help="Mot ou texte à rechercher")
    parser.add_argument(
        "racine",
        nargs="?",
        default=".",
        help="Dossier racine à parcourir (par défaut: dossier courant)",
    )
    parser.add_argument(
        "--exact",
        action="store_true",
        help="Fait une correspondance exacte au lieu d'une recherche partielle",
    )
    parser.add_argument(
        "--case-sensitive",
        action="store_true",
        help="Respecte la casse (majuscules/minuscules)",
    )
    parser.add_argument(
        "--output",
        default="resultats_recherche_xlsb.csv",
        help="Fichier CSV de sortie (par défaut: resultats_recherche_xlsb.csv)",
    )

    args = parser.parse_args()

    racine = Path(args.racine).expanduser().resolve()
    if not racine.exists() or not racine.is_dir():
        print(f"Le dossier n'existe pas ou n'est pas un dossier : {racine}", file=sys.stderr)
        return 1

    fichiers = list(iterer_fichiers_xlsb(racine))
    if not fichiers:
        print(f"Aucun fichier .xlsb trouvé dans : {racine}")
        return 0

    chemin_csv = Path(args.output).expanduser().resolve()
    nb_fichiers = 0
    nb_occurrences = 0
    nb_erreurs = 0

    champs = ["fichier", "chemin_fichier", "feuille", "cellule", "reference_excel", "valeur", "erreur"]

    with chemin_csv.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=champs, delimiter=";")
        writer.writeheader()

        for fichier in fichiers:
            nb_fichiers += 1
            for resultat in chercher_dans_fichier(
                fichier=fichier,
                mot_recherche=args.mot,
                exact=args.exact,
                ignore_case=not args.case_sensitive,
            ):
                writer.writerow(resultat)

                if resultat["erreur"]:
                    nb_erreurs += 1
                    print(
                        f"[ERREUR] {resultat['chemin_fichier']} | "
                        f"{resultat['feuille']} | {resultat['erreur']}"
                    )
                else:
                    nb_occurrences += 1
                    print(
                        f"[OK] {resultat['chemin_fichier']} | "
                        f"Feuille: {resultat['feuille']} | "
                        f"Cellule: {resultat['cellule']} | "
                        f"Valeur: {resultat['valeur']}"
                    )

    print("\n--- Résumé ---")
    print(f"Fichiers analysés : {nb_fichiers}")
    print(f"Occurrences trouvées : {nb_occurrences}")
    print(f"Erreurs : {nb_erreurs}")
    print(f"CSV généré : {chemin_csv}")
    print("Dans Excel, la colonne 'fichier' sera cliquable pour ouvrir directement le fichier sur la cellule trouvée.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
