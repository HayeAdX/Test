from __future__ import annotations

import argparse
import csv
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


# Constantes Excel (pour éviter d'utiliser gencache / constants COM générées)
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


@dataclass(frozen=True)
class ResultatRecherche:
    fichier_brut: str
    feuille: str
    cellule: str
    correspondance_dans: str
    formule: str
    valeur: str
    erreur: str


def numero_vers_colonne_excel(numero: int) -> str:
    """Convertit 1 -> A, 27 -> AA, etc."""
    resultat = ""
    while numero > 0:
        numero, reste = divmod(numero - 1, 26)
        resultat = chr(65 + reste) + resultat
    return resultat


def construire_adresse_excel(ligne: int, colonne: int) -> str:
    """Construit une adresse Excel en notation A1 à partir d'indices 1-based."""
    return f"{numero_vers_colonne_excel(colonne)}{ligne}"


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
    return texte.replace('"', '""')


def neutraliser_formule_csv(valeur: str) -> str:
    """
    Empêche Excel d'interpréter accidentellement certaines valeurs comme des formules
    lorsqu'on ouvre le CSV. Le champ hyperlien généré volontairement n'utilise pas
    cette fonction.
    """
    if valeur and valeur[0] in ("=", "+", "-", "@"):
        return "'" + valeur
    return valeur


def echapper_nom_feuille_pour_reference(feuille: str) -> str:
    """
    Retourne une sous-adresse Excel robuste pour un hyperlien.

    Pour les noms de feuilles contenant des espaces, apostrophes ou caractères spéciaux,
    Excel attend généralement des apostrophes autour du nom. Les apostrophes internes
    doivent être doublées.
    """
    feuille = feuille.replace("'", "''")
    return f"'{feuille}'"


def construire_sous_adresse_excel(feuille: str, cellule: str) -> str:
    return f"{echapper_nom_feuille_pour_reference(feuille)}!{cellule}"


def construire_lien_excel(
    fichier: Path,
    feuille: str | None = None,
    cellule: str | None = None,
) -> str:
    """
    Construit la cible du lien pour la fonction HYPERLINK / LIEN_HYPERTEXTE.

    Important: on N'UTILISE PAS ici la syntaxe des références de formule externes
    du type 'C:\\Dossier\\[Classeur.xlsb]Feuille'!A1.

    Cette syntaxe est correcte pour une *formule Excel* qui référence une cellule
    externe, mais pas pour un *hyperlien*. Pour un hyperlien, Excel sépare l'adresse
    du document et son emplacement interne (subaddress). Dans une formule HYPERLINK,
    on combine classiquement les deux avec un fragment '#':

        C:\\Dossier\\Classeur.xlsb#'Ma feuille'!A1

    C'est ce qui évite le message : "Impossible d'ouvrir le fichier spécifié".
    """
    chemin = str(fichier.resolve())

    if feuille and cellule:
        sous_adresse = construire_sous_adresse_excel(feuille=feuille, cellule=cellule)
        return f"{chemin}#{sous_adresse}"

    return chemin


def construire_formule_hyperlien_excel(
    fichier: Path,
    fonction_hyperlien: str,
    separateur_formule: str,
    feuille: str | None = None,
    cellule: str | None = None,
) -> str:
    cible = construire_lien_excel(
        fichier=fichier,
        feuille=feuille,
        cellule=cellule,
    )
    cible_excel = echapper_chaine_excel(cible)
    affichage = echapper_chaine_excel(str(fichier.resolve()))
    return f'={fonction_hyperlien}("{cible_excel}"{separateur_formule}"{affichage}")'


