"""Mesures de volatilité à partir des cours historiques.

Fonctions disponibles : log-rendements, volatilité historique et glissante,
estimateur de Parkinson, série EWMA et demi-vie de ses poids.
Yang-Zhang et GARCH restent des emplacements à compléter (pass).

Les calculs renvoient des décimaux annualisés ; market_data_engine les
convertit en pourcentages pour l'API. Ce moteur ne contient aucune route Flask.
Les cours sont nettoyés par market_data_engine et λ est vérifié par
validation.py : les fonctions ci-dessous supposent des données propres.
"""

import numpy as np

ANNUALISATION = 252  # séances par an


# ============================================================
# 1. RENDEMENTS
# ============================================================

def log_returns(prices):
    """
    Calcule les log-rendements d'une série de prix.

    Formule :
        r_t = ln(S_t / S_{t-1})

    Les log-rendements servent ensuite de base au calcul de la
    volatilité historique, EWMA, GARCH, etc.
    """
    prices = np.asarray(prices, dtype=float)
    return np.log(prices[1:] / prices[:-1])


# ============================================================
# 2. VOLATILITÉ HISTORIQUE
# ============================================================

def historical_volatility(prices, annualization=ANNUALISATION):
    """
    Calcule la volatilité historique close-to-close.

    Étapes :
    1. calculer les log-rendements ;
    2. calculer leur écart-type ;
    3. annualiser la volatilité.

    Pour des données journalières, on utilise généralement :
        sigma_annuelle = sigma_journalière * sqrt(252)

    Renvoie :
        volatilité historique annualisée en décimal
        Exemple : 0.25 = 25 %
    """
    daily_volatility = np.std(log_returns(prices), ddof=1)
    return float(daily_volatility * np.sqrt(annualization))


def rolling_volatility(prices, window=20, annualization=ANNUALISATION):
    """
    Calcule une volatilité historique sur une fenêtre glissante.

    Exemple avec window=20 :
    à chaque date, on utilise uniquement les 20 derniers rendements.

    Cela permet de voir comment la volatilité évolue dans le temps
    au lieu d'obtenir une seule volatilité sur toute la période.

    Renvoie :
        série de len(rendements) - window + 1 volatilités annualisées
    """
    returns = log_returns(prices)
    rolling_vol = []

    for i in range(window, len(returns) + 1):
        # Sélection des derniers rendements
        window_returns = returns[i - window:i]
        # Écart-type sur la fenêtre, puis annualisation
        rolling_vol.append(np.std(window_returns, ddof=1) * np.sqrt(annualization))

    return np.array(rolling_vol)


def parkinson_volatility(high, low, annualization=ANNUALISATION):
    """
    Estimateur de volatilité de Parkinson.

    Contrairement au close-to-close, il utilise les plus hauts et
    plus bas observés pendant chaque séance.

    L'objectif est d'utiliser davantage d'information contenue dans
    les mouvements intraday.

    Limite importante :
    l'estimateur ne capture pas correctement les gaps overnight.
    """
    # L'amplitude logarithmique mesure les mouvements pendant la séance.
    ranges = np.log(np.asarray(high, dtype=float)) - np.log(np.asarray(low, dtype=float))
    daily_variance = np.mean(ranges ** 2) / (4 * np.log(2))
    return float(np.sqrt(daily_variance * annualization))


def yang_zhang_volatility(open_, high, low, close, annualization=ANNUALISATION):
    """
    Estimateur de volatilité de Yang-Zhang.

    Il combine plusieurs informations :
    - mouvement overnight ;
    - mouvement open-to-close ;
    - information high / low.

    Il est donc plus complet que le simple close-to-close
    et que Parkinson lorsque le sous-jacent présente des gaps.
    """
    pass


# ============================================================
# 3. EWMA / GARCH
# ============================================================

def ewma_half_life(lambda_):
    """
    Calcule la demi-vie associée au paramètre lambda d'un EWMA.

    La demi-vie correspond au nombre de périodes nécessaires
    pour que le poids d'une observation soit divisé par deux.

    Exemple :
        lambda = 0.94
        -> demi-vie d'environ 11 jours.
    """
    return float(np.log(0.5) / np.log(lambda_))


def ewma_volatility_series(returns, lambda_=0.94, annualization=ANNUALISATION):
    """Volatilité EWMA après chaque rendement, en décimal annualisé.

    Les observations récentes pèsent davantage :
        variance_t = lambda * variance_{t-1} + (1 - lambda) * r_t²
    Plus lambda est proche de 1, plus la mémoire est longue et la vol stable.

    Moyenne des rendements supposée nulle. Initialisation au carré du
    premier rendement : aucune observation future n'entre dans le calcul.
    La valeur datée t inclut le rendement t (mise à jour après clôture).
    """
    returns = np.asarray(returns, dtype=float)
    variance = returns[0] ** 2
    values = [np.sqrt(variance * annualization)]
    for daily_return in returns[1:]:
        # On conserve une partie du passé et on ajoute le choc de la séance.
        variance = lambda_ * variance + (1 - lambda_) * daily_return ** 2
        values.append(np.sqrt(variance * annualization))
    return np.asarray(values)


def garch_volatility(returns):
    """
    Estime une volatilité dynamique avec un modèle GARCH.

    Idée générale :
    la volatilité d'aujourd'hui dépend notamment :
    - de la volatilité passée ;
    - des chocs observés récemment.

    Cette fonction sera implémentée plus tard.
    """
    pass
