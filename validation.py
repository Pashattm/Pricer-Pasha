"""Lecture, conversion et vérification des paramètres envoyés par le site.

C'est le SEUL endroit du projet où les paramètres sont contrôlés :
- main.py appelle une fonction parametres_xxx(data) par produit ;
- chaque fonction lit le JSON, convertit les % en décimal, vérifie tout,
  et renvoie un dictionnaire propre ;
- les moteurs reçoivent ce dictionnaire et ne revérifient rien.

Toute erreur lève une ValueError avec un message en français ;
main.py la transforme en réponse 400 affichée en rouge sous le bouton.
Les bornes sont toutes regroupées ci-dessous : une seule valeur à changer.
"""

import numpy as np

from strutu_engine import barrieres_de_rappel
from dra_engine import n_constats_dra


# =========================
# Bornes communes
# =========================

SPOT_MAX = 1e9            # spot, strike, niveaux absolus
NOMINAL_MAX = 1e12
TAUX_MAX = 0.5            # |r| ≤ 50 %
DIVIDENDE_MAX = 0.5       # 0 ≤ q ≤ 50 %
SIGMA_MAX = 2.0           # 200 % de vol
MATURITE_MAX = 30.0       # en années
COUPON_MAX = 1.0          # 100 % du nominal sur une période
BARRIERE_MAX = 3.0        # 300 % du niveau de référence
MONEYNESS_MIN = 0.5       # strike entre 50 % ...
MONEYNESS_MAX = 1.5       # ... et 150 % du spot
N_SIM_MIN = 1_000         # en dessous le prix Monte Carlo bouge trop
N_SIM_MAX = 500_000       # au-dessus ça rame
N_SIM_MAX_DRA = 200_000   # constats quotidiens : plus lourd
CONSTATS_MAX = 6_000_000  # simulations × dates d'observation
CONSTATS_MAX_DRA = 60_000_000
BUDGET_DRA_SENSIBILITE = 250_000_000  # 8 calculs : 1 prix + 7 corrélations
FREQUENCES = (1, 2, 4, 12)            # observations par an
FREQUENCE_DEFAUT = 2
GRAINE_MAX = 2**32 - 1
PERIODES_VOLATILITE = ("6mo", "1y", "2y", "5y")

# Strikes nécessaires à chaque stratégie (sert aussi à centrer le graphe dans main.py)
STRIKES_STRATEGIE = {
    "covered_call":   ["K"],
    "protective_put": ["K"],
    "straddle":       ["K"],
    "strangle":       ["K_put", "K_call"],
    "call_spread":    ["K1", "K2"],
    "put_spread":     ["K1", "K2"],
    "collar":         ["K_put", "K_call"],
}


# =========================
# Lecture des champs du JSON
# =========================

def _vers_nombre(valeur, nom):
    """Convertit une valeur JSON en float fini.

    Pièges Python évités : True est un int (on le refuse), float("nan") passe
    le test "x <= 0" (NaN <= 0 vaut False), donc on teste np.isfinite.
    """
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float, str)):
        raise ValueError(f"{nom} doit être un nombre.")
    try:
        x = float(valeur)
    except ValueError:
        raise ValueError(f"{nom} doit être un nombre.") from None
    if not np.isfinite(x):
        raise ValueError(f"{nom} doit être un nombre fini.")
    return x


def nombre(data, cle, defaut=None):
    """Champ numérique obligatoire (ou valeur par défaut si absent ou vide).

    Un <input> vide envoie "", pas null : les deux comptent comme absents.
    """
    valeur = data.get(cle, defaut)
    if valeur is None or valeur == "":
        valeur = defaut
    if valeur is None:
        raise ValueError(f"Paramètre manquant : {cle}.")
    return _vers_nombre(valeur, cle)


def pourcentage(data, cle, defaut=None):
    """Champ saisi en % dans le formulaire, renvoyé en décimal (5 -> 0.05)."""
    return nombre(data, cle, defaut) / 100


