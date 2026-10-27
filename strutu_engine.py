"""Moteur des produits structurés : Phoenix et Autocall Plain à rappel optionnel.

Je simule le sous-jacent par Monte Carlo aux dates d'observation, je suis
les flux du produit scénario par scénario, puis je fais la moyenne.
Les montants sont en fraction du nominal (1 = 100 %).
Les paramètres sont vérifiés par validation.py avant d'arriver ici.
Les deux analyses suivent les mêmes étapes : calendrier, graine, cours, flux, statistiques.
"""

import numpy as np

from simulation_engine import simuler_cours_observations


# =========================
# Briques communes
# =========================

def barrieres_de_rappel(K_auto, step_down, n_dates):
    """Barrière de rappel à chaque date, en fraction du niveau de référence.

    Le step-down l'abaisse dès le deuxième constat : K_auto, K_auto - s, K_auto - 2s...
    Une seule formule, utilisée par les flux, l'affichage et la validation.
    """
    return K_auto - step_down*np.arange(n_dates)


def calculer_remboursement_phoenix_maturite(ST, S0, B_phoenix, B_pdi, coupon_final, strike_pct=1.0,
                                            bonus_threshold=None, bonus_amount=0.0):
    """Remboursement à maturité d'un Phoenix qui n'a PAS été rappelé.

    Sert au graphe (en fonction du prix final S_T) ET au calcul des flux :
    la règle de maturité n'est écrite qu'ici.
    Limite pour le graphe : je ne compte que le dernier coupon. Avec l'effet
    mémoire, le coupon final dépend du chemin, donc il ne se lit pas sur S_T.

    Renvoie : un dict avec le remboursement, le coupon, le bonus et le total
    (en fraction du nominal) pour chaque valeur de ST.
    """
    ST = np.asarray(ST, dtype=float)
    strike_reference = strike_pct * S0

    # au-dessus de la protection : 100 % ; en dessous : perte du sous-jacent (ex : 50/100 -> 50 %)
    remboursement = np.where(ST >= B_pdi * strike_reference, 1.0, ST / strike_reference)
    coupon = np.where(ST >= B_phoenix * strike_reference, coupon_final, 0.0)
    if bonus_threshold is not None:
        bonus = np.where(ST >= bonus_threshold * strike_reference, bonus_amount, 0.0)
    else:
        bonus = np.zeros_like(ST)

    return {"remboursement": remboursement, "coupon": coupon, "bonus": bonus,
            "total": remboursement + coupon + bonus}


def calculer_remboursement_autocall_maturite(ST, S0, K_auto, B_pdi, coupons_accumules):
    """Remboursement à maturité d'un Autocall Plain qui n'a PAS été rappelé.

    Tous les coupons accumulés si S_T >= K_auto*S0, sinon aucun.
    Capital : 100 %, sauf sous la protection où il vaut S_T/S0.
    B_pdi=None : capital toujours remboursé à 100 % dans le modèle.
    Sert au graphe ET au calcul des flux.
    """
    ST = np.asarray(ST, dtype=float)
    capital = np.ones_like(ST)
    if B_pdi is not None:
        capital = np.where(ST >= B_pdi * S0, 1.0, ST / S0)
    coupons = np.where(ST >= K_auto * S0, coupons_accumules, 0.0)
    return {"capital": capital, "coupons": coupons, "total": capital + coupons}


# =========================
# Phoenix : les flux scénario par scénario
# =========================

