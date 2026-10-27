# `simulation_engine.py` — le simulateur Monte Carlo commun

C'est le **seul** endroit du projet où la diffusion du sous-jacent est écrite. Tous les calculs Monte Carlo passent par lui :

| Utilisateur | Usage |
|---|---|
| `pricer_engine.monte_carlo`, `digital_monte_carlo` | Une seule date : la maturité |
| `pricer_engine.longstaff_schwartz` | 100 dates régulières (chemins complets pour l'option américaine) |
| `strutu_engine` (Phoenix, Autocall) | Les dates d'observation du produit |
| `accumulator_engine` | Les constats de l'accumulateur |
| `dra_engine` | Deux actifs corrélés, constats quotidiens |

> **Pourquoi un fichier à part ?** Au départ, le mouvement brownien géométrique était écrit **trois fois** : dans `monte_carlo`, dans `longstaff_schwartz` et dans `strutu_engine`. Les deux premières versions utilisaient `np.random.normal` sans graine : deux clics donnaient deux prix différents. Seule la version de `strutu_engine` acceptait une graine et des chocs fournis. C'est elle qui a été gardée et déplacée ici. Bonus : pour accélérer tout le projet (Numba, variables antithétiques…), il n'y a plus qu'une fonction à modifier.

---

## 1. Le modèle

Sous la probabilité risque-neutre de Black-Scholes, entre deux dates séparées de dt :

S(t + dt) = S(t) × exp[(r − q − σ²/2)·dt + σ·√dt·Z], avec Z ~ N(0, 1)

- `r − q` : le sous-jacent croît au taux sans risque moins le dividende (portage).
- `− σ²/2` : **correction d'Itô**. Sans elle, l'espérance de S serait trop haute, car E[e^(σW)] = e^(σ²t/2).
- Cette formule est **exacte** quel que soit dt : il n'y a aucune erreur de discrétisation à simuler directement de 0 à 3 ans. C'est pourquoi on ne simule que les dates utiles.

---

## 2. `simuler_cours_observations`

```python
def simuler_cours_observations(S0, r, q, sigma, obs_dates, n_simulations, rng=None, chocs=None):
    if rng is None:
        rng = np.random.default_rng()
    prix = np.zeros((n_simulations, len(obs_dates) + 1))
    prix[:, 0] = S0
    t_precedent = 0.0
    for i, t in enumerate(obs_dates):
        dt = t - t_precedent
        Z = rng.standard_normal(n_simulations) if chocs is None else chocs[i]
        croissance = (r - q - sigma**2 / 2) * dt
        choc = sigma * np.sqrt(dt) * Z
        prix[:, i + 1] = prix[:, i] * np.exp(croissance + choc)
        t_precedent = t
    return prix
```

Ligne par ligne :

- **`rng`** : générateur moderne de NumPy, passé en paramètre (`np.random.default_rng(graine)`). Avec la même graine, on retrouve exactement les mêmes trajectoires : indispensable pour comparer deux paramétrages, pour les tirages communs (strike zéro coût, sensibilité du DRA) et pour un PDF fidèle.
- **`chocs`** : permet de fournir ses propres tirages normaux, de forme (dates, simulations). Sert aux deux actifs corrélés du DRA.
- **La matrice** : une ligne par trajectoire, une colonne par date ; la colonne 0 est aujourd'hui (S0 partout).
- **`dt` variable** : les dates peuvent être irrégulières.
- **La boucle tourne sur les dates**, pas sur les trajectoires : chaque tour met à jour les 50 000 trajectoires d'un coup (vectorisation NumPy). Pour un Phoenix semestriel sur 3 ans, la boucle fait 6 tours.

Aucun contrôle de paramètre ici : `validation.py` s'en charge, et le contrôle de sortie de `main.py` (`after_request`) refuse tout résultat non fini.

---

## 3. `simuler_cours_correles`

```python
z1 = rng.standard_normal(forme)
z2 = rho*z1 + np.sqrt(max(0., 1 - rho*rho))*rng.standard_normal(forme)
prix1 = simuler_cours_observations(S1_0, r, q1, sigma1, obs_dates, n_simulations, chocs=z1)
prix2 = simuler_cours_observations(S2_0, r, q2, sigma2, obs_dates, n_simulations, chocs=z2)
```

- **Décomposition de Cholesky en dimension 2** : si z1 et ε sont indépendants et de variance 1, alors z2 = ρ·z1 + √(1 − ρ²)·ε a une corrélation ρ avec z1 et reste de variance 1.
- `max(0., ...)` protège contre un `1 − ρ²` très légèrement négatif à cause des arrondis.
- Les deux actifs sont ensuite simulés par **la même fonction** : une seule formule de diffusion.
- Le générateur est poursuivi (d'abord z1, puis ε) : la première matrice utilise les mêmes tirages qu'une simulation simple avec la même graine.

---

## 4. Exercices

1. Simulez 100 000 trajectoires à une date T = 1 et vérifiez que la moyenne de S_T·e^(−rT) vaut S0·e^(−qT) (martingale actualisée).
2. Retirez le terme `− sigma**2 / 2` : de combien la moyenne dérive-t-elle pour σ = 40 % ?
3. Simulez deux actifs avec ρ = 0,8 et calculez la corrélation empirique des log-rendements.
