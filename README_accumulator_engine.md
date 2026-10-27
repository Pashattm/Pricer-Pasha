# `accumulator_engine.py` — accumulateur, décumulateur et strike zéro coût

## 1. Le produit

### Accumulateur (« I kill you later »)

Un client veut acheter progressivement une action, moins cher que le marché. La banque lui propose :

- à chaque constat (ex. mensuel), il **achète** `quantite` actions au prix **K**, inférieur au spot initial (ex. K = 95 pour S0 = 100) ;
- si le cours passe **sous K**, il doit acheter **`levier` fois** la quantité (souvent ×2), toujours à K, donc au-dessus du marché ;
- si le cours dépasse une **barrière haute B_KO** (ex. 110), le contrat s'arrête (**knock-out**) : la banque limite ainsi les gains du client.

**Asymétrie :** gains plafonnés par le KO, pertes doublées par le levier et non plafonnées. En 2008, des clients privés asiatiques ont subi des pertes énormes avec ces produits, d'où le surnom.

**Pourquoi K est-il sous le spot ?** Il n'y a généralement pas de prime payée à l'entrée : la décote est « payée » par le put à levier que le client vend implicitement. Le **strike zéro coût** est le K qui rend la valeur du contrat nulle aujourd'hui.

### Décumulateur

Le miroir pour un vendeur : il **vend** à K > S0, doit vendre `levier` fois plus si le cours passe au-dessus de K, et le contrat s'arrête si le cours passe **sous** B_KO.

**Condition de cohérence vérifiée par le code :**
- accumulateur : K < S0 < B_KO ;
- décumulateur : B_KO < S0 < K.

---

## 2. Conventions du fichier

```
r, q et sigma en décimal, dates en années, K et B_KO en prix absolus.
Les flux sont les gains/pertes mark-to-market des achats/ventes au constat,
actualisés individuellement ; aucune mise initiale n'est déduite.
```

- **Niveaux absolus** (pas en %) : sur un desk, un accumulateur se négocie avec un strike en prix (« strike 95 euros »).
- **Pas de nominal** : la valeur est la somme actualisée des P&L. Acheter à 95 une action qui vaut 100 au constat = +5 par action.
- **Mark-to-market au constat** : on valorise chaque achat au prix du jour du constat. On ne suit pas ce que le client fait ensuite des actions.

---

## 3. Où sont les contrôles ?