def optionnel(data, cle):
    """Champ numérique facultatif : None s'il est absent ou vide."""
    valeur = data.get(cle)
    return None if valeur is None or valeur == "" else _vers_nombre(valeur, cle)


def entier(data, cle, defaut=None):
    """Refuse 50000.5 au lieu de le tronquer silencieusement en 50000."""
    x = nombre(data, cle, defaut)
    if not x.is_integer():
        raise ValueError(f"{cle} doit être entier.")
    return int(x)


def booleen(data, cle, defaut):
    """Convertit un true/false venu du JSON en vrai booléen.

    Attention : bool("false") donne True en Python, d'où cette fonction.
    """
    valeur = data.get(cle, defaut)
    if isinstance(valeur, bool):
        return valeur
    if isinstance(valeur, str) and valeur.strip().lower() in ("true", "1", "oui", "yes"):
        return True
    if isinstance(valeur, str) and valeur.strip().lower() in ("false", "0", "non", "no"):
        return False
    if valeur in (0, 1):
        return bool(valeur)
    raise ValueError(f"{cle} doit valoir true ou false (reçu : {valeur!r}).")


def choix(data, cle, options, defaut=None, libelle=None):
    """Champ texte qui doit appartenir à une liste fermée."""
    valeur = data.get(cle, defaut)
    if valeur not in options:
        raise ValueError(f"{libelle or cle} invalide : valeurs possibles {', '.join(options)}.")
    return valeur


# =========================
# Contrôles communs
# =========================

def verifier_marche(S0, T, r, q, sigma=None, sigma_nulle=False):
    """Limites de marché communes à tous les produits (valeurs déjà en décimal)."""
    if not 0 < S0 <= SPOT_MAX:
        raise ValueError("Le spot doit être strictement positif et ≤ 10⁹.")
    if not 0 < T <= MATURITE_MAX:
        raise ValueError("La maturité doit être comprise entre 0 et 30 ans.")
    if abs(r) > TAUX_MAX:
        raise ValueError("Le taux doit être compris entre −50 % et 50 %.")
    if not 0 <= q <= DIVIDENDE_MAX:
        raise ValueError("Le dividende doit être compris entre 0 et 50 %.")
    if sigma is not None and not (0 <= sigma <= SIGMA_MAX and (sigma > 0 or sigma_nulle)):
        raise ValueError("La volatilité doit être strictement positive et ≤ 200 %."
                         if not sigma_nulle else "La volatilité doit être comprise entre 0 et 200 %.")


def verifier_strikes(S0, strikes):
    """Chaque strike entre 50 % et 150 % du spot.

    Un strike à 100 pour un spot à 300 se price, mais n'a pas de sens pédagogique.
    Le site lit ces bornes via /api/config : une seule source de vérité.
    """
    lo, hi = MONEYNESS_MIN * S0, MONEYNESS_MAX * S0
    for K in strikes:
        if not lo <= K <= hi:
            raise ValueError(f"Strike {K:.2f} trop éloigné du spot {S0:.2f} : "
                             f"il doit être entre {lo:.2f} et {hi:.2f} "
                             f"({MONEYNESS_MIN:.0%} à {MONEYNESS_MAX:.0%} du spot).")


def simulations(data, defaut, maximum=N_SIM_MAX):
    """Nombre de simulations : entier entre 1 000 et le maximum du produit."""
    n = entier(data, "n_simulations", defaut)
    if not N_SIM_MIN <= n <= maximum:
        raise ValueError(f"Le nombre de simulations doit être un entier entre "
                         f"{N_SIM_MIN:,} et {maximum:,}.".replace(",", " "))
    return n


def graine(data):
    """Graine du générateur aléatoire : même graine = mêmes tirages = même prix."""
    seed = entier(data, "seed", 42)
    if not 0 <= seed <= GRAINE_MAX:
        raise ValueError("La graine doit être un entier entre 0 et 4 294 967 295.")
    return seed


