from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable

from pyxlsb import open_workbook


@dataclass(frozen=True)
class ParametresExcel:
    fonction_hyperlien: str
    separateur_formule: str
    separateur_csv: str


PARAMETRES_EXCEL_PAR_LANGUE = {
    "fr": ParametresExcel(
        fonction_hyperlien="LIEN_HYPERTEXTE",
        separateur_formule=";",
        separateur_csv=";",
    ),
    "en": ParametresExcel(
        fonction_hyperlien="HYPERLINK",
        separateur_formule=",",
        separateur_csv=",",
    ),
}


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


def echapper_chaine_excel(texte: str) -> str:
    """Double les guillemets pour une chaîne Excel."""
    return texte.replace('"', '""')


def echapper_nom_feuille_excel(nom_feuille: str) -> str:
    """Entoure toujours le nom de feuille avec des apostrophes, et échappe les apostrophes internes."""
    return "'" + nom_feuille.replace("'", "''") + "'"


def neutraliser_formule_csv(valeur: str) -> str:
    """
    Empêche Excel d'interpréter accidentellement certaines valeurs comme des formules
    lorsqu'on ouvre le CSV. Le champ hyperlien généré volontairement n'utilise pas
    cette fonction.
    """
    if valeur and valeur[0] in ("=", "+", "-", "@"):  # protection CSV/Excel
        return "'" + valeur
    return valeur


def construire_cible_excel(fichier: Path, feuille: str, cellule: str) -> str:
    chemin = str(fichier.resolve())
    feuille_excel = echapper_nom_feuille_excel(feuille)
    return f"[{chemin}]{feuille_excel}!{cellule}"


def construire_formule_hyperlien_excel(
    fichier: Path,
    fonction_hyperlien: str,
    separateur_formule: str,
    feuille: str | None = None,
    cellule: str | None = None,
) -> str:
    chemin = str(fichier.resolve())

    if feuille and cellule:
        cible = construire_cible_excel(fichier=fichier, feuille=feuille, cellule=cellule)
    else:
        cible = chemin

    cible_excel = echapper_chaine_excel(cible)
    affichage = echapper_chaine_excel(chemin)

    return f'={fonction_hyperlien}("{cible_excel}"{separateur_formule}"{affichage}")'


def preparer_ligne_csv(
    resultat: Dict[str, str],
    fonction_hyperlien: str,
    separateur_formule: str,
) -> Dict[str, str]:
    fichier = Path(resultat["fichier_brut"])
    feuille = resultat.get("feuille", "")
    cellule = resultat.get("cellule", "")

    ligne = {
        "fichier": construire_formule_hyperlien_excel(
            fichier=fichier,
            feuille=feuille or None,
            cellule=cellule or None,
            fonction_hyperlien=fonction_hyperlien,
            separateur_formule=separateur_formule,
        ),
        "fichier_brut": neutraliser_formule_csv(resultat["fichier_brut"]),
        "feuille": neutraliser_formule_csv(resultat.get("feuille", "")),
        "cellule": neutraliser_formule_csv(resultat.get("cellule", "")),
        "valeur": neutraliser_formule_csv(resultat.get("valeur", "")),
        "erreur": neutraliser_formule_csv(resultat.get("erreur", "")),
    }

    return ligne