def preparer_ligne_csv(
    resultat: ResultatRecherche,
    fonction_hyperlien: str,
    separateur_formule: str,
) -> Dict[str, str]:
    feuille = resultat.feuille.strip()
    cellule = resultat.cellule.strip()
    fichier = Path(resultat.fichier_brut)

    if resultat.erreur:
        lien = construire_formule_hyperlien_excel(
            fichier=fichier,
            feuille=None,
            cellule=None,
            fonction_hyperlien=fonction_hyperlien,
            separateur_formule=separateur_formule,
        )
    else:
        lien = construire_formule_hyperlien_excel(
            fichier=fichier,
            feuille=feuille or None,
            cellule=cellule or None,
            fonction_hyperlien=fonction_hyperlien,
            separateur_formule=separateur_formule,
        )

    return {
        "fichier": lien,
        "fichier_brut": neutraliser_formule_csv(resultat.fichier_brut),
        "feuille": neutraliser_formule_csv(resultat.feuille),
        "cellule": neutraliser_formule_csv(resultat.cellule),
        "correspondance_dans": neutraliser_formule_csv(resultat.correspondance_dans),
        "formule": neutraliser_formule_csv(resultat.formule),
        "valeur": neutraliser_formule_csv(resultat.valeur),
        "erreur": neutraliser_formule_csv(resultat.erreur),
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

    raise RuntimeError("Aucun moteur disponible. Installe pywin32 (Windows + Excel) ou pyxlsb.")


def creer_application_excel():
    if not EXCEL_COM_DISPONIBLE:
        raise RuntimeError("win32com / pywin32 indisponible.")

    pythoncom.CoInitialize()
    excel = win32com.client.DispatchEx("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    excel.ScreenUpdating = False

    try:
        excel.EnableEvents = False
    except Exception:
        pass

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


def _adresse_cellule_depuis_objet_com(cellule) -> str:
    """
    Construit une adresse A1 sans utiliser cellule.Address(...), car selon certaines
    versions/combinaisons pywin32 + Excel, Address peut être renvoyé comme une chaîne,
    ce qui mène à l'erreur 'str' object is not callable.
    """
    ligne = int(cellule.Row)
    colonne = int(cellule.Column)
    return construire_adresse_excel(ligne=ligne, colonne=colonne)


def chercher_dans_fichier_avec_excel(
    excel,
    fichier: Path,
    mot_recherche: str,
    exact: bool = False,
    ignore_case: bool = True,
    search_in: str = "both",
) -> Iterable[ResultatRecherche]:
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
            CorruptLoad=0,
        )

        resultats_ordonnes: "OrderedDict[Tuple[str, str], ResultatRecherche]" = OrderedDict()

        for feuille in classeur.Worksheets:
            nom_feuille = str(feuille.Name)
            try:
                plage = feuille.UsedRange
                if plage is None:
                    continue

                try:
                    nb_cellules = int(plage.Cells.Count)
                except Exception:
                    nb_cellules = 1

                cellule_depart = plage.Cells(nb_cellules)

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

                    premiere_adresse = _adresse_cellule_depuis_objet_com(premiere)
                    cellule = premiere

                    while cellule is not None:
                        adresse = _adresse_cellule_depuis_objet_com(cellule)
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
                            resultats_ordonnes[cle] = ResultatRecherche(
                                fichier_brut=fichier_resolu,
                                feuille=nom_feuille,
                                cellule=adresse,
                                correspondance_dans=nom_mode,
                                formule=formule,
                                valeur=valeur,
                                erreur="",
                            )
                        else:
                            precedent = resultats_ordonnes[cle]
                            resultats_ordonnes[cle] = ResultatRecherche(
                                fichier_brut=precedent.fichier_brut,
                                feuille=precedent.feuille,
                                cellule=precedent.cellule,
                                correspondance_dans=fusionner_correspondance(
                                    precedent.correspondance_dans,
                                    nom_mode,
                                ),
                                formule=precedent.formule or formule,
                                valeur=precedent.valeur or valeur,
                                erreur=precedent.erreur,
                            )

                        cellule = plage.FindNext(cellule)
                        if cellule is None:
                            break

                        adresse_suivante = _adresse_cellule_depuis_objet_com(cellule)
                        if adresse_suivante == premiere_adresse:
                            break

            except Exception as e:
                yield ResultatRecherche(
                    fichier_brut=fichier_resolu,
                    feuille=nom_feuille,
                    cellule="",
                    correspondance_dans="",
                    formule="",
                    valeur="",
                    erreur=f"Erreur lecture feuille: {e}",
                )

        for ligne in resultats_ordonnes.values():
            yield ligne

    except com_error as e:
        yield ResultatRecherche(
            fichier_brut=fichier_resolu,
            feuille="",
            cellule="",
            correspondance_dans="",
            formule="",
            valeur="",
            erreur=f"Erreur ouverture fichier via Excel: {e}",
        )
    except Exception as e:
        yield ResultatRecherche(
            fichier_brut=fichier_resolu,
            feuille="",
            cellule="",
            correspondance_dans="",
            formule="",
            valeur="",
            erreur=f"Erreur ouverture fichier via Excel: {e}",
        )
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
) -> Iterable[ResultatRecherche]:
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

                                    adresse = construire_adresse_excel(
                                        ligne=num_ligne,
                                        colonne=num_colonne,
                                    )

                                    yield ResultatRecherche(
                                        fichier_brut=fichier_resolu,
                                        feuille=str(nom_feuille),
                                        cellule=adresse,
                                        correspondance_dans="valeur",
                                        formule="",
                                        valeur=texte,
                                        erreur="",
                                    )

                except Exception as e:
                    yield ResultatRecherche(
                        fichier_brut=fichier_resolu,
                        feuille=str(nom_feuille),
                        cellule="",
                        correspondance_dans="",
                        formule="",
                        valeur="",
                        erreur=f"Erreur lecture feuille: {e}",
                    )

    except Exception as e:
        yield ResultatRecherche(
            fichier_brut=fichier_resolu,
            feuille="",
            cellule="",
            correspondance_dans="",
            formule="",
            valeur="",
            erreur=f"Erreur ouverture fichier: {e}",
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Recherche un mot dans tous les fichiers .xlsb d'un dossier et de ses "
            "sous-dossiers, puis génère un CSV cliquable.\n\n"
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
            "Moteur de lecture : auto (recommandé), excel (Excel installé sous Windows), "
            "ou pyxlsb (sans Excel, mais sans lecture des formules)."
        ),
    )
    parser.add_argument(
        "--excel-lang",
        choices=["fr", "en"],
        default="fr",
        help="Langue des formules Excel dans le CSV (fr=LIEN_HYPERTEXTE, en=HYPERLINK).",
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

    try:
        moteur, avertissements = lire_moteur_recherche(engine=args.engine, search_in=args.search_in)
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1

    for avertissement in avertissements:
        print(f"[AVERTISSEMENT] {avertissement}")

    fichiers = list(iterer_fichiers_xlsb(racine))
    if not fichiers:
        print(f"Aucun fichier .xlsb trouvé dans : {racine}")
        return 0

    parametres_excel = PARAMETRES_EXCEL_PAR_LANGUE[args.excel_lang]
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
            writer = csv.DictWriter(
                f,
                fieldnames=champs,
                delimiter=parametres_excel.separateur_csv,
                quoting=csv.QUOTE_MINIMAL,
            )
            writer.writeheader()

            for fichier in fichiers:
                nb_fichiers += 1

                if moteur == "excel":
                    iter_resultats = chercher_dans_fichier_avec_excel(
                        excel=excel,
                        fichier=fichier,
                        mot_recherche=args.mot,
                        exact=args.exact,
                        ignore_case=not args.case_sensitive,
                        search_in=args.search_in,
                    )
                else:
                    iter_resultats = chercher_dans_fichier_avec_pyxlsb(
                        fichier=fichier,
                        mot_recherche=args.mot,
                        exact=args.exact,
                        ignore_case=not args.case_sensitive,
                    )

                for resultat in iter_resultats:
                    writer.writerow(
                        preparer_ligne_csv(
                            resultat=resultat,
                            fonction_hyperlien=parametres_excel.fonction_hyperlien,
                            separateur_formule=parametres_excel.separateur_formule,
                        )
                    )

                    if resultat.erreur:
                        nb_erreurs += 1
                        print(
                            f"[ERREUR] {resultat.fichier_brut} | {resultat.feuille} | {resultat.erreur}"
                        )
                    else:
                        nb_occurrences += 1
                        print(
                            f"[OK] {resultat.fichier_brut} | "
                            f"Feuille: {resultat.feuille} | "
                            f"Cellule: {resultat.cellule} | "
                            f"Trouvé dans: {resultat.correspondance_dans} | "
                            f"Formule: {resultat.formule or '-'} | "
                            f"Valeur: {resultat.valeur or '-'}"
                        )

    finally:
        fermer_application_excel(excel)

    print("\n--- Résumé ---")
    print(f"Moteur utilisé : {moteur}")
    print(f"Fichiers analysés : {nb_fichiers}")
    print(f"Occurrences trouvées : {nb_occurrences}")
    print(f"Erreurs : {nb_erreurs}")
    print(f"CSV généré : {chemin_csv}")
    print(
        "Ouvre le CSV dans Excel pour que la colonne 'fichier' soit cliquable. "
        "Sur Windows + Excel de bureau, le lien ouvre le classeur et vise la feuille/cellule trouvée."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
