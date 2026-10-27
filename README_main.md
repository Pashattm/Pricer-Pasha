# `main.py` — le serveur Flask

`main.py` est la porte d'entrée du pricer. Pour chaque requête, il :

1. reçoit les paramètres envoyés par `index.html` (JSON) ;
2. les confie à `validation.py`, qui les lit, les convertit et les vérifie ;
3. appelle le bon moteur ;
4. **met en forme** la réponse (arrondis, montants × nominal, grille du graphe) ;
5. mémorise le calcul pour l'export PDF.

Lancement : `python main.py`, puis <http://127.0.0.1:5000>.

> **Pourquoi Flask ?** C'est le micro-framework web Python le plus simple : une route = une fonction décorée par `@app.post("/api/...")`. Django serait surdimensionné (base de données, administration…) ; FastAPI est excellent mais ajoute des notions (typage Pydantic, asynchrone) qui ne servent pas ici.

Après le nettoyage des doublons, chaque route tient en quelques lignes : **lire les paramètres → calculer → répondre**. Les contrôles sont dans `validation.py`, les erreurs sont gérées une seule fois pour tout le serveur.

---

## 1. Imports

```python
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.exceptions import HTTPException
from pricer_engine import (...)
from strutu_engine import (...)
from accumulator_engine import analyse_accumulation, strike_zero_cout
from dra_engine import analyse_dra, sensibilite_correlation
from market_data_engine import search_tickers, get_cotation, get_vix, get_vol_historique, get_volatility_snapshot
import validation as v
```

- Tous les imports sont **en tête de fichier** (bonne pratique Python : on voit d'un coup d'œil de quoi dépend le fichier).
- `import validation as v` : les routes écrivent `v.parametres_phoenix(...)`, court et explicite.
- `app.config['MAX_CONTENT_LENGTH'] = 2_000_000` : requêtes limitées à 2 Mo. Un formulaire fait quelques kilo-octets ; ça protège contre un envoi énorme qui saturerait la mémoire.

---

## 2. Fonctions utilitaires

### `_err`

```python
return jsonify({"error": message}), code
```
Format d'erreur **unique** pour tout le serveur. Le navigateur sait toujours où lire le message (`data.error`) et l'affiche en rouge sous le bouton.

### `_grid` — grille de prix finaux pour les graphiques

```python
points = list(np.linspace(max(0.01, min(centres) * low), max(centres) * high, n_points))
for x in sauts:
    points.extend([np.nextafter(x, -np.inf), x, np.nextafter(x, np.inf)])
return np.unique(points)
```
- On centre la grille sur les niveaux intéressants (spot, strikes, barrières), de 40 % du plus bas à 160 % du plus haut par défaut.
- `max(0.01, ...)` : un prix ne peut pas être nul ou négatif.
- **`sauts`** : le payoff d'une digitale ou d'un structuré fait une **marche** au strike ou aux barrières. Une grille régulière tombe rarement pile dessus, et le graphe montrerait une pente. On ajoute donc le niveau et ses deux voisins flottants immédiats (`np.nextafter`) : le graphe dessine une vraie marche verticale.
- `np.unique` trie et retire les doublons.

