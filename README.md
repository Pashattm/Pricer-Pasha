# PRICER — pricer pédagogique d'options et de produits structurés

> Auteur : Théo « Pasha » Jondeau — MBA Finance & Data Performance (ESLSCA)
> Public visé : étudiants qui découvrent à la fois **le code** et **les produits**.

Ce dépôt contient un pricer complet, utilisable dans un navigateur, qui couvre :

| Onglet | Produits | Méthodes |
|---|---|---|
| 1) Options | Call / put européens et américains, digitales cash-or-nothing et asset-or-nothing | Black-Scholes, Monte Carlo, Longstaff-Schwartz |
| 2) Forward / Future | Contrat à terme long ou short | Formule de portage |
| 3) Stratégies | Covered call, protective put, straddle, strangle, call spread, put spread, collar | Somme des jambes Black-Scholes |
| 4) Produits structurés | Phoenix, Autocall Plain, Accumulateur, Décumulateur, Dual Range Accrual | Monte Carlo |
| 5) Chantier | Laboratoire de volatilité (historique, glissante, EWMA, Parkinson) | Statistiques sur données Yahoo Finance |

Chaque calcul peut être exporté en PDF.

Ce README donne **la vue d'ensemble**. Chaque fichier a ensuite son propre README détaillé, qui reprend le code bloc par bloc :

| Fichier | Rôle | Documentation |
|---|---|---|
| `main.py` | Serveur Flask : reçoit les requêtes, appelle la validation puis le moteur, renvoie du JSON | [README_main.md](README_main.md) |
| `validation.py` | **Seul endroit** où les paramètres sont lus, convertis et vérifiés | [README_validation.md](README_validation.md) |
| `simulation_engine.py` | **Seul endroit** où la diffusion du sous-jacent (Monte Carlo) est écrite | [README_simulation_engine.md](README_simulation_engine.md) |
| `pricer_engine.py` | Options vanilles, grecques, forwards, stratégies, digitales | [README_pricer_engine.md](README_pricer_engine.md) |
| `strutu_engine.py` | Phoenix, Autocall Plain | [README_strutu_engine.md](README_strutu_engine.md) |
| `accumulator_engine.py` | Accumulateur, décumulateur, strike zéro coût | [README_accumulator_engine.md](README_accumulator_engine.md) |
| `dra_engine.py` | Dual Range Accrual sur deux actions corrélées | [README_dra_engine.md](README_dra_engine.md) |
| `market_data_engine.py` + `volatility_engine.py` | Données de marché et mesures de volatilité | [README_volatilite_market_data.md](README_volatilite_market_data.md) |
| `pdf_engine.py` | Export PDF du dernier calcul | [README_pdf_engine.md](README_pdf_engine.md) |
| `templates/index.html` | Interface : formulaires, graphiques SVG, appels à l'API | [README_index_html.md](README_index_html.md) |

**Ordre de lecture conseillé :** ce README → `README_simulation_engine.md` → `README_pricer_engine.md` (les bases : Black-Scholes, Monte Carlo) → `README_strutu_engine.md` → `README_validation.md` → `README_main.md` → les autres moteurs → `README_index_html.md`.

---

## 1. Installation et lancement

```bash
python -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\activate
pip install flask numpy pandas yfinance reportlab
python main.py
```

Puis ouvrir <http://127.0.0.1:5000>.

Arborescence attendue (Flask cherche les pages HTML dans un dossier `templates/`) :

```
pricer/
├── main.py
├── validation.py
├── simulation_engine.py
├── pricer_engine.py
├── strutu_engine.py
├── accumulator_engine.py
├── dra_engine.py
├── market_data_engine.py
├── volatility_engine.py
├── pdf_engine.py
├── requirements.txt          (flask, numpy, pandas, yfinance, reportlab)
└── templates/
    └── index.html
```

`main.py` renvoie le message « Installez les dépendances : pip install -r requirements.txt » si `reportlab` manque au moment de l'export PDF : pensez à garder ce fichier à jour.

---

## 2. Architecture : qui fait quoi