def _calculer_flux_phoenix(prix, S0, r, obs_dates, K_auto, B_phoenix, B_pdi, coupon_schedule,
                           memory, has_autocall, bonus_threshold, bonus_amount, strike_pct, step_down=0.0):
    """Suit les flux du Phoenix pour chaque scénario.

    À chaque date : rappel si S >= barrière de rappel, coupon si S >= barrière
    coupon (avec ou sans effet mémoire). À maturité : remboursement final.
    Renvoie : un dict avec les flux actualisés, les coupons actualisés, la date
    de rappel (-1 si jamais), S_T et un masque des scénarios en perte.
    """
    n_dates = len(obs_dates)
    n_simulations = prix.shape[0]

    # je passe les barrières en niveau de prix (ex : 70 % d'un strike à 100 -> 70)
    strike_reference = strike_pct * S0
    B_phoenix_level = B_phoenix * strike_reference
    rappel_levels = barrieres_de_rappel(K_auto, step_down, n_dates) * strike_reference

    flux_actualises = np.zeros(n_simulations)      # tout ce que le client reçoit, actualisé
    coupons_actualises = np.zeros(n_simulations)   # seulement la partie coupons
    rappele = np.zeros(n_simulations, dtype=bool)  # False tant que pas rappelé
    date_rappel = np.full(n_simulations, -1)       # -1 = jamais rappelé
    flux_attendus = np.zeros(n_dates)
    flux_attendus_actualises = np.zeros(n_dates)
    coupons_en_attente = np.zeros(n_simulations)   # j'en ai besoin pour l'effet mémoire

    for k in range(1, n_dates + 1):
        t_k = obs_dates[k - 1]
        S_k = prix[:, k]
        actualisation = np.exp(-r * t_k)

        # je ne regarde que les scénarios encore en vie
        actifs = ~rappele

        # je bloque le rappel à la dernière date, à maturité je passe par le remboursement final
        # (sinon mon bonus ne serait jamais versé)
        if has_autocall and k < n_dates:
            autocall_now = actifs & (S_k >= rappel_levels[k - 1])
        else:
            autocall_now = np.zeros(n_simulations, dtype=bool)

        coupon_du = actifs & (S_k >= B_phoenix_level)

        coupon_periode = coupon_schedule[k - 1]
        if memory:
            # j'accumule le coupon de la période, et je paie tout le stock dès que c'est au-dessus
            coupons_en_attente += np.where(actifs, coupon_periode, 0.0)
            montant_coupon = np.where(coupon_du, coupons_en_attente, 0.0)
            coupons_en_attente = np.where(coupon_du, 0.0, coupons_en_attente)  # je remets à 0 si c'est payé
        else:
            # sans mémoire un coupon raté est perdu
            montant_coupon = np.where(coupon_du, coupon_periode, 0.0)

        coupons_actualises += montant_coupon * actualisation
        flux_actualises += montant_coupon * actualisation
        # si rappel : je rends le nominal (1 = 100 %)
        flux_actualises += np.where(autocall_now, actualisation, 0.0)

        paiement = montant_coupon + autocall_now.astype(float)
        flux_attendus[k-1] += np.mean(paiement)
        flux_attendus_actualises[k-1] += np.mean(paiement * actualisation)
        date_rappel[autocall_now] = k - 1
        rappele = rappele | autocall_now

    # maturité : je traite ceux qui n'ont jamais été rappelés
    # (le coupon de la dernière date a déjà été traité dans la boucle, coupon_final=0 ici)
    S_T = prix[:, n_dates]
    actualisation_T = np.exp(-r * obs_dates[-1])
    non_rappelees = ~rappele
    final = calculer_remboursement_phoenix_maturite(S_T, S0, B_phoenix, B_pdi, 0.0, strike_pct,
                                                    bonus_threshold, bonus_amount)
    remboursement_T = np.where(non_rappelees, final["total"], 0.0)

    flux_actualises += remboursement_T * actualisation_T
    flux_attendus[-1] += np.mean(remboursement_T)
    flux_attendus_actualises[-1] += np.mean(remboursement_T * actualisation_T)

    return {
        "flux": flux_actualises,
        "flux_attendus": flux_attendus,
        "flux_attendus_actualises": flux_attendus_actualises,
        "coupons": coupons_actualises,
        "date_rappel": date_rappel,
        "S_T": S_T,
        "perte_capital": non_rappelees & (S_T < B_pdi * strike_reference),
        "strike_reference": strike_reference,
    }


# =========================
# Autocall Plain : les flux scénario par scénario
# =========================

