# `pricer_engine.py` — options vanilles, grecques, forwards, stratégies, digitales

Ce fichier est la **base mathématique** du projet. Il ne dépend que de NumPy, de `statistics.NormalDist` (bibliothèque standard) et du simulateur commun `simulation_engine.py`. Il ne sait rien de Flask ni du navigateur.

**Convention :** `r`, `q`, `sigma` en décimal (`0.05` = 5 %), `T` en années, `S0` et `K` en prix.

**Aucun contrôle de paramètre ici :** `validation.py` vérifie tout avant l'appel. Les fonctions ressemblent donc aux formules du cours, sans lignes de garde.

---

## 1. Fonctions mathématiques de base

```python
def Ncdf(x):
    return NormalDist().cdf(x)

def Npdf(x):
    return NormalDist().pdf(x)
```

- `Ncdf` = N(x), probabilité qu'une loi normale centrée réduite soit inférieure à x. C'est le N(d1), N(d2) des formules.
- `Npdf` = n(x) = e^(−x²/2)/√(2π), la densité. Elle apparaît dans toutes les grecques.

**Pourquoi `statistics.NormalDist` et pas `scipy.stats.norm` ?** Pour éviter une dépendance lourde (SciPy) juste pour deux fonctions. `NormalDist` est dans la bibliothèque standard depuis Python 3.8.

**Limite :** `NormalDist().cdf` travaille sur un seul nombre (pas sur un tableau NumPy). C'est suffisant ici car Black-Scholes est appelé pour un seul jeu de paramètres à la fois. Pour pricer 10 000 options d'un coup, il faudrait `scipy.special.ndtr`, vectorisé.

```python
def _d1_d2(S0, K, T, r, q, sigma):
    d1 = (np.log(S0 / K) + (r - q + sigma**2 / 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return d1, d2
```