```
 Navigateur (index.html)
   │  formulaire → JSON (taux, vol, barrières en %)
   ▼
 main.py  (Flask)
   │  1. before_request : le corps est-il bien un objet JSON ?
   │  2. route /api/... ──► validation.py : lit, convertit % → décimal, vérifie TOUT
   │  3. appel du moteur avec des paramètres propres
   │  4. mise en forme de la réponse (arrondis, % du nominal, grille du graphe)
   │  5. after_request : refus des NaN, mémorisation du calcul pour le PDF
   │  (une ValueError n'importe où → réponse 400 avec le message, une seule fois pour tout le serveur)
   ▼
 Moteurs (aucune dépendance à Flask, aucun contrôle de paramètres)
   pricer_engine · strutu_engine · accumulator_engine · dra_engine
        └──────────────┬──────────────┘
               simulation_engine (la seule diffusion Monte Carlo)
   volatility_engine (maths pures) ← market_data_engine (Yahoo Finance, nettoyage des cours)
   pdf_engine (relit le calcul mémorisé, ne recalcule rien)
```

**Pourquoi cette séparation ?**

- **Chaque règle n'est écrite qu'une fois.** Une borne (vol ≤ 200 %), une formule (le mouvement brownien géométrique), une règle de remboursement (protection du Phoenix) : un seul endroit, donc une seule chose à corriger le jour où elle change. C'est le principe *DRY* (« Don't Repeat Yourself »).
- **Les moteurs ne connaissent pas le web et ne vérifient rien.** Ils reçoivent des paramètres déjà propres et ne font que des maths : leur code ressemble au cours.
- **`validation.py` est la seule porte de contrôle.** Toutes les données venant du navigateur y passent avant d'atteindre un moteur. Lire ce fichier suffit pour connaître toutes les limites du pricer.
- **Les données de marché sont isolées** dans `market_data_engine.py`. Si Yahoo Finance change d'API (c'est arrivé plusieurs fois), un seul fichier est à corriger.

### Exemple : le trajet d'un calcul Phoenix

1. L'utilisateur saisit « barrière coupon 70 % » et « vol 25 % ».
2. `index.html` construit un JSON `{"B_phoenix": "70", "sigma": "25", ...}` et l'envoie en `POST /api/phoenix`.
3. `before_request` vérifie que c'est bien un objet JSON.
4. `api_phoenix` appelle `validation.parametres_phoenix(data)` : conversion `B_phoenix = 0.70`, `sigma = 0.25`, construction des dates `[0.5, 1, 1.5, ...]`, vérification de l'ordre protection ≤ coupon ≤ rappel, etc.
5. `analyse_phoenix` simule 50 000 trajectoires (via `simulation_engine`), suit les flux, renvoie le prix en **fraction du nominal** (ex. `0.9712`).
6. `api_phoenix` multiplie par le nominal (`971.2` pour 1 000 investis), passe en % (`97.12`), construit la courbe de remboursement à maturité.
7. `after_request` vérifie qu'aucun nombre n'est infini, mémorise le calcul et ajoute un `calculation_id`.
8. `index.html` affiche les indicateurs, le graphe, le calendrier et le bouton PDF.

---

## 3. Conventions d'unités (à connaître avant de lire le code)

C'est la source d'erreurs n°1 dans un pricer. La règle :

| Où | Taux r, dividende q, vol σ | Barrières | Montants |
|---|---|---|---|
| Formulaire / JSON entrant | en % (`5` = 5 %) | en % (`70` = 70 %), sauf accumulateur et DRA : en prix absolu | en devise |
| Moteurs | en décimal (`0.05`) | Phoenix : fraction du **strike de référence** ; Autocall : fraction de **S0** ; Accumulateur / DRA : prix absolus | Phoenix / Autocall : fraction du nominal (`1` = 100 %) |
| JSON sortant | — | `_pct` = en % | champs sans suffixe = devise du nominal |

