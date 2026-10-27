#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MARKET DATA ENGINE

Récupération des données financières nécessaires
aux différents moteurs de pricing (Yahoo Finance).

C'est ici, et seulement ici, que les cours sont contrôlés
(dates triées, pas de doublon, cours positifs et finis) :
volatility_engine reçoit des données propres.

Created on Wed Oct 14 10:59:13 2026
@author: theojondeau
"""

import numpy as np
import yfinance as yf

from volatility_engine import (
    historical_volatility, rolling_volatility, log_returns,
    parkinson_volatility, ewma_volatility_series, ewma_half_life, ANNUALISATION,
)

# ==========================================
# 1. RECHERCHE DES SOUS-JACENTS
# ==========================================

def search_tickers(query, max_results=8):
    """Cherche des tickers sur Yahoo Finance (barre de recherche du site).

    Renvoie : une liste de dicts {symbol, name, exchange}, ou une liste vide
    si Yahoo ne répond pas.
    """
    try:
        resultats = yf.Search(query, max_results=max_results).quotes
    except Exception:
        return []  # si yahoo répond pas je renvoie juste rien

    return [
        {
            "symbol": r.get("symbol"),
            "name": r.get("shortname") or r.get("longname") or r.get("symbol"),
            "exchange": r.get("exchange"),
        }
        for r in resultats
        if r.get("symbol")
    ]

# ==========================================
# 2. COTATION : SPOT, DEVISE, VIX
# ==========================================

def get_cotation(ticker):
    """Spot et devise en une seule requête Yahoo."""
    infos = yf.Ticker(ticker).fast_info
    return {"S0": float(infos["last_price"]), "devise": infos["currency"]}


def get_vix():
    """VIX : volatilité implicite à 30 jours du S&P 500, en %."""
    return float(yf.Ticker("^VIX").fast_info["last_price"])


# ==========================================
# 3. HISTORIQUE CONTRÔLÉ (commun à toutes les mesures)
# ==========================================

def _telecharger_historique(ticker, periode):
    """Télécharge l'historique OHLC ajusté, trié par date et sans doublon.

    auto_adjust=True : prix ajustés des dividendes et des splits, sinon un split
    4-pour-1 ressemblerait à une chute de -75 % en une séance.
    """
    data = yf.Ticker(ticker).history(period=periode, auto_adjust=True)
    if data.empty:
        raise ValueError(f"Aucune donnée historique disponible pour {ticker}.")
    data = data.sort_index()
    if data.index.has_duplicates:
        raise ValueError("L'historique contient des dates dupliquées.")
    return data


def _verifier_cours(*series):
    """Tous les cours doivent être finis et strictement positifs (sinon log(0) = -inf)."""
    for serie in series:
        if not np.all(np.isfinite(serie)) or np.any(serie <= 0):
            raise ValueError("L'historique contient des cours manquants, nuls ou non finis.")


# ==========================================
# 4. VOLATILITÉ HISTORIQUE (onglets de pricing)
# ==========================================

def get_vol_historique(ticker, periode="1y"):
    """Volatilité historique close-to-close annualisée, en pourcentage.

    Utilisée par /api/market pour pré-remplir σ dans les onglets de pricing.
    """
    close = _telecharger_historique(ticker, periode)["Close"].dropna().to_numpy(dtype=float)
    _verifier_cours(close)
    if len(close) < 20:
        raise ValueError("Pas assez d'historique pour calculer une volatilité.")
    return historical_volatility(close) * 100


# ==========================================
# 5. LABORATOIRE DE VOLATILITÉ (onglet Chantier)
# ==========================================

def get_volatility_snapshot(ticker, periode="1y", lambda_=0.94):
    """Toutes les mesures sur un seul historique OHLC ajusté.

    Un seul téléchargement garantit les mêmes séances pour toutes les mesures.
    Moteurs : décimaux ; réponse destinée à l'API : pourcentages.
    Parkinson scalaire couvre toute la période ; sa courbe couvre 20 séances.
    Les quatre courbes démarrent à la première fenêtre complète de 60 rendements.
    """
    data = _telecharger_historique(ticker, periode)
    if not {"Close", "High", "Low"}.issubset(data.columns):
        raise ValueError("Les cours de clôture, plus hauts et plus bas sont nécessaires.")
    close = data["Close"].to_numpy(dtype=float)
    high = data["High"].to_numpy(dtype=float)
    low = data["Low"].to_numpy(dtype=float)
    _verifier_cours(close, high, low)
    if np.any(high < low) or np.any(close < low) or np.any(close > high):
        raise ValueError("Les cours OHLC sont incohérents.")
    if len(close) < 61:
        raise ValueError("Il faut au moins 61 séances pour la fenêtre de 60 rendements.")

    vol_hist = historical_volatility(close)
    vol_20 = rolling_volatility(close, window=20)[40:]
    vol_60 = rolling_volatility(close, window=60)
    ewma = ewma_volatility_series(log_returns(close), lambda_)[59:]
    parkinson = parkinson_volatility(high, low)
    # Chaque point utilise 20 amplitudes, jusqu'à la séance affichée incluse.
    parkinson_20 = [parkinson_volatility(high[i-19:i+1], low[i-19:i+1]) for i in range(60, len(close))]

    def percent_series(series):
        return [round(float(value * 100), 4) for value in series]

    return {
        "ticker": ticker, "periode": periode,
        "vol_historique": round(vol_hist * 100, 4),
        "vol_20": round(float(vol_20[-1] * 100), 4),
        "vol_60": round(float(vol_60[-1] * 100), 4),
        "vol_ewma": round(float(ewma[-1] * 100), 4),
        "vol_parkinson": round(parkinson * 100, 4),
        "lambda": lambda_, "ewma_half_life": round(ewma_half_life(lambda_), 2),
        "dates": [date.strftime("%Y-%m-%d") for date in data.index[60:]],
        "series_20": percent_series(vol_20), "series_60": percent_series(vol_60),
        "series_ewma": percent_series(ewma), "series_parkinson_20": percent_series(parkinson_20),
        "annualization": ANNUALISATION, "observations": len(close),
        "start_date": data.index[0].strftime("%Y-%m-%d"),
        "end_date": data.index[-1].strftime("%Y-%m-%d"),
    }
