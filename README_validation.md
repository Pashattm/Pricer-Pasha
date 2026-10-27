# `validation.py` — lecture, conversion et vérification des paramètres

C'est le **seul** endroit du projet où les paramètres envoyés par le site sont contrôlés.

```
navigateur ──JSON──► main.py ──data──► validation.parametres_xxx(data) ──dict propre──► moteur
                                              │
                                              └── ValueError("message en français") ──► 400
```

- `main.py` appelle **une fonction par produit** : `parametres_option`, `parametres_phoenix`, etc.
- Chaque fonction **lit** le JSON, **convertit** les % en décimal et les mois en années, **vérifie** tout, et renvoie un dictionnaire prêt à être passé au moteur.
- Les moteurs ne revérifient rien : leur code ne contient que des maths.
- Toute erreur lève une `ValueError` avec un message lisible ; `main.py` la transforme en réponse 400, affichée en rouge sous le bouton.

> **Pourquoi un module à part ?** Avant ce module, les mêmes contrôles existaient à trois niveaux : un contrôle générique dans `main.py` (`before_request`), un contrôle dans chaque route, et un autre dans certains moteurs (accumulateur, DRA, digitales). Les bornes n'étaient d'ailleurs pas toujours identiques (spot ≤ 10⁹ ici, ≤ 10¹² là ; 200 000 simulations pour le DRA dans la route, 1 000 000 dans le moteur). Un seul module = une seule liste de bornes, des messages homogènes, et un fichier à lire pour connaître toutes les limites du pricer.

---

## 1. Les bornes (en tête de fichier)

```python
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
FREQUENCES = (1, 2, 4, 12)
FREQUENCE_DEFAUT = 2
```

- Ce sont des bornes « de bon sens » : on n'interdit pas les marchés extrêmes, on attrape les fautes de frappe (vol de 2 500 % au lieu de 25 %).
- Les plafonds de constats (simulations × dates) protègent la mémoire : 6 millions de `float64` ≈ 48 Mo par matrice.
- Les underscores (`1_000`) rendent les grands nombres lisibles en Python.
- `STRIKES_STRATEGIE` liste les strikes utiles à chaque stratégie ; `main.py` s'en sert aussi pour centrer le graphe.

---

## 2. Lecture des champs

### `_vers_nombre(valeur, nom)`

```python
if isinstance(valeur, bool) or not isinstance(valeur, (int, float, str)):
    raise ValueError(f"{nom} doit être un nombre.")
x = float(valeur)
if not np.isfinite(x):
    raise ValueError(f"{nom} doit être un nombre fini.")
```

**Deux pièges Python d'un coup :**
1. `True` est un `int` en Python (`isinstance(True, int)` vaut `True`). Sans le test explicite, `"S0": true` serait accepté comme 1.
2. `float("nan")` passe le test `sigma <= 0` (car `NaN <= 0` vaut `False`). Il faut tester `np.isfinite`.

`float("abc")` lève une `ValueError` Python peu lisible : on la remplace par un message en français (`from None` masque l'erreur d'origine dans la trace).

### Les lecteurs

| Fonction | Rôle |
|---|---|
| `nombre(data, cle, defaut)` | Champ numérique ; absent **ou vide** → valeur par défaut, sinon erreur « Paramètre manquant » |
| `pourcentage(data, cle, defaut)` | Comme `nombre`, divisé par 100 : **la seule conversion % → décimal du projet** |
| `optionnel(data, cle)` | Champ facultatif (seuil de bonus) : `None` s'il est vide |
| `entier(data, cle, defaut)` | Refuse `50000.5` au lieu de le tronquer silencieusement en `50000` |
| `booleen(data, cle, defaut)` | Lit `true/false`, `"true"/"false"`, `"1"/"0"`, `"oui"/"non"` |
| `choix(data, cle, options, defaut, libelle)` | Valeur obligatoirement dans une liste fermée (`"call"`, `"put"`…) |

**Pourquoi « absent ou vide » ?** Un `<input>` vide envoie `""`, pas `null`. Sans ce cas, `float("")` planterait.

**Le piège de `booleen` :** `bool("false")` vaut `True` en Python (toute chaîne non vide est vraie). Si le navigateur envoyait la chaîne `"false"`, l'effet mémoire s'activait. La fonction lit explicitement les valeurs attendues et refuse le reste.

---

## 3. Contrôles communs

### `verifier_marche(S0, T, r, q, sigma=None, sigma_nulle=False)`

Limites de marché communes à tous les produits. `sigma_nulle=True` autorise une vol nulle (Autocall, accumulateur, DRA : le calcul reste défini), alors que Black-Scholes divise par σ et exige σ > 0.

### `verifier_strikes(S0, strikes)`

Strike entre 50 % et 150 % du spot. Un strike à 100 pour un spot à 300 se price, mais n'a aucun intérêt pédagogique et rend les grecques illisibles. Le site lit ces bornes via la route `/api/config` : la règle n'est écrite qu'ici.