def _calculer_flux_autocall(prix, S0, r, obs_dates, K_auto, B_pdi, coupon,
                            has_autocall, first_call_date, step_down=0.0):
    """Suit les flux du Plain sur des cours déjà simulés, comme pour le Phoenix.

    Les coupons sont accumulés depuis le début et payés à la sortie,
    jamais aux dates intermédiaires.
    """
    n_dates = len(obs_dates)
    n_simulations = prix.shape[0]
    rappel_levels = barrieres_de_rappel(K_auto, step_down, n_dates) * S0

    flux_actualises = np.zeros(n_simulations)
    coupons_actualises = np.zeros(n_simulations)
    rappele = np.zeros(n_simulations, dtype=bool)
    date_rappel = np.full(n_simulations, -1)
    flux_attendus = np.zeros(n_dates)
    flux_attendus_actualises = np.zeros(n_dates)
    nombre_coupons = np.zeros(n_simulations)

    for k in range(1, n_dates + 1):
        t_k = obs_dates[k - 1]
        S_k = prix[:, k]
        actualisation = np.exp(-r * t_k)
        actifs = ~rappele
        coupons_accumules = coupon * k

        rappel_autorise = first_call_date is None or t_k >= first_call_date
        if has_autocall and k < n_dates and rappel_autorise:
            autocall_now = actifs & (S_k >= rappel_levels[k - 1])
        else:
            autocall_now = np.zeros(n_simulations, dtype=bool)

        # À la sortie, je paie d'un coup tous les coupons accumulés.
        montant_coupon = np.where(autocall_now, coupons_accumules, 0.0)
        coupons_actualises += montant_coupon * actualisation
        flux_actualises += montant_coupon * actualisation
        flux_actualises += np.where(autocall_now, actualisation, 0.0)
        nombre_coupons[autocall_now] = k
        paiement = montant_coupon + autocall_now.astype(float)
        flux_attendus[k-1] += np.mean(paiement)
        flux_attendus_actualises[k-1] += np.mean(paiement * actualisation)
        date_rappel[autocall_now] = k - 1
        rappele = rappele | autocall_now

    # Comme pour le Phoenix, je traite à part ceux qui arrivent à maturité.
    S_T = prix[:, n_dates]
    actualisation_T = np.exp(-r * obs_dates[-1])
    non_rappelees = ~rappele
    final = calculer_remboursement_autocall_maturite(S_T, S0, K_auto, B_pdi, coupon * n_dates)
    montant_coupon = np.where(non_rappelees, final["coupons"], 0.0)
    remboursement_T = np.where(non_rappelees, final["capital"], 0.0)
    nombre_coupons[non_rappelees & (S_T >= K_auto * S0)] = n_dates

    coupons_actualises += montant_coupon * actualisation_T
    flux_actualises += (montant_coupon + remboursement_T) * actualisation_T
    flux_attendus[-1] += np.mean(montant_coupon + remboursement_T)
    flux_attendus_actualises[-1] += np.mean((montant_coupon + remboursement_T) * actualisation_T)

    perte_capital = np.zeros(n_simulations, dtype=bool)
    if B_pdi is not None:
        perte_capital = non_rappelees & (S_T < B_pdi * S0)

    return {
        "flux": flux_actualises,
        "flux_attendus": flux_attendus,
        "flux_attendus_actualises": flux_attendus_actualises,
        "coupons": coupons_actualises,
        "date_rappel": date_rappel,
        "S_T": S_T,
        "perte_capital": perte_capital,
        "strike_reference": S0,
        "nombre_coupons": nombre_coupons,
    }


# =========================
# Statistiques communes
# =========================

def _calculer_statistiques(res, dates, K_auto, step_down):
    """Statistiques communes aux deux produits, à partir du résultat d'une fonction de flux.

    Toutes les moyennes portent sur toutes les trajectoires, sauf le
    remboursement moyen si perte (moyenne conditionnelle, hors coupons et bonus).
    """
    flux_actualises, date_rappel, perte = res["flux"], res["date_rappel"], res["perte_capital"]
    n_simulations = len(flux_actualises)
    prix = float(np.mean(flux_actualises))
    erreur_type = float(np.std(flux_actualises, ddof=1) / np.sqrt(n_simulations))
    # np.maximum(..., 0) évite l'indice -1 (dernière date) pour les non-rappelés
    durees = np.where(date_rappel >= 0, dates[np.maximum(date_rappel, 0)], dates[-1])
    principal_si_perte = res["S_T"][perte] / res["strike_reference"]
    return {
        "prix": prix,
        "erreur_type": erreur_type,
        "ic95": (prix - 1.96 * erreur_type, prix + 1.96 * erreur_type),
        "marge_emetteur": 1.0 - prix,
        "proba_rappel_par_date": [float(np.mean(date_rappel == k)) for k in range(len(dates))],
        "proba_maturite": float(np.mean(date_rappel == -1)),
        "proba_perte_capital": float(np.mean(perte)),
        "remboursement_moyen_si_perte": float(np.mean(principal_si_perte)) if perte.any() else None,
        "duree_de_vie_moyenne": float(np.mean(durees)),
        "coupons_moyens_actualises": float(np.mean(res["coupons"])),
        "obs_dates": dates.tolist(),
        "flux_attendus_par_date": res["flux_attendus"].tolist(),
        "flux_attendus_actualises_par_date": res["flux_attendus_actualises"].tolist(),
        "barrieres_rappel": barrieres_de_rappel(K_auto, step_down, len(dates)).tolist(),
    }