def dates_observation(T, frequence):
    """Construit [1/f, 2/f, ..., T] : dates forcément positives et croissantes.

    T doit tomber pile sur un nombre entier de périodes. Tolérance 1e-9 car
    2,5 × 2 peut donner 4,999999999 en flottant.
    """
    if frequence not in FREQUENCES:
        raise ValueError("La fréquence doit être 1 (annuelle), 2, 4 ou 12 (mensuelle).")
    n_obs = T * frequence
    if abs(n_obs - round(n_obs)) > 1e-9:
        raise ValueError(f"Avec une fréquence de {frequence}/an, la maturité doit être un "
                         f"multiple de {1 / frequence:g} an (reçu : T = {T}).")
    return [k / frequence for k in range(1, int(round(n_obs)) + 1)]


def verifier_constats(n_simulations, n_dates, maximum=CONSTATS_MAX):
    """Plafond mémoire / temps : simulations × dates d'observation."""
    if n_simulations * n_dates > maximum:
        raise ValueError(f"Calcul trop volumineux : réduisez les simulations ou la fréquence "
                         f"(maximum {maximum:,} constats simulés).".replace(",", " "))


def step_down(data, K_auto, n_dates, actif, minimum=0.0):
    """Baisse de la barrière de rappel à chaque constat (en décimal), 0 sans rappel.

    Les barrières de rappel possibles (toutes sauf la maturité) doivent rester
    positives et, pour le Phoenix, au-dessus de la barrière coupon.
    """
    baisse = pourcentage(data, "step_down", 0)
    if baisse < 0:
        raise ValueError("Le step-down doit être positif ou nul.")
    if not actif:
        return 0.0
    niveaux = barrieres_de_rappel(K_auto, baisse, max(2, n_dates))[:max(1, n_dates - 1)]
    if np.any(niveaux <= 0) or np.any(niveaux < minimum):
        raise ValueError("Les seuils de rappel doivent rester positifs et au-dessus du seuil coupon Phoenix.")
    return baisse


def _marche(data, sigma_nulle=False):
    """Lit et vérifie S0, T, r, q, sigma (formulaire en %, renvoyé en décimal)."""
    S0, T = nombre(data, "S0"), nombre(data, "T")
    r, q, sigma = pourcentage(data, "r"), pourcentage(data, "q", 0), pourcentage(data, "sigma")
    verifier_marche(S0, T, r, q, sigma, sigma_nulle)
    return S0, T, r, q, sigma


def _nominal(data):
    nominal = nombre(data, "nominal", 1000)
    if not 0 < nominal <= NOMINAL_MAX:
        raise ValueError("Le montant investi doit être strictement positif et ≤ 10¹².")
    return nominal


def _sens(data):
    return choix(data, "side", ("buy", "sell"), "buy", "Sens")


# =========================
# Un contrôle par produit
# =========================

def parametres_option(data):
    """Option européenne (Black-Scholes ou Monte Carlo) ou américaine (Longstaff-Schwartz)."""
    S0, T, r, q, sigma = _marche(data)
    K = nombre(data, "K")
    verifier_strikes(S0, [K])
    return dict(S0=S0, K=K, T=T, r=r, q=q, sigma=sigma,
                option_type=choix(data, "option_type", ("call", "put"), libelle="Type d'option"),
                style=choix(data, "style", ("european", "american"), libelle="Style d'exercice"),
                method=choix(data, "method", ("black_scholes", "monte_carlo", "longstaff_schwartz"),
                              "black_scholes", "Méthode"),
                side=_sens(data), n_simulations=simulations(data, 30000), seed=graine(data))


def parametres_forward(data):
    """Forward : pas de volatilité, le prix contractuel K doit être positif."""
    S0, T = nombre(data, "S0"), nombre(data, "T")
    r, q = pourcentage(data, "r"), pourcentage(data, "q", 0)
    verifier_marche(S0, T, r, q)
    K = nombre(data, "K")
    if not 0 < K <= SPOT_MAX:
        raise ValueError("Le prix contractuel doit être strictement positif et ≤ 10⁹.")
    return dict(S0=S0, K=K, T=T, r=r, q=q, side=_sens(data))