- La conversion % → décimal est faite **uniquement** dans `validation.py` (fonction `pourcentage`).
- La corrélation `rho` du DRA reste **en décimal** partout (`0.3`), car personne ne dit « 30 % de corrélation » sur un desk.
- `T` et les dates d'observation sont **en années** (`0.5` = 6 mois).
- Les coupons Phoenix / Autocall sont **par période d'observation**, pas annuels. Un coupon de 4 % semestriel ≈ 8 % par an.
- Le coupon du DRA, lui, est **annuel** (`taux_annuel`) et proratisé.

---

## 4. Pourquoi Python et pas C# ?

La question revient souvent, parce que beaucoup de banques utilisent C# (ou C++) pour leurs outils de desk et leurs librairies de pricing. Voici le raisonnement.

### Ce que Python apporte ici

1. **NumPy rend le Monte Carlo rapide sans écrire de boucle sur les scénarios.**
   Une ligne comme
   `prix[:, i + 1] = prix[:, i] * np.exp(croissance + choc)`
   met à jour 50 000 trajectoires d'un coup. La boucle réelle tourne en C à l'intérieur de NumPy. On garde la lisibilité de Python avec une vitesse proche du compilé pour ce type de calcul.
2. **Le code ressemble aux formules du cours.** `S0*np.exp(-q*T)*Ncdf(d1) - K*np.exp(-r*T)*Ncdf(d2)` se lit comme la formule de Black-Scholes. Pour un projet pédagogique, c'est décisif : un étudiant peut vérifier chaque ligne contre son support de cours.
3. **L'écosystème est déjà là :**
   - `yfinance` pour le spot, l'historique et le VIX, gratuitement ;
   - `pandas` pour les séries temporelles ;
   - `Flask` pour exposer une API en quelques lignes ;
   - `reportlab` pour le PDF.
   En C#, il faudrait trouver ou écrire l'équivalent de chacun (pas de client Yahoo officiel, par exemple).
4. **Prototypage rapide.** On teste une idée, on la colle dans le moteur. Pas de compilation, pas de projet Visual Studio.
5. **C'est le langage de la recherche quantitative et de la data.** En structuration et en quant research, les prototypes sont très majoritairement en Python ; C# / C++ servent surtout à industrialiser. Apprendre sur Python, c'est apprendre l'outil qu'on utilisera en stage.
6. **Portabilité.** Le projet tourne à l'identique sur Mac (où il a été écrit), Windows et Linux.

### Ce que C# aurait apporté (et ce qu'on a dû compenser)

| Atout de C# | Conséquence en Python | Contournement dans ce projet |
|---|---|---|
| Typage statique : un `double` ne peut pas recevoir `"abc"` ni `true` | N'importe quoi peut arriver dans le JSON : chaînes, booléens, `NaN` | Un module dédié, `validation.py`, qui convertit et vérifie chaque champ (`nombre`, `entier`, `booleen`, `choix`) |
| Boucles compilées rapides | Une boucle Python sur 50 000 scénarios × 252 jours serait très lente | Vectorisation NumPy ; seules les boucles sur les **dates** restent en Python |
| Vrai multithreading | Le GIL limite le parallélisme CPU en Python | Plafonds de calcul (6 M de constats, 60 M pour le DRA) plutôt que du parallélisme |
| Intégration Excel native (add-ins) | Pas d'intégration directe | Interface web + export PDF |

### Pourquoi ces limites n'étaient pas bloquantes

Les volumes d'un outil pédagogique (au plus quelques centaines de milliers de trajectoires, quelques dizaines de dates) tiennent en quelques secondes avec NumPy. Le jour où il faudra pricer un book entier en temps réel, la bonne étape ne sera pas de tout réécrire en C#, mais de compiler les fonctions critiques (Numba, Cython) ou de les déporter en C++ (pybind11), en gardant Python comme « colle ». Grâce au simulateur unique, il n'y aurait qu'une fonction à accélérer : `simuler_cours_observations`.

---

## 5. Limites atteintes

Ces limites sont **assumées** : elles font partie de ce qu'un étudiant doit savoir avant d'utiliser les chiffres.

### Limites de modèle