Cette fonction remplace trois versions qui existaient avant (la grille simple, la grille de la digitale et celle de l'Autocall). Le Phoenix, qui utilisait la grille simple, profite maintenant lui aussi des marches.

### `_find_breakevens` — points morts

```python
signs = np.sign(pnl)
for i in range(len(signs) - 1):
    if signs[i] != signs[i + 1] and signs[i] != 0 and signs[i + 1] != 0:
        be = ST_grid[i] + (0 - pnl[i]) * (ST_grid[i + 1] - ST_grid[i]) / (pnl[i + 1] - pnl[i])
```
- On cherche les endroits où le P&L **change de signe** entre deux points de grille, puis on interpole linéairement pour trouver le zéro exact.
- Il peut y en avoir plusieurs (straddle : deux breakevens).
- **Problème rencontré :** la première version utilisait `np.interp(0, pnl, ST_grid)`. Or `np.interp` exige des abscisses **croissantes**. Pour un put ou un short call, le P&L décroît : le résultat était faux **sans aucun message d'erreur**. D'où l'interpolation écrite à la main.

### `_arrondi`, `_pct`, `_reponse_graphe`, `_reponse_structure`

- `_arrondi` : arrondit une liste pour alléger le JSON.
- `_pct` : fraction du nominal → pourcentage (`0.9712` → `97.12`), `None` reste `None`.
- `_reponse_graphe` : les trois listes du graphe (`ST_grid`, `payoff`, `pnl`) et, si demandé, les breakevens. Utilisée par les options, le forward, les stratégies et le Phoenix.
- `_reponse_structure` : les indicateurs **communs au Phoenix et à l'Autocall** (prix en devise et en %, IC95, marge, durée de vie, probabilités, flux par date). Avant, ces quinze champs étaient construits deux fois, avec des différences (l'Autocall renvoyait `erreur_type`, pas le Phoenix ; l'Autocall renvoyait la marge sous deux noms).

**Convention de nommage tenue dans tout le fichier :** un champ qui finit par `_pct` est en %, un champ sans suffixe est en devise du nominal.

---

## 3. Gestion des erreurs : un seul endroit

```python
@app.errorhandler(ValueError)
def erreur_parametre(erreur):
    return _err(str(erreur))

@app.errorhandler(Exception)
def erreur_inattendue(erreur):
    if isinstance(erreur, HTTPException):
        if erreur.code == 413:
            return _err("Requête limitée à 2 Mo.", 413)
        return _err(erreur.description, erreur.code)
    return _err("Le calcul a échoué. Vérifiez les paramètres et réessayez.", 500)
```

- **Toute `ValueError`**, levée par `validation.py` ou par un moteur (« aucun strike zéro coût »), devient une réponse **400** avec son message.
- **Toute autre erreur** devient une réponse **500** avec un message générique : on ne renvoie pas le message Python brut à l'utilisateur (il pourrait contenir des détails internes).
- Les erreurs HTTP de Flask (404 page inconnue, 405 mauvaise méthode, 413 requête trop grosse) gardent leur code.

Avant, chaque route avait son propre bloc `try / except KeyError / except ValueError / except Exception`, avec des messages légèrement différents. Ces deux fonctions remplacent une dizaine de blocs identiques.

---

## 4. Routes de page et de configuration

### `GET /`

Renvoie `index.html`. Toute l'interface est dans ce fichier ; le serveur ne fait ensuite que de l'API JSON.

### `GET /api/config`

```python
return jsonify({"moneyness_min": v.MONEYNESS_MIN, "moneyness_max": v.MONEYNESS_MAX})
```
Le site lit ici les bornes de strike au chargement. Avant, la règle 50 %–150 % était écrite dans `main.py` **et** dans `index.html`, avec un commentaire « penser à changer les deux ». Maintenant elle n'existe qu'une fois, dans `validation.py`.

---

## 5. Routes de données de marché

### `GET /api/search_ticker?q=...`

Renvoie les suggestions Yahoo. Requête vide → liste vide. `search_tickers` renvoie déjà une liste vide si Yahoo ne répond pas : pas besoin de `try` ici.

### `GET /api/market/<ticker>`

```python
try:
    cotation = get_cotation(ticker)
    vix = get_vix()
except Exception as e:
    return _err(f"Impossible de récupérer les données pour '{ticker}' : {e}")
try:
    vol_hist = round(get_vol_historique(ticker), 4)
except Exception:
    vol_hist = None
```
- Spot et devise en **une seule** requête Yahoo (`get_cotation`), puis le VIX.
- Le premier `try` garde un message qui cite le ticker : plus utile que le message générique.
- **Dégradation gracieuse :** si l'historique est insuffisant (introduction en Bourse récente), la vol historique vaut `None` au lieu de faire échouer toute la requête. L'interface sélectionne alors automatiquement le VIX.

### `GET /api/volatility`

```python
ticker, periode, lambda_ = v.parametres_volatilite(request.args)
return jsonify(get_volatility_snapshot(ticker, periode, lambda_))
```
Deux lignes : la validation (liste blanche de périodes, 0 < λ < 1) est dans `validation.py`.

---

## 6. `POST /api/option` — options européennes et américaines

```python
p = v.parametres_option(request.get_json())
marche = (p["S0"], p["K"], p["T"], p["r"], p["q"], p["sigma"], p["option_type"])
if p["style"] == "american":
    price = longstaff_schwartz(*marche, p["n_simulations"], seed=p["seed"])
elif p["method"] == "monte_carlo":
    reponse["monte_carlo"] = monte_carlo(*marche, p["n_simulations"], seed=p["seed"])
    price = reponse["monte_carlo"]["price"]
else:
    price = black_scholes(*marche)
```
- `marche` regroupe les sept paramètres communs ; `*marche` les déplie dans l'appel. Ça évite de recopier sept arguments à chaque fonction.
- Européenne : formule fermée ou simulation (au choix, pour comparer). En Monte Carlo, la réponse contient aussi l'**IC95** : l'étudiant voit si Black-Scholes tombe dedans.
- Américaine : obligatoirement Longstaff-Schwartz (pas de formule fermée pour un put américain).
- **Graine** : Monte Carlo et Longstaff-Schwartz sont désormais reproductibles (même résultat à chaque clic).
- Grecques Black-Scholes **européennes**, même pour une américaine : approximation assumée.

```python
sign = 1 if p["side"] == "buy" else -1
pnl = payoff - sign * price
```
Un vendeur encaisse la prime et a un payoff négatif : un seul signe gère les deux sens.

## 7. `POST /api/digital`

- Prix Black-Scholes **et** prix Monte Carlo avec son IC95.
- Grecques multipliées par le signe (vendeur = grecques opposées).
- `_grid(..., sauts=[K])` : la marche au strike est dessinée verticalement.
- `condition = grille > K` : inégalité stricte, cohérente avec le moteur.

## 8. `POST /api/forward`

Prix à terme, valeur du contrat et graphe. `forward_payoff` accepte directement toute la grille (calcul vectorisé). Le P&L est `payoff − valeur` : il n'y a pas de prime pour un forward, mais un contrat signé hors marché (K ≠ F) a déjà une valeur.

## 9. `POST /api/strategy`

```python
STRATEGY_BUILDERS = {
    "covered_call": lambda s, p: make_covered_call(s["K"], p["T"], qty=p["qty"]),
    ...
}
legs = STRATEGY_BUILDERS[p["strategie"]](p["strikes"], p)
```
**Table de dispatch :** un dictionnaire « nom → fonction » plutôt qu'une longue chaîne de `if/elif`. `s` contient les strikes lus par `validation.parametres_strategie` (et seulement ceux-là), `p` le reste. Ajouter une stratégie = une ligne ici et une ligne dans `validation.STRIKES_STRATEGIE`.

## 10. `POST /api/phoenix`

```python
p = v.parametres_phoenix(request.get_json())
nominal = p.pop("nominal")
res = analyse_phoenix(**p)
```
- `validation.parametres_phoenix` renvoie exactement les arguments du moteur, plus le nominal. `p.pop("nominal")` le retire (le moteur travaille en fraction du nominal), puis `**p` passe le reste au moteur.
- **Niveaux pour le graphe**, en prix : strike, rappel (si actif), coupon, protection. Ils servent à la fois de marqueurs et de `sauts` pour la grille.
- **Payoff à maturité** (cas non rappelé) par `calculer_remboursement_phoenix_maturite`, la même fonction que celle utilisée par le moteur pour les flux.
- **P&L du client = payoff − nominal** : le client paie 100 % du nominal, pas le prix théorique.

## 11. `POST /api/autocall`

Même structure que le Phoenix :
- `calculer_remboursement_autocall_maturite` pour la courbe de remboursement (avant, cette règle était recopiée dans `main.py`) ;
- `_reponse_structure` pour les indicateurs communs ;
- champs propres au Plain : `nombre_moyen_coupons`, `coupon_pct`, `first_call_date`, etc.

## 12. `POST /api/accumulateur` et `/api/decumulateur`

```python
@app.post("/api/accumulateur")
@app.post("/api/decumulateur")
def api_accumulation():
    produit = "decumulateur" if request.path.endswith("decumulateur") else "accumulateur"
```
**Une seule fonction pour deux routes** (deux décorateurs empilés) : le produit est déduit de l'URL. `mode` vaut `"analyse"` (K donné → valeur) ou `"zero_cout"` (cherche le K qui annule la valeur). Dans les deux cas, la réponse contient `K`.

## 13. `POST /api/dra`

```python
arguments, rho, sensibilite = v.parametres_dra(request.get_json())
resultat = analyse_dra(**arguments, rho=rho)
if sensibilite:
    resultat["sensibilite_correlation"] = sensibilite_correlation(**arguments)
```
Les flux par date sont calculés par le moteur lui-même ; la route ne fait plus que les transmettre.

---

## 14. Contrôle d'entrée et instantanés pour le PDF

### `@app.before_request`

```python
if not request.is_json:
    return _err('Un objet JSON est requis.', 415)
data = request.get_json(silent=True)
if not isinstance(data, dict):
    return _err('Un objet JSON valide est requis.')
```
Toute requête POST vers l'API doit contenir un **objet** JSON. Les routes peuvent donc écrire `request.get_json()` sans se méfier. Le contenu (types, bornes) est vérifié par `validation.py`. Deux champs inconnus de l'interface sont aussi refusés explicitement (`vol_source` hors liste, `leg_vols`) pour documenter qu'une stratégie utilise une seule vol commune.

### Cache des calculs pour le PDF

**Problème à éviter :** un export PDF qui recalculerait à partir du formulaire ne correspondrait plus à l'écran dès que l'utilisateur modifie un champ après le calcul.

**Solution :** le serveur **mémorise un instantané** (paramètres + résultats) de chaque calcul, identifié par un `calculation_id`. Le PDF relit cet instantané, sans recalcul.

- `uuid4().hex` : identifiant aléatoire impossible à deviner (on ne peut pas récupérer le calcul d'un autre en incrémentant un numéro).
- `OrderedDict` : garde l'ordre d'insertion → on supprime facilement les plus anciens (`popitem(last=False)`).
- `_NB_MAX_CALCULS = 20`, `_DUREE_DE_VIE = 1800` secondes : la mémoire ne grossit pas indéfiniment. Les deux valeurs sont nommées une fois et réutilisées par l'export.
- `monotonic()` plutôt que l'heure système : une horloge monotone ne recule jamais (changement d'heure, synchronisation NTP).
- `RLock` : verrou pour que deux requêtes simultanées ne modifient pas le dictionnaire en même temps.

**Limite :** ce cache vit dans la mémoire du processus. Un redémarrage l'efface (d'où l'erreur 410) et il ne serait pas partagé entre plusieurs processus en production.

### `@app.after_request`

```python
response.headers['X-Content-Type-Options'] = 'nosniff'
response.headers['Cache-Control'] = 'no-store'
json.dumps(data, allow_nan=False)
```
- `nosniff` : le navigateur n'essaie pas de deviner le type du contenu (bonne pratique de sécurité).
- `no-store` : un prix ne doit jamais être servi depuis un cache.
- **Dernier filet :** si un `NaN` ou un `inf` s'est glissé dans un résultat, le JSON standard serait invalide (`NaN` n'existe pas en JSON). On le détecte ici et on renvoie une erreur propre. C'est le **seul** contrôle de finitude des résultats : les moteurs n'en refont pas.

### `POST /api/export_pdf`

- Relit l'instantané ; s'il est absent ou expiré : code **410 Gone** (« la ressource a existé mais n'existe plus »), plus précis qu'un 404.
- `reportlab` est importé à la demande dans `pdf_engine` : s'il n'est pas installé, le pricer fonctionne quand même ; seul l'export renvoie un message (503) qui dit quoi installer.
- `send_file(BytesIO(contenu), ...)` : le PDF est construit en mémoire, jamais écrit sur le disque.

---

## 15. Lancement

```python
if __name__ == "__main__":
    app.run(host="127.0.0.1", debug=False, use_reloader=False)
```
- `127.0.0.1` : accessible uniquement depuis votre ordinateur, pas depuis le réseau.
- `debug=False` : le mode debug de Flask expose une console Python dans le navigateur en cas d'erreur, dangereux si la page est accessible à d'autres.
- `use_reloader=False` : le rechargement automatique lance **deux** processus, ce qui dédoublerait le cache des calculs (un PDF demandé au mauvais processus ne trouverait pas son calcul).

Contrepartie : après une modification du code, il faut relancer `python main.py` à la main.
