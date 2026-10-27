# `dra_engine.py` — Dual Range Accrual sur deux actions corrélées

## 1. Le produit

Un **Range Accrual** paie un coupon proportionnel au nombre de jours où le sous-jacent reste dans une fourchette [L ; U]. Le **Dual** Range Accrual exige que **deux** sous-jacents soient **simultanément** dans leurs fourchettes respectives.

Exemple : coupon maximum 8 % par an, payé trimestriellement. Si, sur un trimestre de 63 jours, les deux actions sont ensemble dans leurs fourchettes 40 jours, le coupon du trimestre vaut
`nominal × 8 % × 0,25 × 40/63`.

Le capital est **garanti à maturité** dans le modèle (hors défaut de l'émetteur).

**Ce qu'il faut comprendre :** le prix dépend fortement de la **corrélation**. Si les deux actions bougent ensemble (ρ élevé), quand l'une est dans sa fourchette l'autre a plus de chances d'y être aussi : le produit vaut plus. C'est un produit typique pour apprendre que la corrélation est un paramètre de prix à part entière.

---

## 2. Pourquoi un moteur séparé ?

- **Constats quotidiens** (252 par an) au lieu de quelques dates : la contrainte mémoire change de nature.
- **Deux sous-jacents** : il faut des tirages corrélés.

Le moteur **réutilise** pourtant le simulateur commun (`simulation_engine.simuler_cours_correles`), pour qu'il n'y ait qu'une seule formule de diffusion dans tout le projet.

---

## 3. Où sont les contrôles ?

Dans `validation.parametres_dra`. Ce moteur avait auparavant deux fonctions de validation (`_valider_simulation` et `valider_parametres_dra`) qui doublaient en partie celle de `main.py`, avec des plafonds différents (1 000 000 simulations dans le moteur, 200 000 dans la route). Tout est maintenant dans `validation.py` :

- `jours_par_an ≤ 366`, `frequence_paiement ≤ jours_par_an` (on ne peut pas payer plus souvent qu'on observe) ;
- **ρ strictement entre −1 et 1** : à ±1, les deux actifs sont parfaitement liés et le produit devient dégénéré ;
- `0 ≤ L < spot < U` : chaque actif doit **démarrer dans sa fourchette**, sinon le produit n'a pas de sens commercial ;
- coupon annuel entre 0 et 100 %, nominal ≤ 10¹² ;
- **plafond de 60 millions de constats** (simulations × jours) et budget de 250 millions quand la sensibilité est demandée (8 calculs).

## 4. `n_constats_dra(T, jours_par_an)`

```python
return max(1, int(round(T*jours_par_an)))
```
Nombre de constats quotidiens. Cette petite fonction est utilisée par le moteur **et** par `validation.py` (pour le budget de calcul) : le calcul du nombre de jours n'est écrit qu'une fois.

## 5. Plus d'adaptateur

Il existait une fonction `simulate_correlated_paths` qui validait les paramètres, reconstruisait la grille de dates **à chaque lot**, puis appelait le simulateur. La validation est partie dans `validation.py` et la grille de dates est désormais construite une seule fois par `_calendrier` : `analyse_dra` appelle directement `simuler_cours_correles`.

## 6. `_calendrier` — rattacher chaque jour à une période de coupon

```python
paiements = np.minimum(np.arange(1, int(np.ceil(T*frequence))+1)/frequence, T)
jours = np.arange(1, n_jours+1)*T/n_jours
groupes = np.searchsorted(paiements, jours-1e-12, side='left')
effectifs = np.bincount(groupes, minlength=len(paiements))
durees = np.diff(np.r_[0., paiements])
return paiements, jours, groupes, durees
```

1. **Dates de paiement** : 0,25 ; 0,5 ; 0,75 ; 1 pour un an trimestriel. `np.minimum(..., T)` gère une **dernière période courte** (ex. T = 1,1 an : la dernière période va de 1 à 1,1).
2. **Dates des constats** quotidiens (renvoyées pour servir directement au simulateur).
3. `np.searchsorted` trouve, pour chaque jour, l'indice de la première date de paiement ≥ ce jour : c'est sa période. Le `-1e-12` évite qu'un jour tombant **exactement** sur une date de paiement soit envoyé dans la période suivante à cause d'un arrondi flottant.
4. `np.bincount` compte les jours par période.
5. `durees` = longueur de chaque période en années (sert à proratiser le coupon annuel).

```python
if np.any(effectifs==0):
    raise ValueError("Une période de coupon ne contient aucun constat ...")
```
Avec 4 constats par an et 12 paiements par an, certaines périodes seraient vides : division par zéro évitée par un message clair.

## 7. `analyse_dra` — le calcul

### 7.1 Problème de mémoire et découpage en lots

**Problème rencontré :** 50 000 trajectoires × 253 dates × 2 actifs en `float64` ≈ 200 Mo, plus les matrices booléennes et les intermédiaires. Sur un portable, ça rame ou ça plante.

**Contournement :**
```python
taille_lot = min(4000, max(1, 1_100_000//(n_jours+1)))
for debut in range(0, n_simulations, taille_lot):
    fin = min(debut+taille_lot, n_simulations)
    p1, p2 = simuler_cours_correles(S1_0, S2_0, r, q1, q2, sigma1, sigma2, rho, jours, fin-debut, rng)
```
- Lots d'au plus 4 000 trajectoires et environ 1,1 million de points.
- Le **même générateur `rng`** est poursuivi d'un lot à l'autre : avec la même graine et la même taille de lot, le résultat est reproductible. Attention : changer la taille de lot change l'ordre des tirages (z1 puis z2 par lot), donc le prix bouge légèrement, dans l'IC95.
- Les résultats par trajectoire (`valeurs`, `comptes`, `coupons_totaux`) sont rangés dans des tableaux pré-alloués `np.empty(n_simulations)` : on ne garde **pas** les chemins, seulement ce dont les statistiques ont besoin.

### 7.2 Jours « dans les fourchettes »

```python
dans = (p1[:,1:]>=L1)&(p1[:,1:]<=U1)&(p2[:,1:]>=L2)&(p2[:,1:]<=U2)
comptes[debut:fin] = np.sum(dans, axis=1)
```
- `[:,1:]` : on exclut la colonne 0 (aujourd'hui), car il n'y a pas de constat au jour initial.
- Bornes **incluses** (`>=`, `<=`).
- Matrice booléenne : `True` si les **deux** actifs sont dans leurs fourchettes ce jour-là.

### 7.3 Coupons par période

```python
for i in range(len(paiements)):
    coupons[:,i] = nominal*taux_annuel*durees[i]*np.mean(dans[:,groupes==i], axis=1)
```
Coupon = nominal × taux annuel × durée de la période × **fraction** des jours de la période passés dans les fourchettes. La moyenne d'un booléen donne directement cette fraction.

### 7.4 Valeur par trajectoire

```python
valeurs[debut:fin] = nominal*np.exp(-r*T) + coupons@actualisation
```
- Capital garanti actualisé depuis la maturité.
- `coupons @ actualisation` : produit matriciel = somme des coupons, chacun actualisé à **sa** date de paiement. Une ligne au lieu d'une boucle.

### 7.5 Statistiques et histogramme

```python
nb_classes = min(n_jours+1, 60)
bornes = np.linspace(-.5, n_jours+.5, nb_classes+1)
```
Le nombre de jours accrued est un **entier**. Des bornes en demi-entiers (−0,5 ; 0,5 ; 1,5…) centrent chaque classe sur un entier et évitent qu'un nombre de jours tombe pile sur une frontière. Au-delà de 60 classes, on regroupe pour rester lisible.

Indicateurs renvoyés, avec leur lecture :

| Champ | Lecture |
|---|---|
| `prix` | Valeur du produit en devise du nominal |
| `proba_dans_fourchettes` | Fraction moyenne des jours où les deux actifs sont dans leurs fourchettes |
| `coupon_max`, `prix_coupon_max` | Ce que paierait le produit si les deux restaient toujours dans les fourchettes (borne haute) |
| `coupon_moyen_actualise` | Prix − capital actualisé = valeur de l'optionnalité coupon |
| `obs_dates` | Dates de paiement (même nom que pour les autres produits) |
| `flux_attendus_par_date` | Paiement moyen à chaque date : coupon moyen, plus le capital à la dernière date |
| `flux_attendus_actualises_par_date` | Les mêmes flux, actualisés à leur date |

```python
flux = somme_coupons/n_simulations
flux[-1] += nominal
"flux_attendus_actualises_par_date": (flux*actualisation).tolist()
```
Ces flux étaient auparavant reconstitués dans `main.py` **et** dans le navigateur, à partir de `coupons_moyens_par_date` et `dates_paiement`. Ils sont maintenant calculés une seule fois, ici, et renvoyés sous les mêmes noms que pour le Phoenix ou l'accumulateur : le tableau « flux attendus » et le PDF fonctionnent sans cas particulier.

## 8. `sensibilite_correlation`

**Problème rencontré :** en recalculant le prix pour ρ = −0,9 ; −0,6 ; … ; 0,9 avec des tirages indépendants, la courbe sortait en dents de scie : le bruit Monte Carlo était du même ordre que l'effet de la corrélation.

**Contournement :**
```python
rhos = np.linspace(-.9, .9, 7)
resultats = [analyse_dra(..., float(rho), ..., seed) for rho in rhos]
```
- **Même graine** pour chaque ρ (elle est toujours fournie par `validation.py`, 42 par défaut).
- **Mêmes lots** (la taille de lot ne dépend pas de ρ).
- Les normales sous-jacentes sont donc identiques ; seule la combinaison ρ·z1 + √(1−ρ²)·ε change. Le bruit est presque le même à chaque point et s'annule dans les **différences** : la courbe devient lisse.

`impact_prix_extremes` = prix(ρ = 0,9) − prix(ρ = −0,9) : un seul chiffre pour résumer la sensibilité.

**Limite de temps :** 7 prix au lieu d'un. `main.py` impose donc un budget de 250 millions de constats en comptant le facteur 8 (1 prix + 7 points).

La docstring précise : « La courbe n'est pas supposée monotone. » Avec des fourchettes asymétriques, l'effet de la corrélation peut s'inverser : il ne faut pas conclure trop vite.

---

## 9. Exercices

1. Fixez ρ = 0 puis ρ = 0,8 : de combien le prix change-t-il ? Est-ce plus que l'IC95 ?
2. Resserrez les fourchettes de ±20 % à ±10 % : que devient `proba_dans_fourchettes` ?
3. Pourquoi la vol augmente-t-elle rarement la valeur d'un Range Accrual ? (Indice : on est payé quand le cours ne bouge pas trop.)