- `d1` et `d2` servent dans **toutes** les formules : les calculer à un seul endroit évite de les recopier (et d'introduire une faute de frappe dans une des copies).
- Le `_` initial signale une fonction « interne » : elle n'est pas destinée à être appelée depuis `main.py`.
- Lecture économique : N(d2) est la probabilité risque-neutre que le call finisse dans la monnaie ; S0·e^(−qT)·N(d1) est la valeur actuelle de l'action reçue dans ce cas.

---

## 2. Black-Scholes

```python
if option_type == "call":
    prix = S0 * np.exp(-q*T) * Ncdf(d1) - K * np.exp(-r*T) * Ncdf(d2)
else:
    prix = K * np.exp(-r*T) * Ncdf(-d2) - S0 * np.exp(-q*T) * Ncdf(-d1)
```

- Version avec **dividende continu q** (modèle de Merton) : on remplace S0 par S0·e^(−qT), car l'action « perd » ses dividendes que le détenteur d'option ne touche pas.
- Le put utilise N(−d) = 1 − N(d). On aurait pu passer par la parité call-put, mais la formule directe évite une soustraction de deux grands nombres (perte de précision pour les options très en dehors de la monnaie).
- `else` couvre le put : la validation du type (`"call"` ou `"put"`) est faite dans `validation.py`.

**Vérification rapide pour un étudiant :** call − put doit valoir S0·e^(−qT) − K·e^(−rT) (parité call-put).

---

## 3. Monte Carlo européen (vanilles et digitales)

Les deux Monte Carlo européens (option vanille et digitale) partagent les mêmes briques. Avant le nettoyage des doublons, `monte_carlo` avait sa propre simulation, sans graine et sans intervalle de confiance, alors que `digital_monte_carlo` avait les deux : c'est cette seconde version qui a été généralisée.

```python
def _simuler_ST(S0, T, r, q, sigma, n_simulations, seed):
    rng = np.random.default_rng(seed)
    return simuler_cours_observations(S0, r, q, sigma, [T], n_simulations, rng)[:, 1]

def _resultat_monte_carlo(flux):
    prix = float(np.mean(flux))
    ecart_type = float(np.std(flux, ddof=1))
    erreur_standard = ecart_type/np.sqrt(len(flux))
    return {"price": prix, "ecart_type": ecart_type, "erreur_standard": float(erreur_standard),
            "ic95": [prix-1.96*erreur_standard, prix+1.96*erreur_standard]}

def monte_carlo(S0, K, T, r, q, sigma, option_type, n_simulations, seed=None):
    ST = _simuler_ST(S0, T, r, q, sigma, n_simulations, seed)
    payoff = np.maximum(ST - K, 0) if option_type == "call" else np.maximum(K - ST, 0)
    return _resultat_monte_carlo(np.exp(-r*T)*payoff)
```

Ligne par ligne :

1. **`_simuler_ST`** : on simule **directement** S_T, avec une seule date d'observation (la maturité). Sous Black-Scholes, la solution du mouvement brownien géométrique est exacte : pas besoin des jours intermédiaires pour une européenne (le payoff ne dépend que de S_T). La formule elle-même, correction d'Itô comprise, est dans `simulation_engine.py`.
2. **La graine** (`seed`) : même graine = mêmes tirages = même prix. `main.py` passe 42 par défaut.
3. `np.maximum` calcule le payoff de toutes les simulations sans boucle.
4. On actualise chaque payoff au taux sans risque, puis `_resultat_monte_carlo` fait la moyenne : c'est la définition du prix risque-neutre.
5. **Erreur standard et IC95** : `ddof=1` donne l'écart-type empirique non biaisé (division par n − 1). L'intervalle prix ± 1,96 × erreur standard contient le vrai prix avec 95 % de probabilité : c'est ce qui permet de dire si l'écart avec Black-Scholes est du bruit ou une erreur.

**Pourquoi garder Monte Carlo alors que Black-Scholes donne la réponse exacte ?** Pour l'enseignement : comparer les deux montre la convergence en 1/√N, et prépare aux produits structurés où aucune formule fermée n'existe.

---

## 4. Longstaff-Schwartz (option américaine)

Une américaine peut être exercée à tout moment : il faut décider, à chaque date, s'il vaut mieux exercer ou attendre. Longstaff-Schwartz (2001) estime la **valeur d'attente** par régression.

### 4.1 Simulation des chemins complets

```python
dt = T / n_steps
dates = np.arange(1, n_steps + 1) * dt
prix = simuler_cours_observations(S0, r, q, sigma, dates, n_simulations, np.random.default_rng(seed))
```

- Ici on a besoin de **tout le chemin** (100 dates par défaut), car l'exercice peut arriver n'importe quand.
- Les chemins viennent du **simulateur commun** : avant, cette fonction avait sa propre boucle de simulation, sans graine. Le prix américain change maintenant seulement si les paramètres changent.
- Mémoire : 30 000 × 101 × 8 octets ≈ 24 Mo. C'est pour ça que `n_steps` reste modeste.

### 4.2 Payoff d'exercice à chaque date

```python
payoff = np.maximum(prix - K, 0)   # matrice entière
cashflow = payoff[:, -1]           # par défaut : exercice à maturité
```

### 4.3 Remontée dans le temps

```python
for t in range(n_steps - 1, 0, -1):
    cashflow *= np.exp(-r * dt)
    itm = payoff[:, t] > 0
    if np.sum(itm) > 2:
        X = prix[itm, t]; Y = cashflow[itm]
        X_norm = (X - X.mean()) / X.std()
        regression = np.polyfit(X_norm, Y, 2)
        continuation = np.polyval(regression, X_norm)
        exercice = payoff[itm, t] > continuation
        indices = np.where(itm)[0]
        cashflow[indices[exercice]] = payoff[indices[exercice], t]
```

- On part de la fin et on recule : à chaque pas, `cashflow` est ramené d'une période (actualisation).
- **On ne régresse que sur les chemins dans la monnaie** (`itm`) : hors de la monnaie, exercer ne rapporte rien, inutile de les inclure (c'est la recommandation de l'article original et ça améliore la régression).
- `np.sum(itm) > 2` : il faut au moins 3 points pour ajuster un polynôme de degré 2.
- **Problème rencontré :** avec des prix de l'ordre de 300, X² ≈ 90 000 ; `polyfit` devenait mal conditionné et le prix sautait d'un calcul à l'autre. **Contournement :** standardiser X (moyenne 0, écart-type 1) avant la régression.
- Si exercer immédiatement rapporte plus que la continuation estimée, le flux du chemin est remplacé par le payoff immédiat.
- `indices[exercice]` : on traduit le masque « parmi les chemins ITM » en indices dans le tableau complet.

### 4.4 Prix final et plancher

```python
prix_lsm = np.mean(cashflow) * np.exp(-r * dt)
return max(prix_lsm, payoff[0, 0])
```

- Dernière actualisation de t=1 vers t=0.
- Plancher : une américaine vaut toujours au moins son exercice immédiat. `payoff[0, 0]` est justement le payoff d'exercice en t = 0 (colonne 0 = S0), déjà calculé : pas besoin de réécrire max(S0 − K, 0).
- Le bruit Monte Carlo pourrait sinon donner un prix inférieur pour une option très dans la monnaie.

**Limites :** base polynomiale de degré 2 seulement (le choix de base influence le prix), 100 dates d'exercice (approximation du continu), estimateur légèrement biaisé vers le bas (règle d'exercice sous-optimale).