def parametres_strategie(data):
    """Stratégie : on ne lit que les strikes utiles à la stratégie choisie."""
    strategie = choix(data, "strategy", tuple(STRIKES_STRATEGIE), libelle="Stratégie")
    S0, T, r, q, sigma = _marche(data)
    qty = nombre(data, "qty", 1)
    if qty <= 0:
        raise ValueError("La quantité doit être strictement positive.")
    strikes = {cle: nombre(data, cle) for cle in STRIKES_STRATEGIE[strategie]}
    verifier_strikes(S0, list(strikes.values()))
    return dict(strategie=strategie, S0=S0, T=T, r=r, q=q, sigma=sigma,
                qty=qty, side=_sens(data), strikes=strikes)


def parametres_digitale(data):
    """Digitale européenne cash-or-nothing ou asset-or-nothing."""
    if data.get("style", "european") != "european":
        raise ValueError("La digitale est européenne uniquement.")
    S0, T, r, q, sigma = _marche(data)
    K = nombre(data, "K")
    verifier_strikes(S0, [K])
    payout = nombre(data, "payout", 1)
    if payout < 0:
        raise ValueError("Le paiement fixe doit être positif ou nul.")
    return dict(S0=S0, K=K, T=T, r=r, q=q, sigma=sigma, payout=payout,
                option_type=choix(data, "option_type", ("call", "put"), "call", "Type d'option"),
                digital_type=choix(data, "digital_type", ("cash", "asset"), "cash", "Type de digitale"),
                side=_sens(data), n_simulations=simulations(data, 30000), seed=graine(data))


def parametres_phoenix(data):
    """Phoenix : barrières, coupons et bonus saisis en % (70 = 70 %).

    Ordre imposé : protection ≤ coupon ≤ rappel (le rappel seulement s'il est actif).
    """
    S0, T, r, q, sigma = _marche(data)
    nominal = _nominal(data)
    obs_dates = dates_observation(T, entier(data, "frequence", FREQUENCE_DEFAUT))
    n = simulations(data, 50000)
    verifier_constats(n, len(obs_dates))

    K_auto = pourcentage(data, "K_auto", 100)
    B_phoenix = pourcentage(data, "B_phoenix")
    B_pdi = pourcentage(data, "B_pdi")
    strike_pct = pourcentage(data, "strike_pct", 100)
    has_autocall = booleen(data, "has_autocall", True)
    if not all(0 < b <= BARRIERE_MAX for b in (K_auto, B_phoenix, B_pdi, strike_pct)):
        raise ValueError("Les barrières et le strike doivent être strictement positifs et ≤ 300 %.")
    if B_pdi > B_phoenix:
        raise ValueError("La barrière de protection doit être inférieure ou égale à la barrière coupon.")
    if has_autocall and B_phoenix > K_auto:
        raise ValueError("La barrière coupon doit être inférieure ou égale à la barrière de rappel.")

    # coupons : un seul montant ou une liste (step-up) avec un coupon par date
    brut = data.get("coupon")
    if isinstance(brut, list):
        if len(brut) != len(obs_dates):
            raise ValueError(f"Il faut un coupon par date d'observation "
                             f"({len(obs_dates)} dates, {len(brut)} coupons).")
        coupon = [_vers_nombre(c, "coupon") / 100 for c in brut]
    else:
        coupon = pourcentage(data, "coupon")
    if not all(0 <= c <= COUPON_MAX for c in np.atleast_1d(coupon)):
        raise ValueError("Chaque coupon doit être compris entre 0 et 100 % du nominal.")

    bonus_threshold = optionnel(data, "bonus_threshold")
    if bonus_threshold is not None:
        bonus_threshold /= 100
        if bonus_threshold <= 0:
            raise ValueError("Le seuil du bonus doit être strictement positif.")
    bonus_amount = pourcentage(data, "bonus_amount", 0)
    if bonus_amount < 0:
        raise ValueError("Le bonus ne peut pas être négatif.")

    return dict(S0=S0, r=r, q=q, sigma=sigma, nominal=nominal, obs_dates=obs_dates,
                K_auto=K_auto, B_phoenix=B_phoenix, B_pdi=B_pdi, strike_pct=strike_pct,
                coupon=coupon, memory=booleen(data, "memory", False), has_autocall=has_autocall,
                bonus_threshold=bonus_threshold, bonus_amount=bonus_amount,
                n_simulations=n, seed=graine(data),
                step_down=step_down(data, K_auto, len(obs_dates), has_autocall, B_phoenix))


