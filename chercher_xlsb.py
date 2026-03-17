from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

try:
    import pythoncom  # type: ignore
    import win32com.client  # type: ignore
    from pywintypes import com_error  # type: ignore

    EXCEL_COM_DISPONIBLE = True
except Exception:  # pragma: no cover - dépend de l'environnement Windows utilisateur
    pythoncom = None
    win32com = None
    com_error = Exception
    EXCEL_COM_DISPONIBLE = False

try:
    from pyxlsb import open_workbook

    PYXLSB_DISPONIBLE = True
except Exception:  # pragma: no cover - dépend de l'environnement utilisateur
    open_workbook = None
    PYXLSB_DISPONIBLE = False


# Constantes Excel (pour éviter de dépendre aux constantes COM générées)
XL_FORMULAS = -4123
XL_VALUES = -4163
XL_WHOLE = 1
XL_PART = 2
XL_BY_ROWS = 1
XL_NEXT = 1
XL_CALCULATION_MANUAL = -4135
MSO_AUTOMATION_SECURITY_FORCE_DISABLE = 3


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
    """
    Construit une cible compatible avec HYPERLINK/LIEN_HYPERTEXTE vers une cellule
    d'un autre classeur.

    Exemple de syntaxe visée par Excel :
    [Book1.xlsx]Sheet1!A10
    ou, avec chemin complet :
    'C:\\Dossier\\[Book1.xlsx]Ma Feuille'!A10
    """
    chemin = fichier.resolve()
    dossier = str(chemin.parent)
    nom_fichier = chemin.name

    base = f"{dossier}{os.sep}[{nom_fichier}]{feuille}"
    base = base.replace("'", "''")
    return f"'{base}'!{cellule}"


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

    return {
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
        "correspondance_dans": neutraliser_formule_csv(resultat.get("correspondance_dans", "")),
        "formule": neutraliser_formule_csv(resultat.get("formule", "")),
        "valeur": neutraliser_formule_csv(resultat.get("valeur", "")),
        "erreur": neutraliser_formule_csv(resultat.get("erreur", "")),
    }


def fusionner_correspondance(existant: str, nouveau: str) -> str:
    morceaux = [part for part in existant.split(",") if part]
    if nouveau not in morceaux:
        morceaux.append(nouveau)
    return ",".join(morceaux)


def lire_moteur_recherche(engine: str, search_in: str) -> Tuple[str, List[str]]:
    avertissements: List[str] = []

    if engine == "excel":
        if not EXCEL_COM_DISPONIBLE:
            raise RuntimeError(
                "Le moteur Excel COM n'est pas disponible. Installe pywin32 et lance le script sur Windows avec Excel installé."
            )
        return "excel", avertissements

    if engine == "pyxlsb":
        if not PYXLSB_DISPONIBLE:
            raise RuntimeError(
                "Le moteur pyxlsb n'est pas disponible. Installe pyxlsb pour utiliser ce mode."
            )
        if search_in in {"formulas", "both"}:
            avertissements.append(
                "Le moteur pyxlsb ne lit pas le texte des formules : il ne recherche que dans les valeurs calculées."
            )
        return "pyxlsb", avertissements

    # auto
    if EXCEL_COM_DISPONIBLE:
        return "excel", avertissements

    if PYXLSB_DISPONIBLE:
        if search_in in {"formulas", "both"}:
            avertissements.append(
                "Excel COM n'est pas disponible ; bascule sur pyxlsb. Les recherches dans les formules ne seront pas trouvées, seules les valeurs calculées seront analysées."
            )
        return "pyxlsb", avertissements

    raise RuntimeError(
        "Aucun moteur disponible. Installe pywin32 (Windows + Excel) ou pyxlsb."
    )