- **Black-Scholes partout.** Volatilité constante, taux constant, dividende continu, pas de sauts. Pas de smile ni de skew : un put 70 % est pricé avec la même vol qu'un call 100 %. Or le prix d'une barrière basse (Phoenix, Autocall) dépend fortement du skew : le prix affiché est **indicatif**, pas une cotation.
- **Barrières observées uniquement aux dates de constat** (discrètes). La protection n'est pas surveillée en continu. C'est la convention la plus fréquente pour les Phoenix européens, mais pas pour tous les produits.
- **Pas de risque de crédit de l'émetteur, pas de frais, pas de funding.** La « marge émetteur » affichée est simplement `100 % − valeur théorique`. Dans la réalité, le spread de crédit de la banque fait une grosse partie de cet écart.
- **Grecques des produits structurés non calculées.** Seules les options, digitales et stratégies ont des grecques. Calculer un delta de Phoenix demanderait des différences finies avec des tirages communs (voir pistes d'amélioration).
- **Option américaine :** les grecques affichées sont celles de Black-Scholes européen. C'est une approximation (exacte pour un call américain sans dividende, fausse pour un put).
- **Yang-Zhang et GARCH** sont présents comme emplacements (`pass`) dans `volatility_engine.py`, pas encore implémentés.

### Limites numériques

- **Erreur Monte Carlo en 1/√N.** Diviser l'erreur par 2 demande 4 fois plus de simulations. C'est pourquoi chaque prix Monte Carlo est accompagné d'un intervalle de confiance à 95 %.
- **Mémoire.** Une matrice de 500 000 scénarios × 12 dates en `float64` fait déjà 48 Mo ; un DRA quotidien (252 constats × 2 actifs) exploserait la RAM sans découpage en lots.
- **Temps de calcul.** Flask traite une requête à la fois dans ce mode de lancement ; un calcul de 30 s bloque l'outil 30 s.

### Limites de données

- **Yahoo Finance n'est pas une source officielle** : données parfois en retard, tickers qui changent, API non documentée qui casse sans prévenir.
- **Le VIX est la vol implicite du S&P 500**, pas celle de l'action choisie. Il sert de solution de repli quand l'historique manque (ticker récent).
- **La volatilité historique regarde le passé**, alors qu'un prix d'option dépend de la vol future (implicite).

### Limites d'architecture

- Le **cache des calculs pour le PDF vit dans la mémoire du processus**. Un redémarrage l'efface (d'où l'erreur 410 « Calcul expiré ») et un déploiement avec plusieurs processus (gunicorn à plusieurs workers) ne fonctionnerait pas sans base partagée (Redis…).
- Serveur de développement Flask, en local uniquement (`127.0.0.1`). Ce n'est pas prévu pour être mis en ligne tel quel.

---

## 6. Problèmes rencontrés et comment ils ont été contournés

C'est la partie la plus utile pour un étudiant : la plupart de ces bugs sont **silencieux** (pas de message d'erreur, juste un mauvais chiffre), ce qui est le pire type de bug en finance.