def parametres_autocall(data):
    """Autocall Plain : coupon par observation, rappel optionnel, protection optionnelle."""
    produit = data.get("produit")
    if produit == "autocall_sans_rappel":
        # ancien produit fusionné dans le Plain : message de migration plutôt que "inconnu"
        raise ValueError("Utilisez produit=autocall_plain avec has_autocall=false. "
                         "Les coupons sont désormais conditionnés au niveau final.")
    if produit != "autocall_plain":
        raise ValueError("Produit autocall inconnu.")

    S0, T, r, q, sigma = _marche(data, sigma_nulle=True)
    nominal = _nominal(data)
    obs_dates = dates_observation(T, entier(data, "frequence", FREQUENCE_DEFAUT))
    n = simulations(data, 50000)
    verifier_constats(n, len(obs_dates))

    K_auto = pourcentage(data, "K_auto", 100)
    coupon = pourcentage(data, "coupon")
    if not 0 < K_auto <= BARRIERE_MAX or not 0 <= coupon <= COUPON_MAX:
        raise ValueError("Barrière : 0 à 300 % ; coupon par observation : 0 à 100 %.")
    B_pdi = pourcentage(data, "B_pdi", 60) if booleen(data, "protection", True) else None
    if B_pdi is not None and not 0 < B_pdi <= min(1.0, K_auto):
        raise ValueError("La protection doit être dans ]0 ; min(100 %, barrière de rappel)].")

    has_autocall = booleen(data, "has_autocall", True)
    first_call_date = None
    if has_autocall:
        first_call_date = nombre(data, "first_call_months", 0) / 12   # mois -> années
        if not 0 <= first_call_date <= obs_dates[-1]:
            raise ValueError("La première date de rappel doit être entre 0 et la maturité.")

    return dict(S0=S0, r=r, q=q, sigma=sigma, nominal=nominal, obs_dates=obs_dates,
                K_auto=K_auto, B_pdi=B_pdi, coupon=coupon, n_simulations=n, seed=graine(data),
                has_autocall=has_autocall, first_call_date=first_call_date,
                step_down=step_down(data, K_auto, len(obs_dates), has_autocall))


def parametres_accumulation(data, produit):
    """Accumulateur ou décumulateur ; K et B_KO en prix absolus.

    mode="analyse" : K donné -> valeur ; mode="zero_cout" : on cherche K.
    Ordre imposé : K < S0 < B_KO (accumulateur), B_KO < S0 < K (décumulateur).
    """
    mode = choix(data, "mode", ("analyse", "zero_cout"), "analyse", "Mode de calcul")
    S0, T, r, q, sigma = _marche(data, sigma_nulle=True)
    obs_dates = dates_observation(T, entier(data, "frequence", FREQUENCE_DEFAUT))
    n = simulations(data, 50000)
    verifier_constats(n, len(obs_dates))

    B_KO, quantite, levier = nombre(data, "B_KO"), nombre(data, "quantite", 1), nombre(data, "levier", 2)
    if quantite <= 0 or B_KO < 0 or levier < 1:
        raise ValueError("Quantité positive ; barrière positive ou nulle ; levier ≥ 1.")
    K = nombre(data, "K") if mode == "analyse" else None
    if K is not None and not 0 < K <= SPOT_MAX:
        raise ValueError("Le strike doit être strictement positif et ≤ 10⁹.")
    if produit == "accumulateur" and not ((K is None or K < S0) and S0 < B_KO):
        raise ValueError("Accumulateur : K < S0 < B_KO requis.")
    if produit == "decumulateur" and not ((K is None or S0 < K) and B_KO < S0):
        raise ValueError("Décumulateur : B_KO < S0 < K requis.")

    n_garanties = entier(data, "n_garanties", 0)
    if not 0 <= n_garanties <= len(obs_dates):
        raise ValueError("Les périodes garanties doivent être entre 0 et le nombre de constats.")

    return dict(mode=mode, K=K, S0=S0, r=r, q=q, sigma=sigma, obs_dates=obs_dates,
                B_KO=B_KO, quantite=quantite, levier=levier, n_simulations=n,
                ko_inclut_date=booleen(data, "ko_inclut_date", False),
                n_garanties=n_garanties, seed=graine(data))