Dans `validation.parametres_accumulation`, comme pour tous les produits. Ce moteur avait auparavant sa propre fonction de validation, qui doublait celle de `main.py` avec des bornes différentes (jusqu'à 1 000 000 simulations ici, 500 000 dans la route). Elle a été fusionnée dans `validation.py`. Les règles vérifiées :

- **K < S0 < B_KO** (accumulateur) ou **B_KO < S0 < K** (décumulateur) ; en mode zéro coût, seule la position de la barrière par rapport au spot est vérifiée, puisque K est cherché ;
- quantité > 0, barrière ≥ 0, **levier ≥ 1** (un levier < 1 inverserait la logique du produit) ;
- au plus **6 millions de constats simulés** (≈ 48 Mo par matrice) ;
- **périodes garanties** entre 0 et le nombre de constats : sur les premiers constats, le KO est désactivé (le client est sûr d'accumuler un minimum) ; le levier, lui, reste actif ;
- `ko_inclut_date` : convention contractuelle du jour du KO. Si le cours touche la barrière à un constat, l'achat de ce jour a-t-il lieu ? Les contrats diffèrent, donc c'est un paramètre.

Le moteur ne contient plus que les calculs.

---

## 4. `_flux_contrat` — le cœur du moteur

```python
signe = 1 if produit == "accumulateur" else -1
```
**Une seule fonction pour les deux produits.** Le gain unitaire d'un acheteur est S − K, celui d'un vendeur K − S = −(S − K). Le signe suffit. Moins de code dupliqué = moins de divergence entre les deux produits.

Boucle sur les dates :

```python
spot = prix[:, i+1]
touche = spot >= B_KO if signe == 1 else spot <= B_KO
ko = actif & touche & (i >= n_garanties)
```
- KO par le haut pour l'accumulateur, par le bas pour le décumulateur.
- `i >= n_garanties` : pas de KO pendant les périodes garanties.

```python
operation = actif if ko_inclut_date else actif & ~ko
```
Selon la convention, l'opération du jour du KO a lieu ou non.

```python
gain_unitaire = signe*(spot-K)
volume = quantite*np.where(gain_unitaire < 0, levier, 1)*operation
flux_date = volume*gain_unitaire
pnl += flux_date*np.exp(-r*date)
```
- **Levier appliqué seulement quand le gain unitaire est négatif** (scénario défavorable au client).
- `*operation` : multiplier par un booléen met le volume à 0 pour les trajectoires inactives.
- Chaque flux est actualisé à sa date.

```python
sortie[ko] = date
date_ko[ko] = i
actif = actif & ~ko
```
On mémorise la date de sortie et on désactive la trajectoire pour les dates suivantes.

Le paramètre `details` (dictionnaire optionnel) collecte les flux moyens par date pour le tableau de l'interface. Il est `None` pendant la dichotomie du strike zéro coût, pour ne pas ralentir chaque évaluation.

**Point important de conception :** cette fonction **ne tire aucun nombre aléatoire**. Elle reçoit des chemins déjà simulés. C'est ce qui rend possible la dichotomie à tirages communs (section 6).

---

## 5. `_statistiques` et les fonctions d'analyse

```python
"perte_maximale_observee": float(max(0, -np.min(pnl)))
```
**Attention au vocabulaire :** c'est la pire perte **observée dans l'échantillon**, pas une borne théorique. La perte théorique d'un accumulateur n'a pas de plafond simple (le cours peut tomber à 0 sur toutes les dates restantes). L'interface le rappelle : « Ce montant ne constitue pas une limite contractuelle de perte. »

L'histogramme (40 classes) donne la distribution des P&L : on y voit nettement la **queue gauche épaisse** due au levier.

```python
def analyse_accumulation(produit, S0, r, q, sigma, obs_dates, K, B_KO, ...):
    chemins = simuler_cours_observations(S0, r, q, sigma, dates, n_simulations, np.random.default_rng(seed))
    flux = _flux_contrat(chemins, dates, r, K, B_KO, quantite, levier, produit, ...)
    return {**_statistiques(*flux, dates), **details}
```
`analyse_accumulation` enchaîne simulation (avec le simulateur commun, `simulation_engine.py`) → flux → statistiques. **Une seule fonction pour les deux produits** : il y avait auparavant `analyse_accumulateur` et `analyse_decumulateur`, deux portes d'entrée identiques à un mot près ; c'est maintenant le paramètre `produit` qui fait la différence, comme dans `_flux_contrat`.

La valeur est renvoyée sous le nom `prix`, comme pour tous les autres produits (elle était auparavant renvoyée deux fois, sous `prix` et `valeur_contrat`).

---

## 6. `strike_zero_cout` — trouver K tel que la valeur soit nulle

### Le problème

On cherche K tel que E[P&L actualisé] = 0. La valeur est monotone en K (plus K est bas, plus le contrat est favorable à l'acheteur), donc une **dichotomie** convient.

**Problème rencontré :** si chaque évaluation de la valeur relance une simulation, le bruit Monte Carlo change d'une évaluation à l'autre. Près de la racine, la valeur est petite, et le bruit peut inverser son signe : la dichotomie part dans la mauvaise direction et ne converge pas.

**Contournement : tirages communs (common random numbers).**

```python
chemins = simuler_cours_observations(S0, r, q, sigma, dates, n_simulations, np.random.default_rng(seed))
def evaluer(K):
    return _flux_contrat(chemins, dates, r, K, B_KO, ...)
```
On simule **une seule fois**, puis on réévalue les flux sur les mêmes chemins pour chaque K. La valeur devient une fonction déterministe (et monotone) de K : la dichotomie converge à coup sûr.

### Ligne par ligne

```python
if produit == "accumulateur":
    bas, haut = S0*1e-12, np.nextafter(S0,0)
else:
    bas, haut = np.nextafter(S0,np.inf), max(S0*2, float(np.max(chemins))*1.01)
```
- Intervalle de recherche **strictement** du bon côté du spot. `np.nextafter(S0, 0)` = le plus grand flottant strictement inférieur à S0.
- Pour le décumulateur, la borne haute dépasse tous les cours simulés : au-delà, le gain est toujours positif pour le vendeur.

```python
if valeur_bas*valeur_haut > 0:
    raise ValueError("Aucun strike zéro coût dans le domaine admissible ...")
```
Si la valeur a le même signe aux deux bornes, il n'y a pas de racine dans l'intervalle (par exemple si le levier est trop faible et la barrière trop proche). Le code le **dit** au lieu de renvoyer un chiffre absurde.

```python
if np.max(evaluer(S0*.95 if produit == "accumulateur" else S0*1.05)[1]) == 0:
    raise ValueError("Aucune opération simulée : strike zéro coût indéterminé.")
```
Si aucune trajectoire ne fait d'opération (KO immédiat partout), toute valeur de K donne 0 : pas de solution unique. On teste avec un strike plausible (95 % ou 105 % du spot).

Ces deux `ValueError` ne sont pas des erreurs de saisie mais des **résultats** du calcul : `main.py` les transforme en message 400 comme les autres.

```python
for iteration in range(1, max_iterations+1):
    K = (bas+haut)/2
    valeur = float(np.mean(evaluer(K)[0]))
    if valeur == 0 or haut-bas <= tolerance*S0:
        break
    if valeur*valeur_bas > 0:
        bas = K; valeur_bas = valeur
    else:
        haut = K
else:
    raise ValueError("La dichotomie n'a pas convergé.")
```
- Dichotomie classique : on garde la moitié d'intervalle où le signe change.
- Tolérance **relative au spot** (`tolerance*S0`) : la même précision a du sens pour une action à 5 € ou à 5 000 €.
- Le `for ... else` de Python : le bloc `else` ne s'exécute que si la boucle n'a **pas** été interrompue par `break`.

Le résultat contient le strike trouvé sous le nom `K` (le même nom qu'en mode analyse) et le nombre d'itérations.

**Ce que la tolérance ne garantit pas :** le strike trouvé est exact **pour cet échantillon** de trajectoires. Avec une autre graine, il bougera légèrement. La docstring le précise : « tolerance est la précision relative sur le strike, pas une garantie statistique ». L'IC95 renvoyé autour de la valeur résiduelle donne l'ordre de grandeur de cette incertitude.

---

## 7. Exercices pour les étudiants

1. Passez le levier de 2 à 1 : le strike zéro coût remonte vers le spot. Pourquoi ?
2. Augmentez la vol : le strike zéro coût baisse-t-il ou monte-t-il ? (Indice : qui est vendeur d'optionnalité ?)
3. Comparez l'histogramme des P&L avec et sans levier : où se trouve le risque ?
