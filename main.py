"""Serveur Flask du pricer.

Rôle de chaque fichier :
- main.py (ici) : reçoit les requêtes du site, appelle validation.py,
  appelle le moteur et met en forme la réponse JSON ;
- validation.py : lit, convertit (% -> décimal) et vérifie les paramètres ;
- *_engine.py : les calculs, sur des paramètres déjà propres.

Lancement : python main.py puis http://127.0.0.1:5000
"""

import json
from collections import OrderedDict
from datetime import datetime, timezone
from io import BytesIO
from threading import RLock
from time import monotonic
from uuid import uuid4

import numpy as np
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.exceptions import HTTPException

from pricer_engine import (
    black_scholes, monte_carlo, longstaff_schwartz,
    grecs_primaires_bs, grecs_secondaires_bs,
    forward_price, forward_value, forward_payoff,
    payoff_strategie, greeks_strategie,
    make_covered_call, make_protective_put, make_straddle, make_strangle,
    make_call_spread, make_put_spread, make_collar,
    digital_bs, asset_or_nothing_bs, grecs_digital_bs, digital_monte_carlo,
)
from strutu_engine import (
    analyse_phoenix, analyse_autocall_plain,
    calculer_remboursement_phoenix_maturite, calculer_remboursement_autocall_maturite,
)
from accumulator_engine import analyse_accumulation, strike_zero_cout
from dra_engine import analyse_dra, sensibilite_correlation
from market_data_engine import search_tickers, get_cotation, get_vix, get_vol_historique, get_volatility_snapshot
import validation as v

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 2_000_000   # un formulaire fait quelques kilo-octets


# =========================
# Fonctions utiles partout
# =========================

def _err(message, code=400):
    """Renvoie une erreur au site (affichée en rouge sous le bouton)."""
    return jsonify({"error": message}), code


def _grid(centres, n_points=121, low=0.4, high=1.6, sauts=()):
    """Grille de prix finaux S_T pour les graphes.

    Je vais de min(centres) x low à max(centres) x high, avec n_points points.
    sauts : niveaux où le payoff fait une marche (strike d'une digitale,
    barrières d'un structuré). J'ajoute le niveau et ses deux voisins
    flottants immédiats pour que le graphe montre une vraie marche verticale.
    """
    points = list(np.linspace(max(0.01, min(centres) * low), max(centres) * high, n_points))
    for x in sauts:
        points.extend([np.nextafter(x, -np.inf), x, np.nextafter(x, np.inf)])
    return np.unique(points)


def _find_breakevens(ST_grid, pnl):
    """Trouve les breakevens : les points où le P&L change de signe.

    Il peut y en avoir plusieurs (straddle, strangle...). J'interpole à la main
    parce que np.interp veut des valeurs croissantes, et le P&L peut très bien
    descendre : ça me donnait un résultat faux sans message d'erreur.
    """
    breakevens = []
    signs = np.sign(pnl)
    for i in range(len(signs) - 1):
        if signs[i] != signs[i + 1] and signs[i] != 0 and signs[i + 1] != 0:
            be = ST_grid[i] + (0 - pnl[i]) * (ST_grid[i + 1] - ST_grid[i]) / (pnl[i + 1] - pnl[i])
            breakevens.append(round(float(be), 2))
    return breakevens


def _arrondi(valeurs, chiffres=4):
    """Arrondit une liste (ou un tableau numpy) pour alléger le JSON."""
    return [round(float(x), chiffres) for x in valeurs]


def _pct(x):
    """Fraction du nominal -> pourcentage arrondi (0.9712 -> 97.12)."""
    return None if x is None else round(100 * float(x), 4)


def _reponse_graphe(ST_grid, payoff, pnl, breakevens=True):
    """Partie commune des réponses avec un graphe payoff / P&L."""
    reponse = {"ST_grid": _arrondi(ST_grid), "payoff": _arrondi(payoff), "pnl": _arrondi(pnl)}
    if breakevens:
        reponse["breakevens"] = _find_breakevens(ST_grid, pnl)
    return reponse