| # | Problème | Symptôme | Contournement | Où |
|---|---|---|---|---|
| 1 | `np.interp` suppose des abscisses croissantes ; on l'utilisait pour trouver les breakevens | Breakeven faux sur les P&L décroissants (put, short call), **sans erreur** | Interpolation linéaire écrite à la main entre deux points où le P&L change de signe | `_find_breakevens`, `main.py` |
| 2 | `bool("false")` vaut `True` en Python (toute chaîne non vide est vraie) | Effet mémoire activé alors que la case était décochée | Fonction `booleen` qui lit explicitement `"true"/"false"/"1"/"0"/"oui"/"non"` | `validation.py` |
| 3 | `NaN <= 0` vaut `False` : un `sigma = NaN` passe le test `sigma <= 0` ; et `True` est un `int` | Prix `NaN` renvoyé au navigateur, voire JSON invalide | `_vers_nombre` refuse booléens et non-finis ; contrôle final `json.dumps(..., allow_nan=False)` | `validation.py`, `main.py` |
| 4 | Les bornes HTML (`min`, `max`) se contournent avec les outils développeur ou une requête à la main | Paramètres absurdes envoyés au moteur | Le serveur revérifie tout dans `validation.py` ; le navigateur ne fait que du confort | `validation.py` |
| 5 | `np.polyfit` sur des prix au carré (ex. 300² = 90 000) est mal conditionné | Régression Longstaff-Schwartz instable, prix américain qui saute | Standardisation de X : `(X − moyenne) / écart-type` avant la régression | `longstaff_schwartz` |
| 6 | Un Phoenix rappelé à la dernière date ne passait jamais par le remboursement final | Bonus à maturité jamais versé | Rappel interdit à la dernière date : la maturité passe toujours par le remboursement final | `_calculer_flux_phoenix` |
| 7 | DRA : 50 000 scénarios × 252 jours × 2 actifs en mémoire | Ordinateur qui rame ou plante | Simulation **par lots** de 4 000 trajectoires (≈ 1,1 M de points max par lot) | `analyse_dra` |
| 8 | Strike zéro coût par dichotomie : chaque évaluation retirait de nouveaux nombres aléatoires | Le signe de la valeur change à cause du bruit, la dichotomie ne converge pas | **Tirages communs** : on simule une seule fois et on réévalue les flux pour chaque strike | `strike_zero_cout` |
| 9 | Sensibilité du DRA à la corrélation : courbe en dents de scie | Bruit Monte Carlo plus grand que l'effet de la corrélation | Même graine et **mêmes lots** pour chaque valeur de rho | `sensibilite_correlation` |
| 10 | Les payoffs des digitales et des structurés sautent (strike, barrières) ; une grille régulière tombe rarement pile dessus | Graphe avec une pente au lieu d'une marche | `_grid(..., sauts=...)` ajoute chaque niveau et ses voisins flottants immédiats (`np.nextafter`) | `main.py` |
| 11 | Calculs trop gros qui bloquent le serveur | Page qui tourne sans fin | Plafonds dans `validation.py` (6 M de constats, 60 M pour le DRA, 250 M avec la sensibilité) ; délai maximal dans `appelApi` | `validation.py`, `index.html` |
| 12 | L'utilisateur change de produit pendant un calcul | Résultat d'un Autocall affiché dans l'écran du Phoenix | Compteur `versionStructure` : une réponse dont la version est dépassée est ignorée | `index.html` |
| 13 | Un PDF recalculé à partir du formulaire, modifié entre-temps | PDF différent de l'écran | Le serveur mémorise **l'instantané** du calcul (`calculation_id`) ; le PDF relit cet instantané sans recalcul | `after_request`, `pdf_engine.py` |
| 14 | Volatilités 20 j, 60 j, EWMA et Parkinson n'ont pas la même longueur | Courbes décalées dans le temps sur le graphe | Toutes les séries démarrent à la première fenêtre complète de 60 rendements | `get_volatility_snapshot` |
| 15 | Initialiser l'EWMA avec la variance de tout l'échantillon (réflexe courant) | Biais d'anticipation (on utilise le futur pour calculer le passé) | Initialisation au carré du **premier** rendement seulement | `ewma_volatility_series` |
| 16 | Maturité qui ne tombe pas sur un nombre entier de périodes (ex. 13 mois en semestriel) | Dernière période fantôme | Refus explicite avec une tolérance de 1e-9 sur les flottants | `validation.dates_observation` |
| 17 | Strike à 100 pour un spot à 300 | Se price, mais n'a aucun sens pédagogique | Strike borné entre 50 % et 150 % du spot ; le site lit ces bornes via `/api/config` | `validation.verifier_strikes` |
| 18 | Effet mémoire : le coupon final dépend du chemin, pas seulement de S_T | Impossible à tracer en fonction du seul prix final | Le graphe ne montre que le dernier coupon, avec une note explicative | `calculer_remboursement_phoenix_maturite` |
| 19 | La même règle écrite à plusieurs endroits (validation en double, trois simulateurs, formules de remboursement copiées) | Une correction faite à un endroit et oubliée à l'autre ; `monte_carlo` et Longstaff-Schwartz sans graine | Nettoyage des doublons (section 7) : une règle = un endroit | tous les fichiers |

---

## 7. Doublons supprimés : ce qui a été fusionné et pourquoi