def chercher_dans_fichier(
    fichier: Path,
    mot_recherche: str,
    exact: bool = False,
    ignore_case: bool = True,
) -> Iterable[Dict[str, str]]:
    fichier_resolu = str(fichier.resolve())

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

                                    yield {
                                        "fichier_brut": fichier_resolu,
                                        "feuille": str(nom_feuille),
                                        "cellule": adresse,
                                        "valeur": texte,
                                        "erreur": "",
                                    }

                except Exception as e:
                    yield {
                        "fichier_brut": fichier_resolu,
                        "feuille": str(nom_feuille),
                        "cellule": "",
                        "valeur": "",
                        "erreur": f"Erreur lecture feuille: {e}",
                    }

    except Exception as e:
        yield {
            "fichier_brut": fichier_resolu,
            "feuille": "",
            "cellule": "",
            "valeur": "",
            "erreur": f"Erreur ouverture fichier: {e}",
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Recherche un mot dans tous les fichiers .xlsb d'un dossier et de ses "
            "sous-dossiers, puis génère un CSV. La colonne 'fichier' contient une "
            "formule Excel cliquable pour ouvrir le classeur, et si possible la bonne cellule."
        )
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
    parser.add_argument(
        "--excel-lang",
        choices=sorted(PARAMETRES_EXCEL_PAR_LANGUE.keys()),
        default="fr",
        help=(
            "Langue/forme des formules Excel à écrire dans le CSV. "
            "fr = LIEN_HYPERTEXTE avec ';', en = HYPERLINK avec ','."
        ),
    )
    parser.add_argument(
        "--csv-delimiter",
        choices=[";", ","],
        default=None,
        help="Force le séparateur du CSV. Par défaut: celui recommandé pour la langue Excel choisie.",
    )
    parser.add_argument(
        "--formula-separator",
        choices=[";", ","],
        default=None,
        help="Force le séparateur d'arguments de la formule Excel.",
    )
    parser.add_argument(
        "--hyperlink-function",
        default=None,
        help="Force le nom de la fonction d'hyperlien Excel (ex: LIEN_HYPERTEXTE ou HYPERLINK).",
    )

    args = parser.parse_args()

    racine = Path(args.racine).expanduser().resolve()
    if not racine.exists() or not racine.is_dir():
        print(f"Le dossier n'existe pas ou n'est pas un dossier : {racine}", file=sys.stderr)
        return 1

    parametres_excel = PARAMETRES_EXCEL_PAR_LANGUE[args.excel_lang]
    fonction_hyperlien = args.hyperlink_function or parametres_excel.fonction_hyperlien
    separateur_formule = args.formula_separator or parametres_excel.separateur_formule
    separateur_csv = args.csv_delimiter or parametres_excel.separateur_csv

    fichiers = list(iterer_fichiers_xlsb(racine))
    if not fichiers:
        print(f"Aucun fichier .xlsb trouvé dans : {racine}")
        return 0

    chemin_csv = Path(args.output).expanduser().resolve()
    nb_fichiers = 0
    nb_occurrences = 0
    nb_erreurs = 0

    champs = ["fichier", "fichier_brut", "feuille", "cellule", "valeur", "erreur"]

    with chemin_csv.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=champs, delimiter=separateur_csv)
        writer.writeheader()

        for fichier in fichiers:
            nb_fichiers += 1
            for resultat in chercher_dans_fichier(
                fichier=fichier,
                mot_recherche=args.mot,
                exact=args.exact,
                ignore_case=not args.case_sensitive,
            ):
                ligne_csv = preparer_ligne_csv(
                    resultat=resultat,
                    fonction_hyperlien=fonction_hyperlien,
                    separateur_formule=separateur_formule,
                )
                writer.writerow(ligne_csv)

                if resultat["erreur"]:
                    nb_erreurs += 1
                    print(
                        f"[ERREUR] {resultat['fichier_brut']} | "
                        f"{resultat['feuille']} | {resultat['erreur']}"
                    )
                else:
                    nb_occurrences += 1
                    print(
                        f"[OK] {resultat['fichier_brut']} | "
                        f"Feuille: {resultat['feuille']} | "
                        f"Cellule: {resultat['cellule']} | "
                        f"Valeur: {resultat['valeur']}"
                    )

    print("\n--- Résumé ---")
    print(f"Fichiers analysés : {nb_fichiers}")
    print(f"Occurrences trouvées : {nb_occurrences}")
    print(f"Erreurs : {nb_erreurs}")
    print(f"CSV généré : {chemin_csv}")
    print(
        "Ouvrez le CSV dans Excel pour que la colonne 'fichier' soit interprétée comme un lien cliquable."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
