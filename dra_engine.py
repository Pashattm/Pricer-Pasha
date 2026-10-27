"""Dual Range Accrual sur deux actions GBM corrélées.

Taux et volatilités en décimal ; T en années ; fourchettes en prix absolus.
Capital garanti à T dans le modèle, hors défaut ; coupons aux dates de paiement.
Les paramètres sont vérifiés par validation.py avant d'arriver ici.
"""
import numpy as np

from simulation_engine import simuler_cours_correles


def n_constats_dra(T, jours_par_an):
    """Nombre de constats quotidiens : utilisé par le calcul ET par la validation du budget."""
    return max(1, int(round(T*jours_par_an)))


def _calendrier(T, n_jours, frequence):
    """Paiements périodiques avec dernière période courte si nécessaire.

    Grille quotidienne régulière de n_jours constats ;
    la fraction accrued est calculée séparément dans chaque période.
    """
    paiements = np.minimum(np.arange(1, int(np.ceil(T*frequence))+1)/frequence, T)
    jours = np.arange(1, n_jours+1)*T/n_jours
    groupes = np.searchsorted(paiements, jours-1e-12, side='left')
    effectifs = np.bincount(groupes, minlength=len(paiements))
    if np.any(effectifs == 0):
        raise ValueError("Une période de coupon ne contient aucun constat : diminuez la fréquence de paiement.")
    durees = np.diff(np.r_[0., paiements])
    return paiements, jours, groupes, durees


def analyse_dra(S1_0, S2_0, r, q1, q2, sigma1, sigma2, rho, T, L1, U1, L2, U2,
                taux_annuel=.08, nominal=1000, n_simulations=50000,
                jours_par_an=252, frequence_paiement=4, seed=42):
    """Prix, IC95, jours accrued et coupons, par lots pour limiter la mémoire.

    Coupon par période = nominal*taux_annuel*durée*jours_doubles/jours_période.
    Capital garanti payé à T ; aucune observation ni coupon au jour initial.
    Les lots, au plus 4 000 trajectoires et environ 1,1 million de points,
    sont identiques à chaque rho pour reproduire la sensibilité.
    Tous les montants sont en devise du nominal ; taux annualisé en décimal.
    """
    n_jours = n_constats_dra(T, jours_par_an)
    paiements, jours, groupes, durees = _calendrier(T, n_jours, frequence_paiement)
    actualisation = np.exp(-r*paiements)
    valeurs = np.empty(n_simulations)
    comptes = np.empty(n_simulations, dtype=int)
    coupons_totaux = np.empty(n_simulations)
    somme_coupons = np.zeros(len(paiements))
    rng = np.random.default_rng(seed)
    taille_lot = min(4000, max(1, 1_100_000//(n_jours+1)))
    for debut in range(0, n_simulations, taille_lot):
        fin = min(debut+taille_lot, n_simulations)
        p1, p2 = simuler_cours_correles(S1_0, S2_0, r, q1, q2, sigma1, sigma2, rho, jours, fin-debut, rng)
        dans = (p1[:, 1:] >= L1) & (p1[:, 1:] <= U1) & (p2[:, 1:] >= L2) & (p2[:, 1:] <= U2)
        comptes[debut:fin] = np.sum(dans, axis=1)
        coupons = np.empty((fin-debut, len(paiements)))
        for i in range(len(paiements)):
            coupons[:, i] = nominal*taux_annuel*durees[i]*np.mean(dans[:, groupes == i], axis=1)
        somme_coupons += np.sum(coupons, axis=0)
        coupons_totaux[debut:fin] = np.sum(coupons, axis=1)
        valeurs[debut:fin] = nominal*np.exp(-r*T)+coupons@actualisation
    prix = float(np.mean(valeurs))
    erreur = float(np.std(valeurs, ddof=1)/np.sqrt(n_simulations))
    # Bornes demi-entières : les jours sont discrets, regroupés au-delà de 60 classes.
    nb_classes = min(n_jours+1, 60)
    bornes = np.linspace(-.5, n_jours+.5, nb_classes+1)
    histogramme, _ = np.histogram(comptes, bins=bornes)
    coupon_moyen = float(np.mean(coupons_totaux))
    # Flux moyens par date de paiement : coupon moyen, plus le capital à la dernière date.
    flux = somme_coupons/n_simulations
    flux[-1] += nominal
    return {"prix": prix, "ic95": [prix-1.96*erreur, prix+1.96*erreur], "erreur_type": erreur,
            "proba_dans_fourchettes": float(np.mean(comptes)/n_jours),
            "jours_accrued_moyens": float(np.mean(comptes)), "n_jours": n_jours,
            "histogramme": {"bornes": bornes.tolist(), "effectifs": histogramme.tolist(),
                            "probabilites": (histogramme/n_simulations).tolist()},
            "coupon_moyen": coupon_moyen, "coupon_moyen_annualise": coupon_moyen/(nominal*T),
            "coupon_max": nominal*taux_annuel*T, "taux_annuel_max": taux_annuel,
            "prix_coupon_max": float(nominal*np.exp(-r*T)+nominal*taux_annuel*np.sum(durees*actualisation)),
            "coupon_moyen_actualise": float(prix-nominal*np.exp(-r*T)),
            "obs_dates": paiements.tolist(),
            "flux_attendus_par_date": flux.tolist(),
            "flux_attendus_actualises_par_date": (flux*actualisation).tolist(),
            "nominal": nominal, "rho": rho, "seed": seed, "n_simulations": n_simulations}


def sensibilite_correlation(S1_0, S2_0, r, q1, q2, sigma1, sigma2, T, L1, U1, L2, U2,
                            taux_annuel=.08, nominal=1000, n_simulations=50000,
                            jours_par_an=252, frequence_paiement=4, seed=42):
    """Prix vs rho de -0.9 à 0.9 (7 points), mêmes normales à chaque évaluation.

    La courbe n'est pas supposée monotone. La graine (toujours fournie par
    validation.py) fait partager les nombres aléatoires ; chaque prix comporte un IC95.
    """
    rhos = np.linspace(-.9, .9, 7)
    resultats = [analyse_dra(S1_0, S2_0, r, q1, q2, sigma1, sigma2, float(rho), T, L1, U1, L2, U2,
                             taux_annuel, nominal, n_simulations, jours_par_an, frequence_paiement, seed)
                 for rho in rhos]
    return {"rhos": rhos.tolist(), "prix": [x["prix"] for x in resultats],
            "ic95": [x["ic95"] for x in resultats],
            "impact_prix_extremes": resultats[-1]["prix"]-resultats[0]["prix"]}
