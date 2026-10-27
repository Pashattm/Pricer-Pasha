# `strutu_engine.py` — Phoenix et Autocall Plain

Ce moteur price les produits autocallables. Il n'existe pas de formule fermée pour ces produits : le flux dépend du **chemin** suivi par le sous-jacent (rappel anticipé, coupons conditionnels, effet mémoire). On simule donc des milliers de trajectoires (avec `simulation_engine.py`), on suit les flux trajectoire par trajectoire, puis on fait la moyenne actualisée.

**Conventions :**
- `r`, `q`, `sigma`, coupons en décimal ; dates en années.
- Phoenix : barrières en **fraction du strike de référence** (`strike_pct × S0`).
- Autocall Plain : barrières en **fraction de S0**.
- Tous les montants sont en **fraction du nominal** (`1` = 100 %). C'est `main.py` qui multiplie par le nominal.
- Aucun contrôle de paramètre ici : `validation.py` s'en charge.

Les deux analyses suivent les **mêmes étapes** : calendrier → générateur aléatoire → cours simulés → flux → statistiques. Tout ce qui est commun aux deux produits est écrit une seule fois (section 2) ; seules les fonctions de flux restent séparées, parce que les règles de coupon sont réellement différentes.

---

## 1. Les produits en quelques mots

### Phoenix autocallable

Exemple : 3 ans, observations semestrielles, coupon 4 % par semestre, rappel 100 %, coupon 70 %, protection 60 %.

À chaque date d'observation :
- **Si le sous-jacent ≥ 100 %** du niveau initial (hors dernière date) : le produit s'arrête, le client récupère 100 % + le coupon.
- **Si ≥ 70 %** : le client touche le coupon de 4 %.
- **Avec effet mémoire** : un coupon manqué est récupéré dès qu'une date ultérieure repasse au-dessus de 70 %.

À maturité, si le produit n'a pas été rappelé :
- ≥ 60 % : 100 % du capital (+ coupon si ≥ 70 %) ;
- < 60 % : le client perd comme le sous-jacent (ex. finit à 45 % → récupère 45 %).

**Économiquement**, le client a **vendu un put down-and-in** (la protection à 60 %) à la banque et touche en échange des coupons élevés. Plus la vol est forte, plus ce put vaut cher, plus les coupons offerts sont élevés.

### Autocall Plain (coupons accumulés)

Pas de coupon versé aux dates intermédiaires. Les coupons **s'accumulent** et sont payés d'un coup :
- au rappel (k-ième date : 100 % + k × coupon) ;
- à maturité, **tous** les coupons si le seuil final est atteint, **aucun** sinon.

Option `has_autocall=False` : plus de rappel anticipé, tout se joue à maturité.

---

## 2. Briques communes

### `barrieres_de_rappel(K_auto, step_down, n_dates)`

```python
return K_auto - step_down*np.arange(n_dates)
```
Barrière de rappel à chaque date : K_auto, K_auto − s, K_auto − 2s… Le **step-down** abaisse la barrière dès le deuxième constat : le produit a plus de chances d'être rappelé avec le temps.

