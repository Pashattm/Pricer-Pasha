# `volatility_engine.py` et `market_data_engine.py` — volatilité et données de marché

Ces deux fichiers alimentent l'onglet **5) Chantier** (laboratoire de volatilité) et le choix « σ historique » des autres onglets.

**Répartition des rôles :**
- `volatility_engine.py` : **maths pures**. Reçoit des prix propres, renvoie des volatilités en **décimal annualisé** (`0.25` = 25 %). Aucun accès réseau, aucune route Flask, aucun contrôle.
- `market_data_engine.py` : **entrées / sorties**. Va chercher les données sur Yahoo Finance, **nettoie les cours** (seul endroit où ils sont contrôlés), appelle le moteur, convertit en **pourcentages** pour l'API.
- Le paramètre λ de l'EWMA et la période sont vérifiés par `validation.parametres_volatilite`.

Pourquoi séparer ? Si Yahoo change d'API (fréquent), seul `market_data_engine.py` est touché. Et les formules restent vérifiables hors connexion, avec des prix inventés.

> **Doublons supprimés :** les contrôles des cours (positifs, finis, plus haut ≥ plus bas…) existaient à la fois dans `market_data_engine` et dans chaque fonction de `volatility_engine`, et λ était vérifié dans la route **et** dans le moteur. Ils sont maintenant faits une fois, à l'entrée. Ont aussi été retirées deux fonctions que rien n'appelait (`get_rolling_volatility`, `ewma_volatility`) et la fonction `get_historical_prices`, remplacée par un téléchargement commun.

---

## Partie A — `volatility_engine.py`

```python
ANNUALISATION = 252  # séances par an
```
Une constante nommée plutôt que `252` écrit en dur dans chaque fonction ; `market_data_engine` la renvoie aussi dans la réponse de l'API.

### A.1 `log_returns`

```python
prices = np.asarray(prices, dtype=float)
return np.log(prices[1:] / prices[:-1])
```
- r_t = ln(S_t / S_{t−1}).
- **Pourquoi des log-rendements ?** Ils s'additionnent dans le temps (le rendement sur 2 jours = somme des deux), et Black-Scholes suppose précisément que ln(S) suit une loi normale.
- `prices[1:] / prices[:-1]` : division décalée vectorisée (tous les rendements d'un coup).
- Un prix nul ferait `log(0)` = −∞ : c'est pourquoi `market_data_engine` refuse les cours nuls ou négatifs avant d'appeler cette fonction.

### A.2 `historical_volatility` (close-to-close)

```python
daily_volatility = np.std(log_returns(prices), ddof=1)
return float(daily_volatility * np.sqrt(annualization))
```
- `ddof=1` : estimateur non biaisé de la variance (division par n−1).
- **Annualisation par √252** : si les rendements quotidiens sont indépendants, la variance s'additionne, donc la vol croît comme la racine du temps. 252 = nombre usuel de séances par an.

### A.3 `rolling_volatility`

```python
for i in range(window, len(returns) + 1):
    window_returns = returns[i - window:i]
    rolling_vol.append(np.std(window_returns, ddof=1) * np.sqrt(annualization))
```
- À chaque date, vol des `window` derniers rendements seulement. On voit ainsi la vol **évoluer** (pics en crise) au lieu d'un seul chiffre.
- Boucle Python assumée : quelques centaines d'itérations, rapide. Une version vectorisée existe (`pandas.Series.rolling(window).std()`), mais la boucle montre explicitement la fenêtre.
- Sortie : `len(rendements) − window + 1` valeurs.

### A.4 `parkinson_volatility`

```python
ranges = np.log(high) - np.log(low)
daily_variance = np.mean(ranges ** 2) / (4 * np.log(2))
return float(np.sqrt(daily_variance * annualization))
```
- Utilise l'**amplitude intraday** (plus haut / plus bas) au lieu des seules clôtures. À nombre de jours égal, l'estimateur est plus précis car il voit davantage du mouvement de la journée.
- Le facteur 1/(4 ln 2) vient de la loi du range d'un brownien.
- **Limite documentée :** les **gaps d'ouverture** (mouvement de nuit) sont invisibles dans l'amplitude d'une séance : Parkinson sous-estime la vol des titres qui gappent (résultats publiés hors séance).

### A.5 `yang_zhang_volatility` et `garch_volatility`

Présentes avec `pass` : **emplacements réservés**. Les docstrings décrivent déjà le but (Yang-Zhang combine overnight, open-to-close et range ; GARCH modélise une vol qui dépend des chocs passés). C'est une bonne pratique de laisser l'interface prête et documentée pour le suivant.

### A.6 `ewma_volatility_series`

```python
variance = returns[0] ** 2
values = [np.sqrt(variance * annualization)]
for daily_return in returns[1:]:
    variance = lambda_ * variance + (1 - lambda_) * daily_return ** 2
    values.append(np.sqrt(variance * annualization))
```
- σ²_t = λ·σ²_{t−1} + (1−λ)·r_t². Les chocs récents pèsent plus ; les anciens s'effacent géométriquement. C'est le modèle RiskMetrics (J.P. Morgan, 1994), avec λ = 0,94 pour des données quotidiennes.
- **Moyenne supposée nulle** : sur des rendements quotidiens, la moyenne est négligeable devant l'écart-type.
- **Piège évité :** l'initialisation « naturelle » consiste à prendre la variance de **tout** l'échantillon comme point de départ. Cela injecte de l'information future dans les premières valeurs (biais d'anticipation). **Choix retenu :** initialisation au carré du **premier** rendement seulement. Conséquence assumée : les toutes premières valeurs sont bruitées, et changer la période historique change l'initialisation (l'interface le dit).
- La valeur datée t **inclut** le rendement du jour t (mise à jour après la clôture).