---

## 5. Grecques primaires (formules fermées)

| Grecque | Signification | Formule (call) |
|---|---|---|
| Delta | ∂V/∂S | e^(−qT)·N(d1) |
| Gamma | ∂²V/∂S² | e^(−qT)·n(d1) / (S·σ·√T) |
| Vega | ∂V/∂σ | S·e^(−qT)·n(d1)·√T |
| Theta | ∂V/∂t (par an) | −S·σ·e^(−qT)·n(d1)/(2√T) − r·K·e^(−rT)·N(d2) + q·S·e^(−qT)·N(d1) |
| Rho | ∂V/∂r | K·T·e^(−rT)·N(d2) |

**Unités à connaître (piège classique) :**
- **Vega et Rho sont pour une variation de 1,00**, c'est-à-dire 100 points de vol ou de taux. Pour « 1 point de vol », diviser par 100.
- **Theta est annuel.** Pour un theta par jour, diviser par 365 (calendaire) ou 252 (ouvré).

Gamma et Vega sont identiques pour le call et le put (la parité call-put les annule).

---

## 6. Grecques secondaires

```python
primaires = grecs_primaires_bs(S0, K, T, r, q, sigma, option_type)
Delta, Gamma, Vega = primaires["Delta"], primaires["Gamma"], primaires["Vega"]
Vanna = (-np.exp(-q*T)*Npdf(d1)*d2)/sigma      # ∂Delta/∂σ
Vomma = (Vega*d1*d2)/sigma                      # ∂Vega/∂σ
Charm = ...                                     # ∂Delta/∂t
Color = ...                                     # ∂Gamma/∂t
Speed = -Gamma/S0*(d1/(sigma*np.sqrt(T))+1)     # ∂Gamma/∂S
Zomma = Gamma*(d1*d2-1)/sigma                   # ∂Gamma/∂σ
Ultima = ...                                    # ∂Vomma/∂σ
Lambda = Delta * S0 / prix                      # élasticité
```

- Delta, Gamma et Vega viennent de `grecs_primaires_bs`. Avant, leurs formules étaient recopiées ici : une faute corrigée dans l'une des copies aurait laissé l'autre fausse. Une seule formule par grecque.
- **Utilité sur un desk :** Vanna et Volga (= Vomma) servent à pricer le smile (méthode « vanna-volga » en change) ; Charm dit comment le delta dérive pendant un week-end ; Speed et Color mesurent la stabilité du gamma.
- **Lambda** = levier de l'option : si Lambda = 8, une hausse de 1 % du sous-jacent fait gagner ≈ 8 % sur la prime.
- **Limite :** Lambda divise par le prix ; pour une option très en dehors de la monnaie (prix ≈ 0), le chiffre explose et n'a plus de sens.

---

## 7. Forward / Future

```python
def forward_price(S0, T, r, q):
    return S0*np.exp((r-q)*T)
```

Prix de **portage** : acheter l'action aujourd'hui coûte le financement (r) mais rapporte les dividendes (q). Par non-arbitrage, F = S0·e^((r−q)T).

```python
def forward_value(S0, K, T, r, q, side):
    sign = 1 if side == "buy" else -1
    return sign*(forward_price(S0, T, r, q)-K)*np.exp(-r*T)
```

Un forward déjà signé au prix K vaut la différence entre le prix à terme actuel F et K, actualisée, avec le signe du sens (acheteur : F − K ; vendeur : K − F). À la signature, K = F et la valeur est nulle : on n'échange pas de prime.

`forward_payoff` renvoie sign × (S_T − K) : un payoff **linéaire**, contrairement à une option. Il accepte directement toute la grille de S_T (calcul vectorisé, pas de boucle dans `main.py`).

---

## 8. Les jambes et les stratégies

### 8.1 Représentation d'une jambe

```python
def make_position(option_type, side, K, T, qty=1):
    return {"instrument": "option", "option_type": ..., "side": ..., "K": ..., "T": ..., "qty": ...}

def make_stock_position(side, qty=1):
    return {"instrument": "stock", "side": side, "qty": qty}
```

**Choix de conception :** une stratégie = une **liste de dictionnaires**. C'est simple, lisible, sérialisable en JSON. Une classe `Leg` aurait été plus « propre » (c'est ce qu'on ferait en C#), mais pour un projet pédagogique le dictionnaire montre directement les données.

### 8.2 Prix et payoff d'une jambe