def _reponse_structure(res, nominal):
    """Indicateurs communs au Phoenix et à l'Autocall : montants en devise, _pct en %."""
    return {
        "prix": round(res["prix"] * nominal, 4),
        "prix_pct": _pct(res["prix"]),
        "erreur_type": round(res["erreur_type"] * nominal, 6),
        "ic95_pct": [_pct(x) for x in res["ic95"]],
        "marge_emetteur_pct": _pct(res["marge_emetteur"]),
        "duree_de_vie_moyenne": round(res["duree_de_vie_moyenne"], 4),
        "coupons_moyens_actualises_pct": _pct(res["coupons_moyens_actualises"]),
        "obs_dates": res["obs_dates"],
        "proba_rappel_par_date_pct": [_pct(p) for p in res["proba_rappel_par_date"]],
        "proba_maturite_pct": _pct(res["proba_maturite"]),
        "proba_perte_capital_pct": _pct(res["proba_perte_capital"]),
        "remboursement_moyen_si_perte_pct": _pct(res["remboursement_moyen_si_perte"]),
        "flux_attendus_par_date": [x * nominal for x in res["flux_attendus_par_date"]],
        "flux_attendus_actualises_par_date": [x * nominal for x in res["flux_attendus_actualises_par_date"]],
    }


# =========================
# Erreurs : un seul endroit pour toutes les routes
# =========================

@app.errorhandler(ValueError)
def erreur_parametre(erreur):
    """Toute ValueError (validation.py ou moteur) devient une erreur 400 lisible."""
    return _err(str(erreur))


@app.errorhandler(Exception)
def erreur_inattendue(erreur):
    """Erreur imprévue : message générique, sans détail Python interne (500).

    Les erreurs HTTP de Flask (404, 405, 413...) gardent leur code.
    """
    if isinstance(erreur, HTTPException):
        if erreur.code == 413:
            return _err("Requête limitée à 2 Mo.", 413)
        return _err(erreur.description, erreur.code)
    return _err("Le calcul a échoué. Vérifiez les paramètres et réessayez.", 500)


# =========================
# Page du site et configuration partagée
# =========================

@app.get("/")
def index():
    """Route GET / : affiche la page du site."""
    return render_template("index.html")


@app.get("/api/config")
def api_config():
    """Bornes lues par le site : la règle des strikes n'est écrite qu'une fois."""
    return jsonify({"moneyness_min": v.MONEYNESS_MIN, "moneyness_max": v.MONEYNESS_MAX})


# =========================
# Données de marché (recherche ticker + spot, vol)
# =========================

@app.get("/api/search_ticker")
def api_search_ticker():
    """Route GET /api/search_ticker?q=... : suggestions de tickers (liste vide si Yahoo ne répond pas)."""
    query = request.args.get("q", "").strip()
    return jsonify({"results": search_tickers(query) if query else []})


@app.get("/api/market/<ticker>")
def api_market(ticker):
    """Route GET /api/market/<ticker> : spot, devise, VIX et vol historique.

    Si la vol historique n'est pas dispo (ticker récent), je renvoie None et
    le site se rabat sur le VIX.
    """
    try:
        cotation = get_cotation(ticker)
        vix = get_vix()
    except Exception as e:
        return _err(f"Impossible de récupérer les données pour '{ticker}' : {e}")
    try:
        vol_hist = round(get_vol_historique(ticker), 4)
    except Exception:
        vol_hist = None
    return jsonify({"S0": round(cotation["S0"], 4), "devise": cotation["devise"],
                    "vix": round(vix, 4), "vol_hist": vol_hist})


@app.get("/api/volatility")
def api_volatility():
    """Volatilités historique, glissantes, EWMA et Parkinson d'un sous-jacent.

    Exemple : /api/volatility?ticker=AAPL&periode=1y&lambda=0.94
    """
    ticker, periode, lambda_ = v.parametres_volatilite(request.args)
    return jsonify(get_volatility_snapshot(ticker, periode, lambda_))


# =========================
# Option européenne / américaine
# =========================