Cette formule était écrite trois fois (flux du Phoenix, flux de l'Autocall, validation dans `main.py`). Elle est maintenant utilisée par les flux, par l'affichage (`barrieres_rappel`) et par `validation.step_down`.

### `calculer_remboursement_phoenix_maturite(...)`

```python
remboursement = np.where(ST >= B_pdi * strike_reference, 1.0, ST / strike_reference)
coupon = np.where(ST >= B_phoenix * strike_reference, coupon_final, 0.0)
bonus = np.where(ST >= bonus_threshold * strike_reference, bonus_amount, 0.0)   # si un seuil est fixé
```
Remboursement à maturité d'un Phoenix **non rappelé**, en fonction de S_T :
- au-dessus de la protection : 100 % ;
- en dessous : perte proportionnelle à la baisse depuis le **strike** (pas depuis la barrière) : 45 % → 45 %, pas 45/60 ;
- coupon et bonus éventuels.

**La règle n'est écrite qu'ici.** Elle sert au graphe (`main.py`) **et** aux flux (section 3). Avant, la même formule était recopiée dans la fonction de flux : rien ne garantissait que la courbe affichée corresponde au prix calculé.

**Limite pour le graphe :** avec l'effet mémoire, le coupon final dépend des coupons manqués avant, donc du chemin. Impossible de le représenter en fonction du seul S_T : le graphe ne compte que le dernier coupon, et l'interface l'écrit en note.

### `calculer_remboursement_autocall_maturite(ST, S0, K_auto, B_pdi, coupons_accumules)`

```python
capital = np.where(ST >= B_pdi * S0, 1.0, ST / S0)          # si une protection existe, sinon 1
coupons = np.where(ST >= K_auto * S0, coupons_accumules, 0.0)
```
Même idée pour l'Autocall : tous les coupons accumulés si S_T ≥ seuil, sinon aucun ; capital protégé ou non. Avant, cette règle était recopiée dans `main.py` pour le graphe.

### `_calculer_statistiques(res, dates, K_auto, step_down)`

Reçoit le résultat d'une fonction de flux et calcule tout ce qui est commun aux deux produits :

```python
prix = float(np.mean(flux_actualises))
erreur_type = float(np.std(flux_actualises, ddof=1) / np.sqrt(n_simulations))
"ic95": (prix - 1.96 * erreur_type, prix + 1.96 * erreur_type)
"marge_emetteur": 1.0 - prix
```

| Statistique | Lecture |
|---|---|
| `prix` | Valeur théorique en % du nominal |
| `ic95` | Intervalle de confiance Monte Carlo à 95 % |
| `marge_emetteur` | 100 % − valeur : ce que la banque garde (avant crédit, frais, couverture) |
| `proba_rappel_par_date` | Part des trajectoires rappelées à chaque date |
| `proba_maturite` | Part des trajectoires jamais rappelées |
| `proba_perte_capital` | Part qui finit sous la protection |
| `remboursement_moyen_si_perte` | Capital moyen récupéré **sachant** qu'il y a perte (hors coupons et bonus) |
| `duree_de_vie_moyenne` | Date de sortie moyenne, en années |
| `coupons_moyens_actualises` | Valeur actuelle moyenne des coupons |
| `flux_attendus_par_date` | Paiement moyen à chaque date (toutes trajectoires) |
| `barrieres_rappel` | Barrière effective à chaque date, après step-down |

- `remboursement_moyen_si_perte` vaut `None` s'il n'y a aucune perte dans l'échantillon, pour éviter une moyenne vide (`nan`) qui casserait le JSON.
- `dates[np.maximum(date_rappel, 0)]` : `np.maximum(..., 0)` évite l'indice `-1` (qui pointerait sur la dernière date en Python) pour les trajectoires non rappelées.

**Pourquoi la marge est-elle importante pédagogiquement ?** Elle montre qu'un produit vendu 100 % vaut souvent 95–97 % en théorie : l'écart rémunère la structuration, la couverture et le risque de crédit.

---

## 3. `_calculer_flux_phoenix` — le cœur du Phoenix

### 3.1 Préparation

```python
strike_reference = strike_pct * S0
B_phoenix_level = B_phoenix * strike_reference
rappel_levels = barrieres_de_rappel(K_auto, step_down, n_dates) * strike_reference
```
On convertit les barrières en **niveaux de prix** une fois pour toutes. `strike_pct` permet un produit dont le niveau de référence n'est pas le spot du jour (ex. strike à 95 %).

| Variable | Contenu |
|---|---|
| `flux_actualises` | Tout ce que le client reçoit, actualisé, par trajectoire |
| `coupons_actualises` | Uniquement la part coupons |
| `rappele` | `True` dès que la trajectoire est rappelée |
| `date_rappel` | Indice de la date de rappel, `-1` si jamais |
| `coupons_en_attente` | Coupons manqués, pour l'effet mémoire |
| `flux_attendus` | Moyenne des paiements à chaque date |

### 3.2 Boucle sur les dates

```python
actifs = ~rappele
```
On ne paie plus rien à une trajectoire déjà rappelée. Toutes les conditions sont combinées avec `actifs &`.

**Rappel :**
```python
if has_autocall and k < n_dates:
    autocall_now = actifs & (S_k >= rappel_levels[k - 1])
```
**`k < n_dates` : le rappel est bloqué à la dernière date.** Problème rencontré : un produit « rappelé » à maturité ne passait jamais par le remboursement final, donc le **bonus n'était jamais versé**. Désormais, la maturité passe toujours par le remboursement final (qui rembourse aussi 100 % au-dessus de la protection : le résultat économique est le même, plus le bonus).

**Coupon :**
```python
coupon_du = actifs & (S_k >= B_phoenix_level)
if memory:
    coupons_en_attente += np.where(actifs, coupon_periode, 0.0)
    montant_coupon = np.where(coupon_du, coupons_en_attente, 0.0)
    coupons_en_attente = np.where(coupon_du, 0.0, coupons_en_attente)
else:
    montant_coupon = np.where(coupon_du, coupon_periode, 0.0)
```
- Avec mémoire : on ajoute le coupon de la période au « stock » ; si la condition est remplie, on paie tout le stock et on le remet à zéro.
- Sans mémoire : un coupon raté est perdu.
- `np.where(condition, a, b)` = « si alors sinon » vectorisé, sans boucle sur les trajectoires.
- `coupon_periode = coupon_schedule[k-1]` : le coupon peut varier par date (**step-up**).

**Paiements et actualisation :**
```python
flux_actualises += montant_coupon * actualisation
flux_actualises += np.where(autocall_now, actualisation, 0.0)
```
Chaque flux est actualisé **à sa propre date** (e^(−r·t_k)), pas à la maturité. Un rappel précoce vaut donc un peu plus qu'un remboursement tardif.

```python
paiement = montant_coupon + autocall_now.astype(float)
flux_attendus[k-1] += np.mean(paiement)
```
Moyenne sur **toutes** les trajectoires (y compris celles qui ne paient rien). Conséquence vérifiable : la somme des flux actualisés par date redonne le prix.

### 3.3 Maturité

```python
final = calculer_remboursement_phoenix_maturite(S_T, S0, B_phoenix, B_pdi, 0.0, strike_pct,
                                                bonus_threshold, bonus_amount)
remboursement_T = np.where(non_rappelees, final["total"], 0.0)
```
- On réutilise la fonction de la section 2 : la règle de maturité est la même que celle du graphe, par construction.
- `coupon_final = 0.0` : le coupon de la dernière date a **déjà** été traité dans la boucle (qui va jusqu'à `n_dates` inclus). Ici on ne gère que le capital et le bonus.
- `np.where(non_rappelees, ...)` : seules les trajectoires jamais rappelées touchent ce remboursement.

---

## 4. `_calculer_flux_autocall` — Autocall Plain

Différences avec le Phoenix :

```python
coupons_accumules = coupon * k
rappel_autorise = first_call_date is None or t_k >= first_call_date
if has_autocall and k < n_dates and rappel_autorise:
    autocall_now = actifs & (S_k >= rappel_levels[k - 1])
montant_coupon = np.where(autocall_now, coupons_accumules, 0.0)
```
- Pas de condition de coupon intermédiaire : à la date k, le stock vaut simplement k × coupon.
- `first_call_date` : période de non-rappel au début (fréquent en pratique : « premier rappel possible à 12 mois »). Les coupons s'accumulent quand même pendant cette période.
- Les coupons ne sont payés **qu'au rappel**.
- Même blocage du rappel à la dernière date que pour le Phoenix.

À maturité :
```python
final = calculer_remboursement_autocall_maturite(S_T, S0, K_auto, B_pdi, coupon * n_dates)
montant_coupon = np.where(non_rappelees, final["coupons"], 0.0)
remboursement_T = np.where(non_rappelees, final["capital"], 0.0)
```
- Tous les coupons si le seuil final (`K_auto` **sans** step-down) est atteint, sinon aucun. C'est voulu : l'interface précise « coupon final du Plain inchangé ».
- `B_pdi=None` = capital garanti à 100 % dans le modèle (case « protection » décochée).
- `nombre_coupons` mémorise combien de coupons chaque trajectoire a touchés : on en déduit `nombre_moyen_coupons`.

---

## 5. `analyse_phoenix` et `analyse_autocall_plain`

```python
coupon_schedule = np.broadcast_to(np.asarray(coupon, dtype=float), dates.shape)
prix = simuler_cours_observations(S0, r, q, sigma, dates, n_simulations, np.random.default_rng(seed))
res = _calculer_flux_phoenix(...)
return {**_calculer_statistiques(res, dates, K_auto, step_down), "coupon_schedule": coupon_schedule.tolist()}
```

1. **Calendrier de coupons** (Phoenix) : `np.broadcast_to` transforme un coupon fixe en liste constante, et laisse une liste step-up telle quelle. La fonction de flux ne gère ainsi qu'un seul cas.
2. **Graine → simulation → flux → statistiques.**
3. Chaque analyse ajoute ce qui lui est propre : le calendrier de coupons pour le Phoenix, le nombre moyen de coupons pour l'Autocall.

Pour l'Autocall, `first_call_date if has_autocall else None` : sans rappel, la date de premier rappel n'a pas de sens, on la neutralise.

La docstring de l'Autocall résume le produit en une phrase : « on attend le rappel, puis on paie d'un coup le nominal et les coupons accumulés. Ce n'est pas un Phoenix à coupons périodiques. » C'est la confusion la plus fréquente chez les étudiants.

---

## 6. Ce qui a été retiré

- **Le simulateur** a déménagé dans `simulation_engine.py` (il sert aussi aux options, à l'accumulateur et au DRA).
- **`calculer_prix_phoenix`** et les **alias anglais** (`price_phoenix`, `simulate_observation_dates`, `payoff_phoenix_maturite`) : rien ne les appelait. `calculer_prix_phoenix` n'acceptait d'ailleurs même pas le step-down.
- **Le contrôle de finitude des flux** : il doublait le contrôle de sortie de `main.py`, qui refuse tout résultat non fini pour toutes les routes.

---

## 7. Pour aller plus loin

- **Exercice :** vérifiez que `sum(flux_attendus_actualises_par_date)` est égal au prix (à l'arrondi près).
- **Exercice :** augmentez la vol de 20 % à 30 % : le prix baisse (le put vendu par le client vaut plus cher). C'est pour ça que les Phoenix sur actions volatiles offrent de gros coupons.
- **Amélioration :** grecques par différences finies avec la même graine (`seed`) pour que le bruit s'annule.
