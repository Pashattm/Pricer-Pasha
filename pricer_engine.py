"""Moteur de pricing : options vanilles, grecques, forwards, stratégies et digitales.

Convention : r, q et sigma en décimal (0.05 = 5 %), T en années.
Les paramètres sont vérifiés par validation.py avant d'arriver ici.
Les simulations passent par simulation_engine.py (une seule diffusion dans tout le projet).
"""

import numpy as np
from statistics import NormalDist

from simulation_engine import simuler_cours_observations


# =========================
# Fonctions maths
# =========================

def Ncdf(x):
    """Fonction de répartition N(x) de la loi normale centrée réduite."""
    return NormalDist().cdf(x)

def Npdf(x):
    """Densité n(x) de la loi normale centrée réduite."""
    return NormalDist().pdf(x)

def _d1_d2(S0, K, T, r, q, sigma):
    """Calcule d1 et d2, que j'utilise dans Black-Scholes et toutes les grecques."""
    d1 = (np.log(S0 / K) + (r - q + sigma**2 / 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return d1, d2

# =========================
# BLACK-SCHOLES
# =========================

def black_scholes(S0, K, T, r, q, sigma, option_type):
    """Prix d'une option européenne par Black-Scholes, avec dividende continu q.

    Paramètres : S0, K, T, r, q, sigma, option_type ("call" ou "put").
    Renvoie : le prix de l'option.
    """
    d1, d2 = _d1_d2(S0, K, T, r, q, sigma)

    if option_type == "call":
        prix = S0 * np.exp(-q*T) * Ncdf(d1) - K * np.exp(-r*T) * Ncdf(d2)
    else:
        prix = K * np.exp(-r*T) * Ncdf(-d2) - S0 * np.exp(-q*T) * Ncdf(-d1)

    return prix


# =========================
# MONTE CARLO EUROPÉEN (vanille et digitales)
# =========================

def _simuler_ST(S0, T, r, q, sigma, n_simulations, seed):
    """Simule directement S_T : une européenne ne dépend que du prix final."""
    rng = np.random.default_rng(seed)
    return simuler_cours_observations(S0, r, q, sigma, [T], n_simulations, rng)[:, 1]


def _resultat_monte_carlo(flux):
    """Prix, écart-type des flux actualisés, erreur standard et IC normal à 95 %."""
    prix = float(np.mean(flux))
    ecart_type = float(np.std(flux, ddof=1))
    erreur_standard = ecart_type/np.sqrt(len(flux))
    return {"price": prix, "ecart_type": ecart_type,
            "erreur_standard": float(erreur_standard),
            "ic95": [prix-1.96*erreur_standard, prix+1.96*erreur_standard]}


def monte_carlo(S0, K, T, r, q, sigma, option_type, n_simulations, seed=None):
    """Prix d'une option européenne par Monte Carlo, avec son intervalle de confiance.

    Le résultat doit être proche de Black-Scholes : l'IC95 permet de le vérifier.
    seed permet de retrouver exactement le même prix.
    """
    ST = _simuler_ST(S0, T, r, q, sigma, n_simulations, seed)
    payoff = np.maximum(ST - K, 0) if option_type == "call" else np.maximum(K - ST, 0)
    return _resultat_monte_carlo(np.exp(-r*T)*payoff)


def digital_monte_carlo(S0, K, T, r, q, sigma, option_type, n_simulations,
                        payout=1.0, seed=None, digital_type="cash"):
    """Prix Monte Carlo d'une digitale, avec son intervalle de confiance.

    payout ne concerne que la cash-or-nothing ; l'asset-or-nothing livre S_T.
    """
    ST = _simuler_ST(S0, T, r, q, sigma, n_simulations, seed)
    condition = ST > K if option_type == "call" else ST < K
    flux = np.exp(-r*T)*condition*(ST if digital_type == "asset" else payout)
    return _resultat_monte_carlo(flux)


# =========================
# LONGSTAFF-SCHWARTZ
# OPTION AMÉRICAINE
# =========================

def longstaff_schwartz(S0, K, T, r, q, sigma, option_type, n_simulations, n_steps=100, seed=None):
    """Prix d'une option américaine par Longstaff-Schwartz.

    Je simule les chemins complets sur n_steps dates, puis je remonte le temps :
    à chaque date, je compare le payoff d'exercice immédiat avec la valeur de
    continuation, que j'estime par une régression polynomiale de degré 2 sur
    les chemins dans la monnaie.
    Renvoie : le prix, avec au minimum la valeur d'exercice immédiat.
    """
    dt = T / n_steps

    # chemins complets (ici j'ai besoin de toutes les dates), avec le simulateur commun
    dates = np.arange(1, n_steps + 1) * dt
    prix = simuler_cours_observations(S0, r, q, sigma, dates, n_simulations,
                                      np.random.default_rng(seed))

    # je calcule le payoff d'exercice à chaque date
    if option_type == "call":
        payoff = np.maximum(prix - K, 0)
    else:
        payoff = np.maximum(K - prix, 0)

    # au départ je suppose qu'on exerce à maturité
    cashflow = payoff[:, -1]

    # je remonte dans le temps
    for t in range(n_steps - 1, 0, -1):

        cashflow *= np.exp(-r * dt)  # je ramène les flux d'une période

        # je fais la régression seulement sur les chemins dans la monnaie
        itm = payoff[:, t] > 0

        if np.sum(itm) > 2:

            X = prix[itm, t]
            Y = cashflow[itm]

            # je standardise X sinon polyfit est instable (grands nombres au carré)
            X_norm = (X - X.mean()) / X.std()

            # j'estime la valeur de continuation avec un polynôme de degré 2
            regression = np.polyfit(X_norm, Y, 2)
            continuation = np.polyval(regression, X_norm)

            # j'exerce si le payoff immédiat est mieux que continuer
            exercice = payoff[itm, t] > continuation

            indices = np.where(itm)[0]
            cashflow[indices[exercice]] = payoff[indices[exercice], t]

    prix_lsm = np.mean(cashflow) * np.exp(-r * dt)

    # une américaine vaut au moins son exercice immédiat, je mets ce plancher
    return max(prix_lsm, payoff[0, 0])

# =========================
# Grecques primaires
# =========================

def grecs_primaires_bs(S0, K, T, r, q, sigma, option_type):
    """Grecques primaires Black-Scholes (avec dividende q).

    Renvoie : un dict {Delta, Gamma, Vega, Theta, Rho}.
    Vega et Rho sont pour une variation de 1 (100 %) de sigma ou de r,
    Theta est annuel.
    """
    d1, d2 = _d1_d2(S0, K, T, r, q, sigma)

    # delta : sensibilité au spot
    if option_type == "call":
        Delta = np.exp(-q*T)*Ncdf(d1)
    else:
        Delta = np.exp(-q*T)*(Ncdf(d1)-1)

    # gamma : variation du delta, pareil pour call et put
    Gamma = np.exp(-q*T)*Npdf(d1)/(S0*sigma*np.sqrt(T))

    # vega : sensibilité à la vol, pareil pour call et put
    Vega = S0*np.exp(-q*T)*Npdf(d1)*np.sqrt(T)

    # theta : perte de valeur avec le temps (par an)
    if option_type == "call":
        Theta = -(S0*sigma*np.exp(-q*T)*Npdf(d1))/(2*np.sqrt(T)) - r*K*np.exp(-r*T)*Ncdf(d2) + q*S0*np.exp(-q*T)*Ncdf(d1)
    else:
        Theta = -(S0*sigma*np.exp(-q*T)*Npdf(d1))/(2*np.sqrt(T)) + r*K*np.exp(-r*T)*Ncdf(-d2) - q*S0*np.exp(-q*T)*Ncdf(-d1)

    # rho : sensibilité aux taux
    if option_type == "call":
        Rho = K*T*np.exp(-r*T)*Ncdf(d2)
    else:
        Rho = -K*T*np.exp(-r*T)*Ncdf(-d2)

    return {"Delta": Delta, "Gamma": Gamma, "Vega": Vega, "Theta": Theta, "Rho": Rho}

# =========================
# Grecques secondaires
# =========================

def grecs_secondaires_bs(S0, K, T, r, q, sigma, option_type):
    """Grecques secondaires Black-Scholes.

    Renvoie : un dict {Vanna, Vomma, Charm, Color, Speed, Zomma, Ultima, Lambda}.
    Lambda = élasticité de l'option (Delta x S0 / prix).
    """
    d1, d2 = _d1_d2(S0, K, T, r, q, sigma)
    # Delta, Gamma et Vega viennent des grecques primaires (une seule formule par grecque)
    primaires = grecs_primaires_bs(S0, K, T, r, q, sigma, option_type)
    Delta, Gamma, Vega = primaires["Delta"], primaires["Gamma"], primaires["Vega"]

    # vanna : delta par rapport à la vol
    Vanna = (-np.exp(-q*T)*Npdf(d1)*d2)/sigma

    # vomma : vega par rapport à la vol
    Vomma = (Vega*d1*d2)/sigma

    # charm : delta par rapport au temps
    if option_type == "call":
        Charm = q*np.exp(-q*T)*Ncdf(d1)-np.exp(-q*T)*Npdf(d1)*(2*(r-q)*T - d2*sigma*np.sqrt(T)) / (2*T*sigma*np.sqrt(T))
    else:
        Charm = -q*np.exp(-q*T)*Ncdf(-d1)-np.exp(-q*T)*Npdf(d1)*(2*(r-q)*T-d2*sigma*np.sqrt(T))/(2*T*sigma*np.sqrt(T))

    # color : gamma par rapport au temps
    Color = -np.exp(-q*T)*Npdf(d1)/(2*S0*T*sigma*np.sqrt(T))*(2*q*T+1+(2*(r-q)*T-d2*sigma*np.sqrt(T)))*d1/(sigma*np.sqrt(T))

    # speed : gamma par rapport au spot
    Speed = -Gamma/S0*(d1/(sigma*np.sqrt(T))+1)

    # zomma : gamma par rapport à la vol / ultima : vomma par rapport à la vol
    Zomma = Gamma*(d1*d2-1)/sigma
    Ultima = -Vega*(d1*d2*(1-d1*d2)+d1**2+d2**2)/sigma**2

    # lambda : élasticité (effet de levier de l'option)
    Lambda = Delta * S0 / black_scholes(S0, K, T, r, q, sigma, option_type)

    return {"Vanna": Vanna, "Vomma": Vomma, "Charm": Charm, "Color": Color,
            "Speed": Speed, "Zomma": Zomma, "Ultima": Ultima, "Lambda": Lambda}

# =========================
# Futures / Forward
# =========================

def forward_price(S0, T, r, q):
    """Prix à terme théorique : F = S0 x e^((r - q) T)."""
    return S0*np.exp((r-q)*T)

def forward_value(S0, K, T, r, q, side):
    """Valeur aujourd'hui d'un forward déjà signé au prix K.

    side = "buy" ou "sell". Renvoie (F - K) actualisé pour l'acheteur,
    l'inverse pour le vendeur.
    """
    sign = 1 if side == "buy" else -1
    return sign*(forward_price(S0, T, r, q)-K)*np.exp(-r*T)

def forward_payoff(ST, K, side):
    """Payoff à maturité d'un forward : S_T - K si long, K - S_T si short.

    ST peut être un nombre ou toute une grille numpy.
    """
    sign = 1 if side == "buy" else -1
    return sign*(np.asarray(ST, dtype=float)-K)

# =========================
# Legs
# =========================

def make_position(option_type, side, K, T, qty=1):
    """Construit une jambe option (long ou short, call ou put)."""
    return {"instrument": "option", "option_type": option_type, "side": side,
            "K": K, "T": T, "qty": qty}

def make_stock_position(side, qty=1):
    """Construit une jambe action (pas de strike ni de maturité)."""
    return {"instrument": "stock", "side": side, "qty": qty}

def _signe(leg):
    """+1 pour une jambe achetée, -1 pour une jambe vendue."""
    return 1 if leg["side"] == "buy" else -1

def price_leg(leg, S0, r, q, sigma):
    """Coût d'une jambe aujourd'hui, avec le signe.

    Positif si j'achète, négatif si je vends (j'encaisse la prime).
    """
    if leg["instrument"] == "stock":
        prix_unitaire = S0
    else:
        prix_unitaire = black_scholes(S0, leg["K"], leg["T"], r, q, sigma, leg["option_type"])
    return _signe(leg) * leg["qty"] * prix_unitaire

# =========================
# Payoffs
# =========================

def payoff_leg(leg, ST):
    """Payoff à maturité d'une jambe, sur toute la grille de S_T (tableau numpy)."""
    if leg["instrument"] == "stock":
        payoff = ST
    elif leg["option_type"] == "call":
        payoff = np.maximum(ST - leg["K"], 0)
    else:
        payoff = np.maximum(leg["K"] - ST, 0)
    return _signe(leg) * leg["qty"] * payoff

def payoff_strategie(legs, S0, r, q, sigma, ST_grid):
    """Payoff et P&L de toute la stratégie sur la grille de S_T.

    Je fais la somme des jambes. P&L = payoff total - coût total de mise en place.
    Renvoie : un dict {payoff, pnl}.
    """
    payoff_total = sum(payoff_leg(leg, ST_grid) for leg in legs)
    cout_total = sum(price_leg(leg, S0, r, q, sigma) for leg in legs)
    return {"payoff": payoff_total, "pnl": payoff_total - cout_total}

def greeks_strategie(legs, S0, r, q, sigma):
    """Grecques primaires de la stratégie.

    J'additionne les grecques de chaque jambe avec leur signe. Une action
    apporte juste un delta de 1.
    Renvoie : un dict {Delta, Gamma, Vega, Theta, Rho}.
    """
    total = {"Delta": 0, "Gamma": 0, "Vega": 0, "Theta": 0, "Rho": 0}
    for leg in legs:
        poids = _signe(leg) * leg["qty"]
        if leg["instrument"] == "stock":
            # une action a juste un delta de 1, le reste vaut 0
            total["Delta"] += poids
        else:
            grecs = grecs_primaires_bs(S0, leg["K"], leg["T"], r, q, sigma, leg["option_type"])
            for cle in total:
                total[cle] += poids * grecs[cle]
    return total

# =========================
# Stratégies
# =========================

def make_covered_call(K, T, qty=1):
    """Covered call : achat de l'action + vente d'un call de strike K."""
    return [make_stock_position("buy", qty=qty), make_position("call", "sell", K, T, qty=qty)]

def make_protective_put(K, T, qty=1):
    """Protective put : achat de l'action + achat d'un put de strike K (assurance contre la baisse)."""
    return [make_stock_position("buy", qty=qty), make_position("put", "buy", K, T, qty=qty)]

def make_straddle(K, T, side="buy", qty=1):
    """Straddle : call + put de même strike K, même sens (pari sur la vol)."""
    return [make_position("call", side, K, T, qty=qty), make_position("put", side, K, T, qty=qty)]

def make_strangle(K_put, K_call, T, side="buy", qty=1):
    """Strangle : call K_call + put K_put (K_put < K_call), même sens. Moins cher qu'un straddle."""
    return [make_position("call", side, K_call, T, qty=qty), make_position("put", side, K_put, T, qty=qty)]

def make_call_spread(K1, K2, T, qty=1):
    """Bull call spread : achat call K1 + vente call K2, avec K1 < K2."""
    return [make_position("call", "buy", K1, T, qty=qty), make_position("call", "sell", K2, T, qty=qty)]

def make_put_spread(K1, K2, T, qty=1):
    """Bear put spread : achat put K1 + vente put K2, avec K1 > K2."""
    return [make_position("put", "buy", K1, T, qty=qty), make_position("put", "sell", K2, T, qty=qty)]

def make_collar(K_put, K_call, T, qty=1):
    """Collar : achat de l'action + achat put K_put + vente call K_call (K_put < K_call)."""
    return [make_stock_position("buy", qty=qty),
            make_position("put", "buy", K_put, T, qty=qty),
            make_position("call", "sell", K_call, T, qty=qty)]

# =========================
# Digitales
# =========================

def digital_bs(S0, K, T, r, q, sigma, option_type, payout=1.0):
    """Prix cash-or-nothing : paiement fixe à maturité si strictement dans la monnaie."""
    _, d2 = _d1_d2(S0, K, T, r, q, sigma)
    signe = 1 if option_type == "call" else -1
    return payout * np.exp(-r*T) * Ncdf(signe*d2)


def asset_or_nothing_bs(S0, K, T, r, q, sigma, option_type):
    """Prix asset-or-nothing : livraison de S_T si strictement dans la monnaie."""
    d1, _ = _d1_d2(S0, K, T, r, q, sigma)
    signe = 1 if option_type == "call" else -1
    return S0 * np.exp(-q*T) * Ncdf(signe*d1)


def grecs_digital_bs(S0, K, T, r, q, sigma, option_type, payout=1.0,
                     digital_type="cash"):
    """Grecques fermées : Vega/Rho pour 1 unité, Theta annuel = -dV/dT.

    Le gamma et le vega cash-or-nothing changent de signe selon le spot.
    Pour l'asset, j'utilise l'identité asset = vanille + K*cash (call),
    ou K*cash - vanille (put).
    """
    d1, d2 = _d1_d2(S0, K, T, r, q, sigma)
    signe = 1 if option_type == "call" else -1
    racine = np.sqrt(T)
    facteur = payout*np.exp(-r*T)
    densite = Npdf(d2)
    prix = facteur*Ncdf(signe*d2)
    derivee_t = (r-q-sigma**2/2)/(sigma*racine) - d2/(2*T)
    grecs = {
        "Delta": facteur*signe*densite/(S0*sigma*racine),
        "Gamma": -facteur*signe*densite*d1/(S0**2*sigma**2*T),
        "Vega": -facteur*signe*densite*d1/sigma,
        "Theta": r*prix-facteur*signe*densite*derivee_t,
        "Rho": -T*prix+facteur*signe*densite*racine/sigma,
    }
    if digital_type == "asset":
        cash = grecs_digital_bs(S0, K, T, r, q, sigma, option_type)
        vanille = grecs_primaires_bs(S0, K, T, r, q, sigma, option_type)
        return {cle: K*cash[cle]+signe*vanille[cle] for cle in cash}
    return grecs