@app.post("/api/option")
def api_option():
    """Prix, grecques et graphe d'une option.

    Européenne : Black-Scholes ou Monte Carlo (avec IC95). Américaine : Longstaff-Schwartz.
    """
    p = v.parametres_option(request.get_json())
    marche = (p["S0"], p["K"], p["T"], p["r"], p["q"], p["sigma"], p["option_type"])

    reponse = {}
    if p["style"] == "american":
        price, used = longstaff_schwartz(*marche, p["n_simulations"], seed=p["seed"]), "longstaff_schwartz"
    elif p["method"] == "monte_carlo":
        reponse["monte_carlo"] = monte_carlo(*marche, p["n_simulations"], seed=p["seed"])
        price, used = reponse["monte_carlo"]["price"], "monte_carlo"
    else:
        price, used = black_scholes(*marche), "black_scholes"

    # grecques Black-Scholes (approximation pour l'américaine)
    sign = 1 if p["side"] == "buy" else -1
    ST_grid = _grid([p["S0"], p["K"]])
    payoff = sign * (np.maximum(ST_grid - p["K"], 0) if p["option_type"] == "call"
                     else np.maximum(p["K"] - ST_grid, 0))
    pnl = payoff - sign * price  # j'enlève la prime payée (ou je l'ajoute si vendeur)

    return jsonify({
        **reponse,
        "price": round(float(price), 4),
        "model_used": used,
        "primaires": {k: round(float(x), 6) for k, x in grecs_primaires_bs(*marche).items()},
        "secondaires": {k: round(float(x), 6) for k, x in grecs_secondaires_bs(*marche).items()},
        **_reponse_graphe(ST_grid, payoff, pnl),
    })


@app.post("/api/digital")
def api_digital():
    """Prix Black-Scholes et Monte Carlo d'une digitale européenne."""
    p = v.parametres_digitale(request.get_json())
    marche = (p["S0"], p["K"], p["T"], p["r"], p["q"], p["sigma"], p["option_type"])
    prix = digital_bs(*marche, p["payout"]) if p["digital_type"] == "cash" else asset_or_nothing_bs(*marche)
    mc = digital_monte_carlo(*marche, p["n_simulations"], payout=p["payout"],
                             seed=p["seed"], digital_type=p["digital_type"])
    grecs = grecs_digital_bs(*marche, p["payout"], p["digital_type"])
    signe = 1 if p["side"] == "buy" else -1
    grille = _grid([p["S0"], p["K"]], sauts=[p["K"]])
    condition = grille > p["K"] if p["option_type"] == "call" else grille < p["K"]
    payoff = signe*condition*(p["payout"] if p["digital_type"] == "cash" else grille)
    return jsonify({"price": float(prix), "monte_carlo": mc, "model_used": "black_scholes",
                    "primaires": {cle: float(signe*x) for cle, x in grecs.items()},
                    "secondaires": {}, "breakevens": [], "ST_grid": grille.tolist(),
                    "payoff": payoff.tolist(), "pnl": (payoff-signe*prix).tolist(),
                    "strikes": [p["K"]], "digital_type": p["digital_type"]})


# =========================
# Forward / Future
# =========================

@app.post("/api/forward")
def api_forward():
    """Prix à terme, valeur du contrat et graphe."""
    p = v.parametres_forward(request.get_json())
    valeur = forward_value(p["S0"], p["K"], p["T"], p["r"], p["q"], p["side"])
    ST_grid = _grid([p["S0"], p["K"]])
    payoff = forward_payoff(ST_grid, p["K"], p["side"])
    # pas de prime pour un forward, le P&L c'est payoff - valeur actuelle du contrat
    return jsonify({
        "forward_price": round(float(forward_price(p["S0"], p["T"], p["r"], p["q"])), 4),
        "valeur_actuelle": round(float(valeur), 4),
        **_reponse_graphe(ST_grid, payoff, payoff - valeur),
    })


# =========================
# Stratégies
# =========================

# pour chaque stratégie, la fonction qui me construit ses jambes
# (les strikes à lire sont dans validation.STRIKES_STRATEGIE)
STRATEGY_BUILDERS = {
    "covered_call":   lambda s, p: make_covered_call(s["K"], p["T"], qty=p["qty"]),
    "protective_put": lambda s, p: make_protective_put(s["K"], p["T"], qty=p["qty"]),
    "straddle":       lambda s, p: make_straddle(s["K"], p["T"], side=p["side"], qty=p["qty"]),
    "strangle":       lambda s, p: make_strangle(s["K_put"], s["K_call"], p["T"], side=p["side"], qty=p["qty"]),
    "call_spread":    lambda s, p: make_call_spread(s["K1"], s["K2"], p["T"], qty=p["qty"]),
    "put_spread":     lambda s, p: make_put_spread(s["K1"], s["K2"], p["T"], qty=p["qty"]),
    "collar":         lambda s, p: make_collar(s["K_put"], s["K_call"], p["T"], qty=p["qty"]),
}