def parametres_dra(data):
    """Dual Range Accrual : marché et coupon en % ; rho en décimal ; fourchettes absolues.

    Renvoie (arguments du moteur, sensibilité demandée ?).
    """
    T = nombre(data, "T")
    S1_0, S2_0 = nombre(data, "S1_0"), nombre(data, "S2_0")
    r = pourcentage(data, "r", 0)
    q1, q2 = pourcentage(data, "q1", 0), pourcentage(data, "q2", 0)
    sigma1, sigma2 = pourcentage(data, "sigma1", 0), pourcentage(data, "sigma2", 0)
    verifier_marche(S1_0, T, r, q1, sigma1, sigma_nulle=True)
    verifier_marche(S2_0, T, r, q2, sigma2, sigma_nulle=True)

    rho = nombre(data, "rho", 0)
    if not -1 < rho < 1:
        raise ValueError("La corrélation doit être strictement entre -1 et 1.")
    L1, U1, L2, U2 = (nombre(data, cle) for cle in ("L1", "U1", "L2", "U2"))
    if not 0 <= L1 < S1_0 < U1 or not 0 <= L2 < S2_0 < U2:
        raise ValueError("Chaque actif doit respecter 0 ≤ L < spot < U.")
    taux_annuel = pourcentage(data, "taux_annuel", 8)
    if not 0 <= taux_annuel <= 1:
        raise ValueError("Coupon annuel entre 0 et 100 %.")
    nominal = _nominal(data)

    jours_par_an = entier(data, "jours_par_an", 252)
    frequence_paiement = entier(data, "frequence_paiement", 4)
    if not 1 <= jours_par_an <= 366 or not 1 <= frequence_paiement <= jours_par_an:
        raise ValueError("Jours par an entre 1 et 366 ; fréquence de paiement entre 1 et jours par an.")
    n = simulations(data, 20000, N_SIM_MAX_DRA)
    sensibilite = booleen(data, "sensibilite", False)
    n_jours = n_constats_dra(T, jours_par_an)
    verifier_constats(n, n_jours, CONSTATS_MAX_DRA)
    if n * n_jours * (8 if sensibilite else 1) > BUDGET_DRA_SENSIBILITE:
        raise ValueError("Calcul trop volumineux : réduisez les simulations ou la maturité, "
                         "ou désactivez la sensibilité.")

    arguments = dict(S1_0=S1_0, S2_0=S2_0, r=r, q1=q1, q2=q2, sigma1=sigma1, sigma2=sigma2,
                     T=T, L1=L1, U1=U1, L2=L2, U2=U2, taux_annuel=taux_annuel, nominal=nominal,
                     n_simulations=n, jours_par_an=jours_par_an,
                     frequence_paiement=frequence_paiement, seed=graine(data))
    return arguments, rho, sensibilite


def parametres_volatilite(args):
    """Onglet Chantier : ticker, période (liste blanche) et λ de l'EWMA."""
    ticker = args.get("ticker", "").strip().upper()
    if not ticker:
        raise ValueError("Veuillez renseigner un ticker.")
    periode = args.get("periode", "1y")
    if periode not in PERIODES_VOLATILITE:
        raise ValueError("Période historique invalide.")
    lambda_ = nombre(args, "lambda", 0.94)
    if not 0 < lambda_ < 1:
        raise ValueError("Lambda doit être strictement compris entre 0 et 1.")
    return ticker, periode, lambda_