### A.7 `ewma_half_life`

```python
return float(np.log(0.5) / np.log(lambda_))
```
Nombre de jours pour que le poids d'une observation soit divisé par deux : λ^h = 0,5 ⇒ h = ln 0,5 / ln λ. Pour λ = 0,94, h ≈ 11,2 jours. Ça rend λ **interprétable** : « la mémoire de l'EWMA est d'environ deux semaines ».

---

## Partie B — `market_data_engine.py`

### B.1 `search_tickers`

```python
try:
    resultats = yf.Search(query, max_results=max_results).quotes
except Exception:
    return []
```
- Utilise la barre de recherche de Yahoo.
- **Choix volontaire :** en cas d'échec, on renvoie une liste vide plutôt qu'une erreur. La recherche est un confort (autocomplétion) ; elle ne doit pas bloquer l'utilisateur, qui peut toujours taper le symbole.
- On garde `shortname`, sinon `longname`, sinon le symbole : Yahoo ne remplit pas toujours tous les champs.

### B.2 `get_cotation` et `get_vix`

```python
infos = yf.Ticker(ticker).fast_info
return {"S0": float(infos["last_price"]), "devise": infos["currency"]}
```
- Spot et devise en **une seule** requête (`fast_info`, plus léger que de télécharger tout l'historique). Il y avait auparavant deux fonctions, `get_spot` et `get_devise`, qui interrogeaient Yahoo deux fois pour le même ticker.
- Le VIX est récupéré via son ticker `^VIX`.

**À savoir :** le VIX est la vol **implicite** à 30 jours du **S&P 500**. L'utiliser pour une action européenne est une approximation grossière, utile seulement comme valeur par défaut.

### B.3 `_telecharger_historique` et `_verifier_cours` — le nettoyage commun

```python
data = yf.Ticker(ticker).history(period=periode, auto_adjust=True)
if data.empty: raise ...
data = data.sort_index()
if data.index.has_duplicates: raise ...
```
```python
for serie in series:
    if not np.all(np.isfinite(serie)) or np.any(serie <= 0):
        raise ValueError("L'historique contient des cours manquants, nuls ou non finis.")
```
- **`auto_adjust=True` est essentiel :** les prix sont ajustés des dividendes et des divisions d'actions (splits). Sans ça, un split 4-pour-1 apparaît comme une chute de −75 % en une journée et fait exploser la volatilité.
- Tri chronologique, pas de dates en double, pas de trous (`NaN`), pas de cours nuls ou négatifs.
- Ces deux fonctions sont utilisées par **les deux** chemins (vol historique des onglets de pricing et onglet Chantier) : Yahoo n'est pas toujours propre, et le nettoyage n'est écrit qu'une fois.

### B.4 `get_vol_historique`

```python
close = _telecharger_historique(ticker, periode)["Close"].dropna().to_numpy(dtype=float)
_verifier_cours(close)
if len(close) < 20: raise ...
return historical_volatility(close) * 100
```
Volatilité historique close-to-close sur 1 an, en %. Exige au moins 20 cours : en dessous, l'estimation n'a pas de sens. Dans `main.py`, si cette fonction échoue (ticker récent), la route renvoie `vol_hist = None` et l'interface bascule sur le VIX.

### B.5 `get_volatility_snapshot` — la fonction de l'onglet Chantier

**Problème à éviter :** télécharger les clôtures puis, dans un second appel, les plus hauts / plus bas. Entre deux appels, une séance peut s'ajouter : les mesures ne porteraient plus exactement sur les mêmes jours.

**Solution :** **un seul téléchargement** OHLC, d'où sont tirées toutes les mesures.

Contrôles spécifiques, en plus du nettoyage commun :
```python
if np.any(high < low) or np.any(close < low) or np.any(close > high): raise ...
if len(close) < 61: raise ...
```
- Cohérence OHLC (la clôture est entre le plus bas et le plus haut).
- 61 séances minimum = 60 rendements pour la fenêtre longue.

**Alignement des courbes.** Les mesures n'ont pas la même longueur : 20 j, 60 j, EWMA et Parkinson glissant démarrent à des dates différentes. Pour les superposer sur un même graphe :

| Série | Longueur brute (n = nombre de cours) | Découpe | Longueur finale |
|---|---|---|---|
| `vol_20` | n − 20 | `[40:]` | n − 60 |
| `vol_60` | n − 60 | — | n − 60 |
| `ewma` | n − 1 | `[59:]` | n − 60 |
| `parkinson_20` | `i` de 60 à n−1 | — | n − 60 |
| `dates` | n | `index[60:]` | n − 60 |

Toutes les courbes **démarrent à la première fenêtre complète de 60 rendements** et ont la même longueur que les dates.

```python
parkinson_20 = [parkinson_volatility(high[i-19:i+1], low[i-19:i+1]) for i in range(60, len(close))]
```
Parkinson glissant sur les 20 séances qui se terminent à la séance affichée **incluse** (`i+1` car la borne haute d'une tranche Python est exclue).

**Deux Parkinson différents** (piège pour le lecteur) : l'indicateur `vol_parkinson` couvre toute la période ; la courbe `series_parkinson_20` couvre 20 séances. L'interface l'écrit sous le graphe.

Le résultat contient aussi `annualization`, le nombre d'observations, les dates de début et de fin et la demi-vie EWMA : tout ce qu'il faut pour savoir **sur quoi** porte le calcul.

---

## Exercices

1. Comparez la vol historique 1 an d'une action et sa vol EWMA juste après une publication de résultats : laquelle réagit le plus ? Pourquoi ?
2. Prenez λ = 0,97 puis λ = 0,90 : calculez les demi-vies et observez la réactivité des courbes.
3. Trouvez une action avec de gros gaps d'ouverture : Parkinson est-il au-dessus ou en dessous du close-to-close ?