```python
def _signe(leg):
    return 1 if leg["side"] == "buy" else -1
```

Ce **signe** est l'idée clé (écrit une seule fois et utilisé par le prix, le payoff et les grecques) : acheter = coût positif et payoff positif ; vendre = prime encaissée (coût négatif) et payoff négatif. Toute la stratégie devient une **somme signée**.

- `price_leg` : action → S0 ; option → Black-Scholes.
- `payoff_leg` : action → S_T ; option → max(S_T − K, 0) ou max(K − S_T, 0).

### 8.3 Agrégation

```python
payoff_total = sum(payoff_leg(leg, ST_grid) for leg in legs)
cout_total = sum(price_leg(leg, S0, r, q, sigma) for leg in legs)
return {"payoff": payoff_total, "pnl": payoff_total - cout_total}
```

Le P&L à maturité = payoff total − coût de mise en place. On néglige le coût de financement de la prime entre aujourd'hui et la maturité (choix pédagogique, à mentionner si on compare avec un vrai P&L).

`greeks_strategie` additionne de même les grecques signées, avec une boucle sur les cinq grecques (`for cle in total`) au lieu de cinq lignes recopiées. Une action a un delta de 1 et toutes les autres grecques nulles.

**Limite :** une seule volatilité pour toutes les jambes (pas de smile). `main.py` refuse d'ailleurs explicitement un champ `leg_vols`.

### 8.4 Les constructeurs de stratégies

| Fonction | Composition | Vue de marché |
|---|---|---|
| `make_covered_call` | + action, − call K | Neutre / légèrement haussier, on vend la hausse au-delà de K contre une prime |
| `make_protective_put` | + action, + put K | Haussier mais assuré contre la baisse |
| `make_straddle` | call K + put K, même sens | Achat : pari sur un gros mouvement (vol) ; vente : pari sur le calme |
| `make_strangle` | call K_call + put K_put | Comme le straddle, moins cher, il faut un plus gros mouvement |
| `make_call_spread` | + call K1, − call K2 (K1 < K2) | Haussier à gain plafonné, moins cher qu'un call sec |
| `make_put_spread` | + put K1, − put K2 (K1 > K2) | Baissier à gain plafonné |
| `make_collar` | + action, + put, − call | Protection financée par la vente du potentiel de hausse |

---

## 9. Options digitales

### 9.1 Prix fermés

```python
def digital_bs(...):         # cash-or-nothing
    return payout * np.exp(-r*T) * Ncdf(signe*d2)

def asset_or_nothing_bs(...):
    return S0 * np.exp(-q*T) * Ncdf(signe*d1)
```

- Cash-or-nothing : on reçoit `payout` si S_T > K (call). Prix = montant actualisé × probabilité risque-neutre N(d2).
- Asset-or-nothing : on reçoit l'action si S_T > K. Prix = S0·e^(−qT)·N(d1).
- `signe = +1` pour un call, `−1` pour un put : N(−d) pour le put, sans dupliquer la formule.
- **Lien avec le call vanille :** call = asset-or-nothing − K × cash-or-nothing (unitaire). C'est la décomposition qui sert aux grecques ci-dessous.

### 9.2 Grecques des digitales

- Calculées par dérivation de e^(−rT)·N(±d2).
- **Particularité pédagogique :** le gamma et le vega d'une digitale **changent de signe** autour du strike, et le delta explose à l'approche de la maturité près du strike. C'est pourquoi les traders couvrent une digitale par un call spread serré.
- `derivee_t` = ∂d2/∂T, utilisée dans Theta et dans la dérivation de N(d2) par rapport au temps.
- Pour l'asset-or-nothing, on n'a pas réécrit de formules : on utilise l'identité
  `asset = vanille + K·cash` (call) ou `K·cash − vanille` (put),
  d'où `K*cash[cle] + signe*vanille[cle]`. Moins de code = moins d'erreurs.

### 9.3 Monte Carlo des digitales

```python
ST = _simuler_ST(S0, T, r, q, sigma, n_simulations, seed)
condition = ST > K if option_type == "call" else ST < K
flux = np.exp(-r*T)*condition*(ST if digital_type == "asset" else payout)
return _resultat_monte_carlo(flux)
```

- Mêmes briques que le Monte Carlo vanille (section 3) : seul le payoff change.
- `condition` est un tableau de booléens ; multiplier par un booléen vaut 0 ou 1.
- Inégalité **stricte** (`ST > K`) : le cas S_T = K a une probabilité nulle en théorie, mais il faut une convention cohérente avec le graphe (`main.py` utilise aussi `>`).