### `simulations(data, defaut, maximum)` et `graine(data)`

- Nombre de simulations : entier entre 1 000 et le maximum du produit.
- Graine : entier entre 0 et 2³² − 1, 42 par défaut. Même graine = mêmes tirages = même prix, à chaque clic.

### `dates_observation(T, frequence)`

```python
n_obs = T * frequence
if abs(n_obs - round(n_obs)) > 1e-9:
    raise ValueError(...)
return [k / frequence for k in range(1, int(round(n_obs)) + 1)]
```

- Construit `[0.5, 1, 1.5, ...]` à partir de la maturité et de la fréquence. Les dates ne sont jamais saisies à la main : elles sont **forcément positives et croissantes**, aucun contrôle supplémentaire n'est nécessaire.
- **Tolérance 1e-9** : 2,5 × 2 peut donner 4,999999999 en flottant ; comparer `n_obs == round(n_obs)` échouerait au hasard.
- Une maturité de 13 mois en semestriel est refusée (dernière période incomplète).

### `verifier_constats(n_simulations, n_dates, maximum)`

Plafond mémoire / temps : simulations × dates d'observation.

### `step_down(data, K_auto, n_dates, actif, minimum)`

```python
niveaux = barrieres_de_rappel(K_auto, baisse, max(2, n_dates))[:max(1, n_dates - 1)]
if np.any(niveaux <= 0) or np.any(niveaux < minimum):
    raise ValueError(...)
```

- Utilise la **même** fonction que le moteur (`strutu_engine.barrieres_de_rappel`) : la règle du step-down n'est écrite qu'une fois.
- On ne vérifie que les dates où le rappel est possible (toutes sauf la maturité).
- Pour le Phoenix, `minimum = B_phoenix` : la barrière de rappel ne doit jamais passer sous la barrière coupon.
- Sans rappel, le step-down est ignoré (renvoie 0).

---

## 4. Un contrôle par produit

Chaque fonction suit le même plan : **marché → calendrier et simulations → règles du produit → dictionnaire propre**.

| Fonction | Règles propres au produit |
|---|---|
| `parametres_option` | Type, style, méthode dans une liste fermée ; σ > 0 ; strike dans la fourchette de moneyness |
| `parametres_forward` | Pas de vol ; prix contractuel K positif |
| `parametres_strategie` | Nom de stratégie connu ; on ne lit **que** les strikes utiles ; quantité > 0 |
| `parametres_digitale` | Européenne uniquement ; paiement fixe ≥ 0 ; type cash ou asset |
| `parametres_phoenix` | Barrières dans ]0 ; 300 %] ; **protection ≤ coupon ≤ rappel** (rappel seulement s'il est actif) ; step-up = un coupon par date ; chaque coupon entre 0 et 100 % ; bonus ≥ 0 |
| `parametres_autocall` | Message de migration pour l'ancien produit « sans rappel » ; protection dans ]0 ; min(100 %, barrière)] ; premier rappel entre 0 et la maturité (mois → années) |
| `parametres_accumulation` | Mode `analyse` (K donné) ou `zero_cout` (K cherché) ; **K < S0 < B_KO** (accumulateur) ou **B_KO < S0 < K** (décumulateur) ; levier ≥ 1 ; périodes garanties ≤ nombre de constats |
| `parametres_dra` | ρ strictement entre −1 et 1 ; chaque actif démarre dans sa fourchette (0 ≤ L < spot < U) ; budget de calcul qui compte les 8 calculs de la sensibilité |
| `parametres_volatilite` | Ticker non vide ; période dans une liste blanche (on ne transmet jamais une chaîne libre à Yahoo) ; 0 < λ < 1 |

### Détails à remarquer

- **Phoenix, coupon step-up** : la liste est vérifiée élément par élément avec `_vers_nombre`, puis divisée par 100.
- **Autocall, message de migration** : il existait un produit séparé « sans rappel », fusionné dans le Plain (`has_autocall=false`). Plutôt qu'un sec « produit inconnu », l'erreur explique la nouvelle manière de faire.
- **DRA** : la fonction renvoie trois choses, `(arguments, rho, sensibilite)`, car la sensibilité recalcule le prix pour 7 valeurs de ρ et ne doit pas recevoir le ρ saisi. Le nombre de constats vient de `dra_engine.n_constats_dra`, la même fonction que celle du moteur.

---

## 5. Comment ajouter un produit

1. Écrire `parametres_mon_produit(data)` sur le modèle des autres, en réutilisant `nombre`, `pourcentage`, `verifier_marche`, `simulations`, `graine`…
2. Si une nouvelle borne apparaît, l'ajouter en tête de fichier plutôt que d'écrire le nombre en dur.
3. Dans `main.py`, la route appelle cette fonction puis le moteur. Aucun `try / except` à écrire : la `ValueError` est déjà transformée en 400.