@app.post("/api/strategy")
def api_strategy():
    """Payoff, P&L et grecques d'une stratégie."""
    p = v.parametres_strategie(request.get_json())
    legs = STRATEGY_BUILDERS[p["strategie"]](p["strikes"], p)
    ST_grid = _grid([p["S0"], *p["strikes"].values()])
    resultat = payoff_strategie(legs, p["S0"], p["r"], p["q"], p["sigma"], ST_grid)
    grecques = greeks_strategie(legs, p["S0"], p["r"], p["q"], p["sigma"])
    return jsonify({
        "greeks": {k: round(float(x), 6) for k, x in grecques.items()},
        **_reponse_graphe(ST_grid, resultat["payoff"], resultat["pnl"]),
        "n_legs": len(legs),
    })


# =========================
# Produits structurés : Phoenix
# =========================

@app.post("/api/phoenix")
def api_phoenix():
    """Prix, stats des scénarios et graphe du Phoenix.

    Montants en devise du nominal, et en % du nominal pour les champs en _pct.
    """
    p = v.parametres_phoenix(request.get_json())
    nominal = p.pop("nominal")
    res = analyse_phoenix(**p)

    # je passe les barrières en prix pour les tracer sur le graphe
    strike_reference = p["strike_pct"] * p["S0"]
    niveaux = {
        "strike": strike_reference,
        "autocall": p["K_auto"] * strike_reference if p["has_autocall"] else None,
        "coupon": p["B_phoenix"] * strike_reference,
        "protection": p["B_pdi"] * strike_reference,
    }
    seuils = [x for x in niveaux.values() if x is not None]
    ST_grid = _grid([p["S0"], *seuils], n_points=241, low=0.6, high=1.3, sauts=seuils)

    # payoff à maturité si pas rappelé
    payoff = calculer_remboursement_phoenix_maturite(
        ST_grid, p["S0"], p["B_phoenix"], p["B_pdi"], res["coupon_schedule"][-1],
        p["strike_pct"], p["bonus_threshold"], p["bonus_amount"])["total"] * nominal
    # P&L du client : il a payé le nominal (100 %), pas le prix théorique
    return jsonify({
        **_reponse_structure(res, nominal),
        "barrieres_rappel_pct": [x*100 for x in res["barrieres_rappel"]] if p["has_autocall"] else None,
        "coupon_schedule_pct": [_pct(c) for c in res["coupon_schedule"]],
        "niveaux": {k: (None if x is None else round(float(x), 4)) for k, x in niveaux.items()},
        **_reponse_graphe(ST_grid, payoff, payoff - nominal, breakevens=False),
    })


# =========================
# Autocall Plain : rappel anticipé optionnel
# =========================

@app.post("/api/autocall")
def api_autocall():
    """Entrées du formulaire en %, calculs en décimal, montants en devise du nominal."""
    p = v.parametres_autocall(request.get_json())
    nominal = p.pop("nominal")
    res = analyse_autocall_plain(**p)

    # Graphe du remboursement final, dans le cas sans rappel antérieur.
    S0, K_auto, B_pdi = p["S0"], p["K_auto"], p["B_pdi"]
    niveaux = {"autocall": K_auto * S0 if p["has_autocall"] else None,
               "coupon": K_auto * S0,
               "protection": None if B_pdi is None else B_pdi * S0}
    seuils = [x for x in niveaux.values() if x is not None]
    grille = _grid([S0, *seuils], n_points=241, low=0.6, high=1.3, sauts=seuils)
    remboursement_final = calculer_remboursement_autocall_maturite(
        grille, S0, K_auto, B_pdi, p["coupon"] * len(p["obs_dates"]))["total"] * nominal
    return jsonify({
        **_reponse_structure(res, nominal),
        "ST_grid": grille.tolist(),
        "payoff": remboursement_final.tolist(),
        "pnl": (remboursement_final - nominal).tolist(),
        "niveaux": niveaux,
        "produit": "autocall_plain",
        "has_autocall": p["has_autocall"],
        "barrieres_rappel_pct": [x*100 for x in res["barrieres_rappel"]] if p["has_autocall"] else None,
        "nombre_moyen_coupons": round(res["nombre_moyen_coupons"], 4),
        "nominal": nominal, "S0": S0,
        "coupon_pct": p["coupon"] * 100, "K_auto_pct": K_auto * 100,
        "B_pdi_pct": None if B_pdi is None else B_pdi * 100,
        "first_call_date": p["first_call_date"], "n_simulations": p["n_simulations"], "seed": p["seed"],
    })


# =========================
# Accumulateur / décumulateur
# =========================