# =========================
# Fonctions appelées par main.py
# =========================

def analyse_phoenix(S0, r, q, sigma, obs_dates, K_auto, B_phoenix, B_pdi, coupon,
                    n_simulations, memory=False, has_autocall=True,
                    bonus_threshold=None, bonus_amount=0.0, strike_pct=1.0, seed=None, step_down=0.0):
    """Price le Phoenix et calcule les stats des scénarios.

    Paramètres : S0, r, q, sigma en décimal, obs_dates en années, barrières
    K_auto / B_phoenix / B_pdi en fraction du strike (0.7 = 70 %), coupon
    (un nombre ou une liste pour un step-up), n_simulations, memory,
    has_autocall, bonus_threshold / bonus_amount (optionnels), strike_pct,
    seed (pour retomber sur les mêmes tirages), step_down.
    """
    dates = np.asarray(obs_dates, dtype=float)
    # coupon fixe -> je mets le même à chaque date, sinon j'ai une liste (step-up)
    coupon_schedule = np.broadcast_to(np.asarray(coupon, dtype=float), dates.shape)

    prix = simuler_cours_observations(S0, r, q, sigma, dates, n_simulations, np.random.default_rng(seed))
    res = _calculer_flux_phoenix(prix, S0, r, dates, K_auto, B_phoenix, B_pdi,
                                 coupon_schedule, memory, has_autocall,
                                 bonus_threshold, bonus_amount, strike_pct, step_down)
    return {**_calculer_statistiques(res, dates, K_auto, step_down),
            "coupon_schedule": coupon_schedule.tolist()}


def analyse_autocall_plain(S0, r, q, sigma, obs_dates, K_auto, B_pdi, coupon,
                           n_simulations=50000, first_call_date=None, seed=None, has_autocall=True, step_down=0.0):
    """Autocall Plain : coupon accumulé jusqu'au rappel ou à maturité.

    Conventions identiques au Phoenix : r, q, sigma et coupon en décimal,
    barrières en fraction de S0, dates en années, montants en fraction du nominal.
    Exemple : coupon=0.02 signifie 2 % PAR OBSERVATION (pas par an).

    Rappel à la k-ième date (k = 1, 2...) si S >= barrière : paiement de 1 + k*coupon.
    Sans rappel, à maturité : tous les coupons si S_T >= K_auto*S0 ; sinon
    aucun coupon. Capital : 1, sauf sous B_pdi*S0 où il vaut S_T/S0.
    has_autocall=False supprime uniquement le rappel anticipé.
    B_pdi=None désactive la perte conditionnelle de capital.
    first_call_date décale le début des rappels, sans décaler l'accumulation.
    La protection n'est jamais surveillée entre les dates ni avant maturité.

    Traduction en phrase : on attend le rappel, puis on paie d'un coup le
    nominal et les coupons accumulés. Ce n'est pas un Phoenix à coupons périodiques.
    """
    dates = np.asarray(obs_dates, dtype=float)

    prix = simuler_cours_observations(S0, r, q, sigma, dates, n_simulations, np.random.default_rng(seed))
    res = _calculer_flux_autocall(prix, S0, r, dates, K_auto, B_pdi, coupon,
                                  has_autocall, first_call_date if has_autocall else None, step_down)
    return {**_calculer_statistiques(res, dates, K_auto, step_down),
            "nombre_moyen_coupons": float(np.mean(res["nombre_coupons"]))}