Le projet a grandi par couches : chaque nouveau produit apportait sa propre version d'une chose qui existait déjà. Une relecture a permis de garder, à chaque fois, **la meilleure version** et de supprimer les autres. Les prix sont strictement identiques avant et après ce nettoyage (vérifié route par route), sauf pour Monte Carlo et Longstaff-Schwartz, qui sont maintenant **reproductibles** grâce à une graine.

| Doublon | Version gardée | Pourquoi c'était la meilleure |
|---|---|---|
| Contrôles des paramètres dans `main.py` **et** dans les moteurs **et** dans `before_request` | `validation.py`, une fonction par produit | Une seule liste de bornes, des messages homogènes, des moteurs qui ne font que des maths |
| Trois simulations du mouvement brownien (`monte_carlo`, `longstaff_schwartz`, `simuler_cours_observations`) | `simulation_engine.simuler_cours_observations` | Elle prenait une graine et des chocs fournis ; les deux autres n'en avaient pas |
| `monte_carlo` (sans graine, sans IC) et `digital_monte_carlo` (graine + IC95) | Le schéma de `digital_monte_carlo`, partagé via `_resultat_monte_carlo` | L'IC95 permet de vérifier Monte Carlo contre Black-Scholes |
| Lecteurs `nombre()` / `entier()` recopiés dans trois routes | `validation.nombre`, `validation.entier` | Mêmes règles partout (refus des booléens, des vides, des non-finis) |
| Grecques Delta / Gamma / Vega recalculées dans `grecs_secondaires_bs` | `grecs_primaires_bs`, appelée par les secondaires | Une seule formule par grecque |
| Règle de remboursement à maturité écrite dans les flux **et** dans la fonction du graphe (Phoenix) ou dans `main.py` (Autocall) | `calculer_remboursement_phoenix_maturite` et `calculer_remboursement_autocall_maturite` | Le graphe et le prix utilisent la même règle, par construction |
| Formule de la barrière de rappel avec step-down écrite trois fois | `strutu_engine.barrieres_de_rappel` | Flux, affichage et validation lisent la même liste |
| Deux grilles « avec marche » (`_grille_digitale`, grille de l'Autocall) | `_grid(..., sauts=...)` | Une fonction, utilisée aussi pour le Phoenix qui n'en profitait pas |
| Réponses Phoenix et Autocall construites champ par champ deux fois | `_reponse_structure` | Mêmes clés, mêmes arrondis ; `erreur_type` ajouté au Phoenix au passage |
| `analyse_accumulateur` / `analyse_decumulateur` | `analyse_accumulation(produit, ...)` | Les deux ne différaient que par un mot |
| Flux du DRA calculés dans `main.py` **et** dans le navigateur | Calculés une fois dans `analyse_dra` | Le serveur est la seule source des chiffres |
| `get_spot` + `get_devise` (deux requêtes Yahoo) | `get_cotation` (une requête) | Deux fois moins d'appels réseau |
| Nettoyage des cours dans `market_data_engine` **et** dans `volatility_engine` | `market_data_engine._telecharger_historique` / `_verifier_cours` | Les cours sont nettoyés à l'entrée, une fois |
| Gestion d'erreur `try / except` recopiée dans chaque route | `@app.errorhandler(ValueError)` et `@app.errorhandler(Exception)` | Toute erreur de paramètre devient une réponse 400 au même format |
| Bornes de strike (50 %–150 %) écrites dans `main.py` **et** `index.html` | `validation.py`, lues par le site via `/api/config` | Une seule valeur à changer |
| JS : deux recherches de ticker, deux chargements de données de marché, deux façons d'appeler l'API, quatre formateurs de pourcentage, trois mécanismes « réponse obsolète » | `brancherRechercheTicker`, `chargerMarche`, `appelApi`, `fmtPct`, `versionStructure` | À chaque fois la version la plus robuste (annulation, compteur de version, `textContent`, délai maximal) |
| Champs de réponse redondants (`valeur_contrat` = `prix`, `strike` = `K`, `ecart_souscription_pct` = `marge_emetteur_pct`, `dates_paiement` = `obs_dates`) | Un seul nom par information | Le PDF et l'écran affichent la même chose sous le même nom |
| Code mort : `get_rolling_volatility`, `ewma_volatility`, `calculer_prix_phoenix`, anciens alias anglais, constantes redéfinies | Supprimé | Rien ne les appelait |

### Ce qui reste volontairement séparé

- **`_calculer_flux_phoenix` et `_calculer_flux_autocall`** : ils se ressemblent par leur structure, mais leurs règles de coupon sont différentes (coupon conditionnel versé à chaque date, avec ou sans mémoire, contre coupons accumulés payés à la sortie). Les fusionner obligerait à multiplier les `if` et rendrait chaque produit plus difficile à lire. Ce qui était réellement commun (barrières de rappel, remboursement à maturité, statistiques) est déjà partagé.
- **`renderChart` et `renderVolatilityChart`** dans `index.html` : un axe en prix et une courbe statique d'un côté, un axe en dates avec curseur interactif et navigation au clavier de l'autre. Deux besoins différents.
- **Les attributs `min` / `max` des champs HTML** : ils répètent certaines bornes du serveur, mais ils servent au confort (le navigateur signale l'erreur avant l'envoi). La règle qui fait foi reste celle de `validation.py`.

---

## 8. Dette technique restante

1. **`Lambda` (élasticité)** divise par le prix : pour une option très en dehors de la monnaie, le prix est presque nul et Lambda explose.
2. **Interface** : le JavaScript tient dans `index.html` (≈ 1 400 lignes). Au-delà, il faudrait le découper en fichiers dans `static/`.
3. **Indicateurs et graphes** : ils sont encore injectés avec `innerHTML`. Ce n'est pas dangereux ici (ce sont des nombres calculés par le serveur), mais `textContent` reste la bonne habitude.
4. **Mode « strike zéro coût »** de l'accumulateur : disponible dans l'API (`mode: "zero_cout"`), pas encore dans l'interface.

---

## 9. Pistes d'amélioration

- Volatilité locale ou modèle de Heston pour tenir compte du skew sur les barrières.
- Grecques des structurés par différences finies avec tirages communs (même graine, choc de spot de ±1 %).
- Variables antithétiques ou variables de contrôle pour réduire l'erreur Monte Carlo à nombre de tirages égal (une seule fonction à modifier : `simuler_cours_observations`).
- Courbe de taux et dividendes discrets au lieu de `r` et `q` constants.
- Spread de crédit de l'émetteur dans l'actualisation des structurés.
- Implémenter Yang-Zhang et GARCH(1,1).
- Tests automatiques (`pytest`) : parité call-put, convergence MC → BS, cas limites des barrières, un test par message d'erreur de `validation.py`.

---

## 10. Mini-glossaire des produits

- **Call / put européen** : droit d'acheter / vendre à K, exerçable seulement à maturité.
- **Américain** : exerçable à tout moment ; vaut au moins l'européen.
- **Digitale cash-or-nothing** : paie un montant fixe si l'option finit dans la monnaie. **Asset-or-nothing** : livre le sous-jacent.
- **Forward** : engagement ferme d'acheter / vendre à K à maturité.
- **Phoenix** : coupon versé à chaque date si le sous-jacent est au-dessus d'une barrière coupon ; rappel anticipé au-dessus d'une barrière de rappel ; perte en capital à maturité sous la barrière de protection.
- **Autocall Plain** : pas de coupon intermédiaire ; les coupons s'accumulent et sont payés d'un coup au rappel (ou à maturité si le seuil final est atteint).
- **Accumulateur** : le client achète une quantité fixe à un prix décoté K à chaque constat, deux fois plus (levier) si le cours passe sous K ; le produit s'arrête (KO) si le cours dépasse une barrière haute. Surnommé « I kill you later » à cause des pertes de 2008.
- **Décumulateur** : le miroir, pour un vendeur.
- **Dual Range Accrual** : coupon proportionnel au nombre de jours où **deux** sous-jacents sont **simultanément** dans leurs fourchettes ; capital garanti.

Chaque produit est détaillé, avec son intuition économique, dans le README de son moteur.