@app.post("/api/accumulateur")
@app.post("/api/decumulateur")
def api_accumulation():
    """Valeur (K donné) ou strike zéro coût ; taux en %, niveaux K et B_KO absolus.

    Une seule fonction pour deux routes : le produit est déduit de l'URL appelée.
    """
    produit = "decumulateur" if request.path.endswith("decumulateur") else "accumulateur"
    p = v.parametres_accumulation(request.get_json(), produit)
    mode, K = p.pop("mode"), p.pop("K")
    if mode == "zero_cout":
        resultat = strike_zero_cout(produit, **p)
    else:
        resultat = {**analyse_accumulation(produit, K=K, **p), "K": K}
    return jsonify({**resultat, "produit": produit, "mode": mode, "seed": p["seed"],
                    "ko_inclut_date": p["ko_inclut_date"], "n_garanties": p["n_garanties"]})


# =========================
# Dual Range Accrual
# =========================

@app.post("/api/dra")
def api_dra():
    """Marché et coupon en % ; rho en décimal ; fourchettes absolues."""
    arguments, rho, sensibilite = v.parametres_dra(request.get_json())
    resultat = analyse_dra(**arguments, rho=rho)
    if sensibilite:
        resultat["sensibilite_correlation"] = sensibilite_correlation(**arguments)
    return jsonify(resultat)


# =========================
# Contrôle d'entrée et instantanés pour l'export PDF
# =========================

_calculs = OrderedDict()
_verrou = RLock()
_DUREE_DE_VIE = 1800   # secondes
_NB_MAX_CALCULS = 20
_pricing_paths = {'/api/option', '/api/digital', '/api/forward', '/api/strategy', '/api/phoenix',
                  '/api/autocall', '/api/dra', '/api/accumulateur', '/api/decumulateur'}


@app.before_request
def verifier_json_api():
    """Toute requête POST vers l'API doit contenir un objet JSON.

    Le contenu (nombres, bornes, types) est vérifié par validation.py.
    """
    if request.method != 'POST' or not request.path.startswith('/api/'):
        return
    if not request.is_json:
        return _err('Un objet JSON est requis.', 415)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _err('Un objet JSON valide est requis.')
    if data.get('vol_source') not in (None, 'manual', 'hist', 'vix'):
        return _err('Source de volatilité inconnue : utilisez une saisie, un historique ou le VIX.')
    if 'leg_vols' in data:
        return _err('La stratégie utilise une volatilité constante commune à ses jambes.')


@app.after_request
def memoriser_calcul(response):
    """Contrôle de sortie (aucun NaN/inf) et mémorisation du calcul pour le PDF."""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store'
    if response.is_json and response.status_code < 400:
        data = response.get_json()
        try:
            json.dumps(data, allow_nan=False)
        except (ValueError, TypeError):
            response.set_data(app.json.dumps({'error': 'Résultat numérique non fini : vérifiez les paramètres.'}))
            response.status_code = 500
            return response
        if request.path in _pricing_paths:
            ident = uuid4().hex
            with _verrou:
                now = monotonic()
                for key in list(_calculs):
                    if now - _calculs[key]['clock'] > _DUREE_DE_VIE:
                        del _calculs[key]
                _calculs[ident] = {'clock': now, 'date': datetime.now(timezone.utc).isoformat(),
                                   'route': request.path, 'parametres': request.get_json(), 'resultats': data}
                while len(_calculs) > _NB_MAX_CALCULS:
                    _calculs.popitem(last=False)
            data['calculation_id'] = ident
            response.set_data(app.json.dumps(data))
    return response


@app.post('/api/export_pdf')
def api_export_pdf():
    """PDF du calcul mémorisé : aucun recalcul, donc identique à l'écran."""
    ident = request.get_json().get('calculation_id', '')
    with _verrou:
        snapshot = _calculs.get(ident) if isinstance(ident, str) else None
        if snapshot is None or monotonic() - snapshot['clock'] > _DUREE_DE_VIE:
            return _err('Calcul expiré ou serveur redémarré : relancez le calcul.', 410)
    from pdf_engine import generer_pdf_calcul
    try:
        contenu = generer_pdf_calcul(snapshot)
    except ImportError:   # reportlab est importé à la demande dans pdf_engine
        return _err('Installez les dépendances : pip install -r requirements.txt', 503)
    return send_file(BytesIO(contenu), mimetype='application/pdf', as_attachment=True,
                     download_name='dernier_calcul.pdf')


if __name__ == "__main__":
    app.run(host="127.0.0.1", debug=False, use_reloader=False)
