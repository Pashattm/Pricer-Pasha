"""Accumulateur et décumulateur : flux observés, KO discret et levier.

r, q et sigma en décimal, dates en années, K et B_KO en prix absolus.
Les flux sont les gains/pertes mark-to-market des achats/ventes au constat,
actualisés individuellement ; aucune mise initiale n'est déduite.
Les paramètres sont vérifiés par validation.py avant d'arriver ici.
Un seul jeu de fonctions pour les deux produits : seul le signe change.
"""
import numpy as np

from simulation_engine import simuler_cours_observations


def _flux_contrat(prix, dates, r, K, B_KO, quantite, levier, produit,
                  ko_inclut_date, n_garanties, details=None):
    """Calcule les flux de chemins déjà simulés, sans nouveau tirage aléatoire."""
    n = prix.shape[0]
    actif = np.ones(n, dtype=bool)
    pnl = np.zeros(n)
    quantites = np.zeros(n)
    sortie = np.full(n, dates[-1])
    date_ko = np.full(n, -1)
    signe = 1 if produit == "accumulateur" else -1
    for i, date in enumerate(dates):
        spot = prix[:, i+1]
        # KO par le haut pour l'accumulateur, par le bas pour le décumulateur ;
        # désactivé pendant les n_garanties premiers constats
        touche = spot >= B_KO if signe == 1 else spot <= B_KO
        ko = actif & touche & (i >= n_garanties)
        operation = actif if ko_inclut_date else actif & ~ko
        gain_unitaire = signe*(spot-K)
        # levier appliqué seulement dans le scénario défavorable au client
        volume = quantite*np.where(gain_unitaire < 0, levier, 1)*operation
        flux_date = volume*gain_unitaire
        pnl += flux_date*np.exp(-r*date)
        if details is not None:
            details['flux_attendus_par_date'].append(float(np.mean(flux_date)))
            details['flux_attendus_actualises_par_date'].append(float(np.mean(flux_date))*np.exp(-r*date))
        quantites += volume
        sortie[ko] = date
        date_ko[ko] = i
        actif = actif & ~ko
    return pnl, quantites, sortie, date_ko


def _statistiques(pnl, quantites, sortie, date_ko, dates):
    """Agrège les P&L actualisés ; perte maximale observée, sans borne théorique."""
    n = len(pnl)
    prix = float(np.mean(pnl))
    erreur = float(np.std(pnl, ddof=1)/np.sqrt(n))
    effectifs, bornes = np.histogram(pnl, bins=40)
    return {"prix": prix, "erreur_type": erreur,
            "ic95": [prix-1.96*erreur, prix+1.96*erreur],
            "proba_ko_par_date": [float(np.mean(date_ko == i)) for i in range(len(dates))],
            "proba_non_ko": float(np.mean(date_ko == -1)),
            "duree_de_vie_moyenne": float(np.mean(sortie)),
            "quantite_totale_moyenne": float(np.mean(quantites)),
            "perte_maximale_observee": float(max(0, -np.min(pnl))),
            "histogramme": {"bornes": bornes.tolist(), "effectifs": effectifs.tolist(),
                            "probabilites": (effectifs/n).tolist()},
            "obs_dates": dates.tolist(), "n_simulations": n}


def analyse_accumulation(produit, S0, r, q, sigma, obs_dates, K, B_KO, quantite=1,
                         levier=2, n_simulations=50000, ko_inclut_date=False,
                         n_garanties=0, seed=None):
    """Valeur, IC95 et statistiques d'un accumulateur ou d'un décumulateur.

    produit="accumulateur" : achats à K, volume × levier si S_i < K, KO si S_i ≥ B_KO.
    produit="decumulateur" : ventes à K, volume × levier si S_i > K, KO si S_i ≤ B_KO.
    """
    dates = np.asarray(obs_dates, dtype=float)
    chemins = simuler_cours_observations(S0, r, q, sigma, dates, n_simulations, np.random.default_rng(seed))
    details = {'flux_attendus_par_date': [], 'flux_attendus_actualises_par_date': []}
    flux = _flux_contrat(chemins, dates, r, K, B_KO, quantite, levier, produit,
                         ko_inclut_date, n_garanties, details)
    return {**_statistiques(*flux, dates), **details}


def strike_zero_cout(produit, S0, r, q, sigma, obs_dates, B_KO, quantite=1, levier=2,
                     n_simulations=50000, ko_inclut_date=False, n_garanties=0, seed=42,
                     tolerance=1e-8, max_iterations=100):
    """Dichotomie avec chemins communs : aucun nouveau tirage aux évaluations.

    Cherche uniquement un strike respectant K<S0 (achat) ou K>S0 (vente).
    Une racine admissible n'existe pas toujours ; je le signale explicitement.
    tolerance est la précision relative sur le strike, pas une garantie statistique.
    Renvoie le strike K, la valeur résiduelle, l'IC95 et les statistiques sur le même échantillon.
    """
    dates = np.asarray(obs_dates, dtype=float)
    chemins = simuler_cours_observations(S0, r, q, sigma, dates, n_simulations, np.random.default_rng(seed))

    def evaluer(K):
        return _flux_contrat(chemins, dates, r, K, B_KO, quantite, levier, produit, ko_inclut_date, n_garanties)

    if produit == "accumulateur":
        bas, haut = S0*1e-12, np.nextafter(S0, 0)
    else:
        bas, haut = np.nextafter(S0, np.inf), max(S0*2, float(np.max(chemins))*1.01)
    valeur_bas, valeur_haut = (float(np.mean(evaluer(K)[0])) for K in (bas, haut))
    if valeur_bas*valeur_haut > 0:
        raise ValueError("Aucun strike zéro coût dans le domaine admissible pour ces paramètres et cet échantillon.")
    # Sans opération sur aucun chemin, toute valeur serait nulle : pas de cotation unique.
    if np.max(evaluer(S0*.95 if produit == "accumulateur" else S0*1.05)[1]) == 0:
        raise ValueError("Aucune opération simulée : strike zéro coût indéterminé.")
    if valeur_bas == 0:
        K = bas; iteration = 0
    elif valeur_haut == 0:
        K = haut; iteration = 0
    else:
        for iteration in range(1, max_iterations+1):
            K = (bas+haut)/2
            valeur = float(np.mean(evaluer(K)[0]))
            if valeur == 0 or haut-bas <= tolerance*S0:
                break
            if valeur*valeur_bas > 0:
                bas = K; valeur_bas = valeur
            else:
                haut = K
        else:
            raise ValueError("La dichotomie n'a pas convergé.")
    resultat = _statistiques(*evaluer(K), dates)
    resultat.update(K=float(K), iterations=iteration)
    return resultat
