"""Simulateur commun : mouvement brownien géométrique sous la probabilité risque-neutre.

C'est le SEUL endroit du projet où la diffusion du sous-jacent est écrite.
Il est utilisé par :
- pricer_engine (Monte Carlo européen, digitales, Longstaff-Schwartz) ;
- strutu_engine (Phoenix, Autocall Plain) ;
- accumulator_engine (accumulateur, décumulateur) ;
- dra_engine (deux actifs corrélés).

Convention : r, q, sigma en décimal, dates en années.
Les paramètres sont vérifiés par validation.py avant d'arriver ici.
"""

import numpy as np


def simuler_cours_observations(S0, r, q, sigma, obs_dates, n_simulations, rng=None, chocs=None):
    """Simule le sous-jacent uniquement aux dates d'observation.

    Sous Black-Scholes, le passage d'une date à la suivante est exact, quel que
    soit l'écart entre les dates : on ne simule que les dates utiles.

    rng   : générateur numpy (np.random.default_rng(graine)) pour des tirages reproductibles.
    chocs : tirages normaux fournis par l'appelant, de forme (dates, simulations).
            Sert aux deux actifs corrélés du DRA.
    Renvoie : une matrice (n_simulations, n_dates + 1), colonne 0 = S0.
    """
    if rng is None:
        rng = np.random.default_rng()

    prix = np.zeros((n_simulations, len(obs_dates) + 1))
    prix[:, 0] = S0  # tous les scénarios partent du même spot

    t_precedent = 0.0
    for i, t in enumerate(obs_dates):
        dt = t - t_precedent  # temps écoulé depuis la dernière observation
        Z = rng.standard_normal(n_simulations) if chocs is None else chocs[i]
        croissance = (r - q - sigma**2 / 2) * dt   # dérive avec correction d'Itô
        choc = sigma * np.sqrt(dt) * Z
        prix[:, i + 1] = prix[:, i] * np.exp(croissance + choc)
        t_precedent = t

    return prix


def simuler_cours_correles(S1_0, S2_0, r, q1, q2, sigma1, sigma2, rho,
                            obs_dates, n_simulations, rng=None):
    """Deux actifs corrélés : chocs de Cholesky, puis deux appels au simulateur commun.

    z2 = rho*z1 + racine(1 - rho²)*epsilon a une corrélation rho avec z1.
    Le générateur est poursuivi : d'abord z1, puis epsilon.
    """
    rng = np.random.default_rng() if rng is None else rng
    forme = (len(obs_dates), n_simulations)
    z1 = rng.standard_normal(forme)
    z2 = rho*z1 + np.sqrt(max(0., 1 - rho*rho))*rng.standard_normal(forme)
    prix1 = simuler_cours_observations(S1_0, r, q1, sigma1, obs_dates, n_simulations, chocs=z1)
    prix2 = simuler_cours_observations(S2_0, r, q2, sigma2, obs_dates, n_simulations, chocs=z2)
    return prix1, prix2