def creer_application_excel():
    if not EXCEL_COM_DISPONIBLE:
        raise RuntimeError("win32com / pywin32 indisponible.")

    pythoncom.CoInitialize()
    excel = win32com.client.DispatchEx("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    excel.ScreenUpdating = False
    excel.EnableEvents = False

    try:
        excel.AskToUpdateLinks = False
    except Exception:
        pass

    try:
        excel.AutomationSecurity = MSO_AUTOMATION_SECURITY_FORCE_DISABLE
    except Exception:
        pass

    try:
        excel.Calculation = XL_CALCULATION_MANUAL
    except Exception:
        pass

    return excel


def fermer_application_excel(excel) -> None:
    try:
        if excel is not None:
            excel.Quit()
    finally:
        if pythoncom is not None:
            pythoncom.CoUninitialize()


def obtenir_modes_find(search_in: str) -> List[Tuple[str, int]]:
    if search_in == "formulas":
        return [("formule", XL_FORMULAS)]
    if search_in == "values":
        return [("valeur", XL_VALUES)]
    return [("formule", XL_FORMULAS), ("valeur", XL_VALUES)]


def chercher_dans_fichier_avec_excel(
    excel,
    fichier: Path,
    mot_recherche: str,
    exact: bool = False,
    ignore_case: bool = True,
    search_in: str = "both",
) -> Iterable[Dict[str, str]]:
    fichier_resolu = str(fichier.resolve())
    classeur = None

    try:
        classeur = excel.Workbooks.Open(
            Filename=fichier_resolu,
            UpdateLinks=0,
            ReadOnly=True,
            IgnoreReadOnlyRecommended=True,
            Notify=False,
            AddToMru=False,
        )

        resultats_ordonnes: "OrderedDict[Tuple[str, str], Dict[str, str]]" = OrderedDict()

        for feuille in classeur.Worksheets:
            nom_feuille = str(feuille.Name)
            try:
                plage = feuille.UsedRange
                cellule_depart = plage.Cells(plage.Cells.Count)

                for nom_mode, look_in in obtenir_modes_find(search_in):
                    premiere = plage.Find(
                        What=mot_recherche,
                        After=cellule_depart,
                        LookIn=look_in,
                        LookAt=XL_WHOLE if exact else XL_PART,
                        SearchOrder=XL_BY_ROWS,
                        SearchDirection=XL_NEXT,
                        MatchCase=not ignore_case,
                        SearchFormat=False,
                    )

                    if premiere is None:
                        continue

                    premiere_adresse = premiere.Address(RowAbsolute=False, ColumnAbsolute=False)
                    cellule = premiere

                    while cellule is not None:
                        adresse = cellule.Address(RowAbsolute=False, ColumnAbsolute=False)
                        cle = (nom_feuille, adresse)

                        try:
                            has_formula = bool(cellule.HasFormula)
                        except Exception:
                            has_formula = False

                        try:
                            formule = normaliser_texte(cellule.Formula) if has_formula else ""
                        except Exception:
                            formule = ""

                        try:
                            valeur = normaliser_texte(cellule.Value2)
                        except Exception:
                            valeur = ""

                        if cle not in resultats_ordonnes:
                            resultats_ordonnes[cle] = {
                                "fichier_brut": fichier_resolu,
                                "feuille": nom_feuille,
                                "cellule": adresse,
                                "correspondance_dans": nom_mode,
                                "formule": formule,
                                "valeur": valeur,
                                "erreur": "",
                            }
                        else:
                            resultats_ordonnes[cle]["correspondance_dans"] = fusionner_correspondance(
                                resultats_ordonnes[cle]["correspondance_dans"],
                                nom_mode,
                            )
                            if not resultats_ordonnes[cle]["formule"] and formule:
                                resultats_ordonnes[cle]["formule"] = formule
                            if not resultats_ordonnes[cle]["valeur"] and valeur:
                                resultats_ordonnes[cle]["valeur"] = valeur

                        cellule = plage.FindNext(cellule)
                        if cellule is None:
                            break

                        adresse_suivante = cellule.Address(RowAbsolute=False, ColumnAbsolute=False)
                        if adresse_suivante == premiere_adresse:
                            break

            except Exception as e:
                yield {
                    "fichier_brut": fichier_resolu,
                    "feuille": nom_feuille,
                    "cellule": "",
                    "correspondance_dans": "",
                    "formule": "",
                    "valeur": "",
                    "erreur": f"Erreur lecture feuille: {e}",
                }

        for ligne in resultats_ordonnes.values():
            yield ligne

    except com_error as e:
        yield {
            "fichier_brut": fichier_resolu,
            "feuille": "",
            "cellule": "",
            "correspondance_dans": "",
            "formule": "",
            "valeur": "",
            "erreur": f"Erreur ouverture fichier via Excel: {e}",
        }
    except Exception as e:
        yield {
            "fichier_brut": fichier_resolu,
            "feuille": "",
            "cellule": "",
            "correspondance_dans": "",
            "formule": "",
            "valeur": "",
            "erreur": f"Erreur ouverture fichier via Excel: {e}",
        }
    finally:
        if classeur is not None:
            try:
                classeur.Close(SaveChanges=False)
            except Exception:
                pass


def chercher_dans_fichier_avec_pyxlsb(
    fichier: Path,
    mot_recherche: str,
    exact: bool = False,
    ignore_case: bool = True,
) -> Iterable[Dict[str, str]]:
    """
    Fallback sans Excel : ne lit que les valeurs calculées / texte stocké dans la cellule.
    Il ne peut pas retrouver le texte d'une formule comme =ma_fonction().
    """
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
                                        "correspondance_dans": "valeur",
                                        "formule": "",
                                        "valeur": texte,
                                        "erreur": "",
                                    }

                except Exception as e:
                    yield {
                        "fichier_brut": fichier_resolu,
                        "feuille": str(nom_feuille),
                        "cellule": "",
                        "correspondance_dans": "",
                        "formule": "",
                        "valeur": "",
                        "erreur": f"Erreur lecture feuille: {e}",
                    }

    except Exception as e:
        yield {
            "fichier_brut": fichier_resolu,
            "feuille": "",
            "cellule": "",
            "correspondance_dans": "",
            "formule": "",
            "valeur": "",
            "erreur": f"Erreur ouverture fichier: {e}",
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Recherche un mot dans tous les fichiers .xlsb d'un dossier et de ses "
            "sous-dossiers, puis génère un CSV.\n\n"
            "Mode recommandé sur Windows : moteur Excel COM, qui sait chercher dans "
            "les formules (=ma_fonction()) et dans les valeurs calculées."
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
        "--search-in",
        choices=["both", "formulas", "values"],
        default="both",
        help="Cherche dans les formules, dans les valeurs, ou dans les deux (par défaut: both).",
    )
    parser.add_argument(
        "--engine",
        choices=["auto", "excel", "pyxlsb"],
        default="auto",
        help=(
            "Moteur de lecture. auto = Excel COM si disponible, sinon pyxlsb. "
            "excel = force Excel COM. pyxlsb = force pyxlsb."
        ),
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

    try:
        moteur, avertissements = lire_moteur_recherche(args.engine, args.search_in)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1

    for avertissement in avertissements:
        print(f"[AVERTISSEMENT] {avertissement}")

    fichiers = list(iterer_fichiers_xlsb(racine))
    if not fichiers:
        print(f"Aucun fichier .xlsb trouvé dans : {racine}")
        return 0

    chemin_csv = Path(args.output).expanduser().resolve()
    nb_fichiers = 0
    nb_occurrences = 0
    nb_erreurs = 0

    champs = [
        "fichier",
        "fichier_brut",
        "feuille",
        "cellule",
        "correspondance_dans",
        "formule",
        "valeur",
        "erreur",
    ]

    excel = None
    try:
        if moteur == "excel":
            excel = creer_application_excel()

        with chemin_csv.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=champs, delimiter=separateur_csv)
            writer.writeheader()

            for fichier in fichiers:
                nb_fichiers += 1

                if moteur == "excel":
                    resultats = chercher_dans_fichier_avec_excel(
                        excel=excel,
                        fichier=fichier,
                        mot_recherche=args.mot,
                        exact=args.exact,
                        ignore_case=not args.case_sensitive,
                        search_in=args.search_in,
                    )
                else:
                    resultats = chercher_dans_fichier_avec_pyxlsb(
                        fichier=fichier,
                        mot_recherche=args.mot,
                        exact=args.exact,
                        ignore_case=not args.case_sensitive,
                    )

                for resultat in resultats:
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
                            f"Dans: {resultat['correspondance_dans']} | "
                            f"Formule: {resultat['formule'] or '-'} | "
                            f"Valeur: {resultat['valeur'] or '-'}"
                        )
    finally:
        if excel is not None:
            fermer_application_excel(excel)

    print("\n--- Résumé ---")
    print(f"Moteur utilisé : {moteur}")
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
