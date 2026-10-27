# `templates/index.html` — l'interface

Toute l'interface tient dans **un seul fichier** : HTML (structure), CSS (apparence) et JavaScript (comportement). Aucun framework (pas de React, pas de Vue), aucune librairie de graphiques.

**Pourquoi ce choix ?**
- Rien à installer côté interface : Flask sert le fichier, le navigateur l'exécute.
- Un étudiant peut tout lire de haut en bas.
- Les graphiques sont dessinés **à la main en SVG** : on comprend exactement comment une courbe de payoff est construite (changement d'échelle, axes, zones colorées).

Contrepartie : le fichier est long (≈ 1 400 lignes). Au-delà, il faudrait le découper (fichiers `.js` séparés dans `static/`).

> **Doublons supprimés.** Le JavaScript avait grandi onglet par onglet : deux recherches de ticker, deux façons de charger les données de marché, une fonction d'appel au serveur plus des `fetch` écrits à la main, quatre formateurs de pourcentage identiques, trois mécanismes différents pour ignorer une réponse arrivée trop tard, et le même bloc « bouton bloqué / message d'attente / erreur en rouge » recopié dans chaque formulaire. À chaque fois, la version la plus robuste a été gardée et partagée (section 4). Le comportement de la page est identique, vérifié parcours par parcours.

---

## 1. `<head>` et polices

- `lang="fr"` : lecteurs d'écran et correcteurs en français.
- `viewport` : affichage correct sur mobile.
- Polices **IBM Plex Mono** (chiffres, saisies) et **IBM Plex Sans Condensed** (texte). Une police à **chasse fixe** aligne les chiffres en colonnes, comme sur un terminal de marché.

## 2. CSS : un thème « terminal de marché »

```css
:root{
  --bg:#000; --panel:#07090d; --bar:#102a52;
  --amber:#ffa028; --green:#3ddc84; --red:#ff5c5c; ...
}
```
- **Variables CSS** : toutes les couleurs sont définies une fois. Changer `--amber` change tout le site.
- Inspiration terminal financier : fond noir, saisies ambre, bandeaux bleus, codes de fonction (`OPT <GO>`).
- `font-variant-numeric: tabular-nums` : tous les chiffres ont la même largeur, les colonnes ne « dansent » pas quand les valeurs changent.
- `input:invalid:not(:focus):not(:placeholder-shown)` : bordure rouge sur un champ invalide, mais seulement quand l'utilisateur a fini de taper.
- `.grid` : deux colonnes (formulaire 340 px + résultats). Le formulaire est `position: sticky` : il reste visible quand on fait défiler les résultats.
- `@media (max-width:900px)` : une seule colonne sur petit écran.
- `@media (prefers-reduced-motion:reduce)` : le curseur clignotant s'arrête pour les personnes qui désactivent les animations dans leur système (accessibilité).

## 3. Structure HTML

- **Barre du haut** : marque, « ligne de commande » qui affiche le code de la fonction ouverte (`OPT`, `FWD`, `STRC PHNX`…), horloge de Paris.
- **Onglets** (`.tab` avec `data-tab="options"`, etc.) et **panneaux** (`<section id="panel-options">`). Le lien se fait par convention de nom : `panel-` + nom de l'onglet.
- Chaque panneau = un formulaire à gauche + une zone `.results` à droite.
- Produits structurés : **un formulaire par produit**, marqué `data-product="phoenix"`, etc. Un menu déroulant affiche le bon. Ajouter un produit = ajouter une `<option>` et un `<form data-product="...">`.

Attributs utiles pour la validation côté navigateur : `type="number"`, `min`, `max`, `step`, `required`. Ils servent au **confort** (le navigateur signale l'erreur avant l'envoi) mais **ne suffisent pas** : ils se contournent avec les outils développeur. La règle qui fait foi est celle de `validation.py`, côté serveur.

`role="status"` et `aria-live="polite"` sur les messages : un lecteur d'écran annonce « Calcul en cours… » puis « Résultats à jour ».

---

## 4. JavaScript — les briques communes

### Formats de nombres

```js
const FORMAT_2 = new Intl.NumberFormat('fr-FR', {minimumFractionDigits: 2, maximumFractionDigits: 2});
const fmt = x => FORMAT_4.format(x);                                 // valeurs : 2 à 4 décimales
const fmt2 = x => x == null ? '—' : FORMAT_2.format(x);              // indicateurs : 2 décimales
const fmtPct = x => x == null ? '—' : FORMAT_2.format(x) + ' %';     // pourcentages
```
- `Intl.NumberFormat('fr-FR')` : virgule décimale et espace des milliers, à la française (1 234,56).
- Les formateurs sont créés **une fois** (créer un `Intl.NumberFormat` à chaque appel est coûteux).
- `fmtPct` remplace quatre fonctions identiques qui existaient avant (`fmtPct`, `fmtVol`, `volPercent`, `acPercent`). Une valeur absente (`null`) s'affiche `—`.

### `appelApi` — un seul point d'appel au serveur

```js
async function appelApi(url, {payload, timeout = 300000, controleur = new AbortController()} = {}) {
  let delaiDepasse = false;
  const minuteur = setTimeout(() => { delaiDepasse = true; controleur.abort(); }, timeout);
  try {
    ...
    const res = await fetch(url, options);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Erreur inconnue.');
    return data;
  } catch (err) {
    if (delaiDepasse) throw new Error('Délai dépassé : réduisez le calcul (simulations, période) et réessayez.');
    throw err;
  } finally {
    clearTimeout(minuteur);
  }
}
```
- **GET ou POST** : avec un `payload`, la requête part en POST avec du JSON ; sans, c'est un GET (recherche, données de marché, volatilité, configuration).
- **Message d'erreur du serveur** : toutes les routes renvoient `{error: ...}` en cas d'échec (voir `_err` dans `main.py`) ; on le transforme en exception affichable.
- **Délai maximal** : 5 minutes par défaut, moins pour les appels rapides (15 s pour la recherche). `AbortController` coupe la requête et `delaiDepasse` distingue un dépassement de délai d'une annulation volontaire.
- **`controleur`** fourni par l'appelant : la recherche de ticker annule la requête précédente quand l'utilisateur continue de taper.
- `async / await` : le navigateur ne se fige pas pendant le calcul.

Avant, `callApi` n'avait pas de délai, et l'Autocall, l'onglet Chantier et la recherche faisaient chacun leur propre `fetch`. Seul l'export PDF garde un `fetch` direct, car sa réponse est un fichier binaire et non du JSON.

### `surSoumission` — le comportement commun à tous les formulaires

```js
function surSoumission(form, status, calcul, {avant, attente = 'Calcul en cours...'} = {}) {
  const bouton = form.querySelector('button[type=submit]');
  form.addEventListener('submit', async e => {
    e.preventDefault();
    if (avant) avant();
    if (bouton.disabled || !form.reportValidity()) return;
    bouton.disabled = true; status.className = 'status'; status.textContent = attente;
    try { status.textContent = await calcul() || ''; }
    catch (err) { status.className = 'status error'; status.textContent = err.message; }
    finally { bouton.disabled = false; }
  });
}
```
- `e.preventDefault()` : empêche le rechargement de la page (comportement par défaut d'un formulaire).
- `avant` : un contrôle à faire juste avant l'envoi (ordre des barrières du Phoenix, dates de l'Autocall).
- `form.reportValidity()` : déclenche la validation **native** du navigateur et affiche ses bulles d'erreur.
- Bouton désactivé pendant le calcul : impossible de lancer deux calculs en double-cliquant.
- `calcul()` renvoie le message de succès, ou rien si le résultat est devenu inutile.
- `finally` : le bouton est réactivé même en cas d'erreur.

Ce bloc était recopié dans chacun des huit formulaires.

### `renderChart` — moteur de graphique SVG

C'est la fonction la plus instructive du fichier.

```js
const W=760, H=340, L=60, R=16, top=34, bottom=44;
const X = x => L + (x-xmin)/(xmax-xmin) * (W-L-R);
const Y = y => top + (ymax-y)/(ymax-ymin) * (H-top-bottom);
```
- **Changement d'échelle** : `X` transforme un prix en position horizontale en pixels ; `Y` une valeur en position verticale. `ymax − y` car en SVG l'axe vertical va **vers le bas**.
- `pad` : 12 % de marge en haut et en bas pour que la courbe ne touche pas le bord. `Math.min(0, ...)` / `Math.max(0, ...)` : l'axe zéro est **toujours** visible (important pour lire un P&L).

**Zones de gain et de perte :**
```js
<clipPath id="haut${id}"> rectangle au-dessus de zéro </clipPath>
<clipPath id="bas${id}">  rectangle en dessous de zéro </clipPath>
```
On dessine **deux fois** la même surface sous la courbe de P&L : une fois en vert découpée par le rectangle du haut, une fois en rouge découpée par celui du bas. Résultat : vert là où le P&L est positif, rouge là où il est négatif, sans calculer les intersections.

`chartId` : chaque graphe reçoit un identifiant unique, sinon deux graphes sur la même page partageraient les mêmes `clipPath` (les identifiants SVG sont globaux à la page).

Autres éléments : grille, graduations, **marqueurs verticaux** (barrières des structurés), **breakevens** (pointillés jaunes + cercle), **légende** dont la position avance selon la longueur du libellé.

### `greeksTable`

Construit un tableau HTML à partir d'un dictionnaire, dans un **ordre imposé** (Delta, Gamma, Vega, Theta, Rho) plutôt que l'ordre des clés.

### `brancherRechercheTicker` — recherche de ticker, commune aux cinq formulaires

```js
input.addEventListener('input', () => {
  fermer();                                   // annule la requête et la temporisation précédentes
  const maVersion = version, query = input.value.trim();
  minuteur = setTimeout(async () => {
    controleur = new AbortController();
    const data = await appelApi('/api/search_ticker?q=' + encodeURIComponent(query), {controleur, timeout: 15000});
    if (maVersion !== version) return;        // une frappe plus récente a eu lieu
    ...
  }, 350);
});
```
Trois protections, qui n'existaient auparavant que dans l'onglet Chantier :
- **Temporisation (debounce)** : on attend 350 ms après la dernière frappe. Taper « AAPL » fait **une** requête au lieu de quatre.
- **Annulation** : la requête précédente est coupée (`controleur.abort()`).
- **Compteur de version** : une réponse lente pour « AA » ne peut pas écraser celle de « AAPL ».

Les suggestions sont construites avec `createElement` et `textContent` : un nom venant de Yahoo qui contiendrait du HTML est affiché tel quel, jamais interprété. L'ancienne version de l'onglet Options injectait ces noms avec `innerHTML`.

`encodeURIComponent` : un ticker comme `MC.PA` ou `^VIX` est correctement transmis dans l'URL. Échap ou un clic en dehors ferment la liste. L'option `saisieLibre` (onglet Chantier) précise qu'on peut aussi taper le symbole directement. La fonction renvoie `fermer`, pour que le Chantier puisse annuler une recherche en cours au moment du calcul.

### `chargerMarche` — spot et volatilités d'un ticker

```js
const version = status.dataset.version = String(Number(status.dataset.version || 0) + 1);
if (bouton) bouton.disabled = true;
const data = await appelApi('/api/market/' + encodeURIComponent(symbol), {timeout: 30000});
if (version !== status.dataset.version) return;
if (!(Number.isFinite(data.S0) && data.S0 > 0)) throw new Error('Spot indisponible.');
appliquer(data);
```
- Compteur de version rangé dans le champ de statut lui-même (`dataset`) : seule la dernière sélection s'affiche.
- Bouton « Calculer » désactivé pendant le chargement : impossible de lancer un calcul avec un spot à moitié chargé.
- Vérification du spot avant de l'utiliser.
- `appliquer` : ce que chaque onglet fait des données (remplir le spot, recentrer les strikes, mettre à jour les textes d'aide…).

C'était la version de l'Autocall ; les autres onglets utilisaient une version plus simple, sans compteur ni blocage du bouton.

### Volatilité : manuelle, historique ou VIX

```js
const volMarche = {};
function setVolMarche(p, data) { volMarche[p] = {hist: data.vol_hist, vix: data.vix}; ... }
function lireSigma(p) { ... }
```
- Les vols de marché sont gardées **par onglet** (préfixe `opt`, `strat`, `phx`, `ac-plain`) : chercher un ticker dans l'onglet Options ne change pas la vol du Phoenix.
- Après une recherche, la source passe automatiquement sur « historique », ou sur « VIX » si l'historique est indisponible.
- `lireSigma` lève une erreur claire si l'utilisateur choisit « historique » sans avoir cherché de ticker ; comme elle est appelée dans `calcul()`, l'erreur s'affiche en rouge via `surSoumission`.

### Bornes de strike lues sur le serveur

```js
appelApi('/api/config', {timeout: 10000}).then(config => {
  moneyness.min = config.moneyness_min;
  moneyness.max = config.moneyness_max;
  updateOptStrikes(); updateStratStrikes();
});
```
La règle « strike entre 50 % et 150 % du spot » était écrite dans `main.py` **et** ici, avec un commentaire « penser à changer les deux ». Elle n'existe plus que dans `validation.py` ; la page la lit au chargement.

```js
const lo = Math.ceil(S0 * moneyness.min * 100) / 100;
const hi = Math.floor(S0 * moneyness.max * 100) / 100;
```
`ceil` pour la borne basse et `floor` pour la borne haute : après arrondi au centime, les bornes affichées restent **à l'intérieur** de l'intervalle accepté par le serveur (sinon un strike à la borne affichée pourrait être refusé).

---

## 5. Onglet Options

```js
const METHODES = {
  european: [['black_scholes','Black-Scholes'], ['monte_carlo','Monte Carlo']],
  american: [['longstaff_schwartz','Longstaff-Schwartz']],
};
```
La liste des méthodes dépend du style d'exercice : on **empêche** de choisir une combinaison impossible plutôt que de la refuser après coup.

Le champ « nombre de simulations » n'apparaît que si un calcul simule : Monte Carlo, Longstaff-Schwartz, ou une digitale (pour laquelle Black-Scholes et Monte Carlo sont calculés tous les deux). Un petit bug a été corrigé au passage : le champ restait affiché quand on revenait d'une digitale à une option classique en Black-Scholes.

Choix d'une **digitale** : style forcé en européen, et l'appel part vers `/api/digital` au lieu de `/api/option`. En Monte Carlo (vanille ou digitale), le résultat affiche aussi l'IC95.

Quand on choisit un ticker, le strike est **recentré** sur le nouveau spot (à la monnaie), pour ne pas garder un strike à 100 sur une action à 3 000.

## 6. Onglet Forward

Formulaire simple, branché sur `surSoumission`.

## 7. Onglet Stratégies

```js
const STRAT_FIELDS = {
  covered_call: {k:true, k1:false, k2:false, side:false},
  strangle:     {k:false, ..., side:true, kput:true, kcall:true},
  ...
};
```
Table de configuration : quels champs afficher pour quelle stratégie. Les champs K1 / K2 sont **réutilisés** pour strike put / strike call (strangle, collar), seul leur libellé change. Les champs masqués sont aussi `disabled` pour ne pas bloquer la validation du formulaire.

`recentrerStrategie` place les strikes à ±5 % du spot, dans le bon ordre selon la stratégie (pour un put spread, K1 est le strike haut).

## 8. Onglet Produits structurés

### Sélecteur de produit et `versionStructure`

```js
let versionStructure = 0;
$('struct-product').addEventListener('change', () => {
  ++versionStructure;
  ...
});
```
**Problème rencontré :** un calcul est lancé, puis l'utilisateur change de produit ; la réponse arrive et s'affiche sur un écran qui ne lui correspond plus. Chaque calcul de structuré mémorise `versionStructure` au départ et ignore sa réponse si elle a changé entre-temps. Le compteur augmente quand on change de produit ou quand on active / désactive le rappel de l'Autocall.

Avant, trois mécanismes différents existaient (un compteur pour l'Autocall, une comparaison du nom du produit pour le Phoenix, une autre pour le DRA et l'accumulateur), et deux écouteurs distincts étaient branchés sur le même menu.

### `verifierMaturite` — maturité commune au Phoenix et à l'Autocall

```js
const pasMois = 12 / Number($(freqId).value);
champ.step = pasMois; champ.min = pasMois;
const n = Number(champ.value) / pasMois;
champ.setCustomValidity(Number.isInteger(n) && n > 0 ? '' : message);
```
La maturité en mois doit être un nombre entier de périodes (même règle que `validation.dates_observation`). `step` et `min` guident la saisie ; `setCustomValidity` bloque l'envoi avec une bulle d'erreur native. Le Phoenix affiche en plus « 6 date(s) d'observation, tous les 6 mois ». Les deux produits avaient chacun leur version de cette logique.

### `renderStructuredMetrics`

Les 8 indicateurs communs au Phoenix et au Plain (prix, valeur, marge colorée en vert / rouge, probabilité de perte, durée de vie, etc.), en miroir de `_reponse_structure` côté serveur.

### Phoenix

- **Les dates ne sont jamais saisies** : on choisit maturité (mois) et fréquence.
- **Le spot n'est pas saisi** : un Phoenix se lit en % du niveau initial. Sans ticker, on part de 100 ; avec un ticker, du vrai spot, et les textes d'aide affichent la barrière en devise (« 70 % du départ (124,50 EUR) »).
- **Textes d'aide dynamiques** : chaque barrière explique en français ce qui se passe (« À chaque date, s'il est au-dessus de… tu touches le coupon »). Pédagogie intégrée au formulaire.
- **Ordre des barrières** :
  ```js
  $('phx-bpdi').setCustomValidity(bpdi > bcoupon ? 'La protection doit être ≤ ...' : '');
  ```
  Le formulaire refuse de partir et affiche la bulle d'erreur standard.
- **Step-up** : le navigateur construit la liste des coupons `[c, c+h, c+2h, ...]` avant l'envoi.

### Autocall Plain

```js
const ac = id => $('ac-plain-' + id);
```
Raccourci pour les identifiants du formulaire. Il remplace une boucle `for (const kind of ['plain'])` sur une liste à un seul élément, reste de l'époque où il y avait deux produits (« plain » et « sans rappel »), aujourd'hui fusionnés.

- Le libellé et la règle affichés changent selon que le rappel est activé ou non.
- Le résultat est construit en grande partie avec `createElement` / `textContent` (le texte n'est jamais interprété comme du HTML), puis inséré d'un coup avec `replaceChildren`.
- La note de résultat lit la marge sous son nom unique, `marge_emetteur_pct` (le serveur la renvoyait auparavant aussi sous `ecart_souscription_pct`).

### Accumulateur, décumulateur, DRA

```js
const payload = Object.fromEntries(new FormData(form));
```
Formulaires écrits de façon **générique** : chaque `<input>` porte un attribut `name` identique au nom du paramètre attendu par le serveur. `FormData` lit tout le formulaire d'un coup. Ajouter un paramètre = ajouter un `<input name="...">`, sans toucher au JavaScript.

La case `ko_inclut_date` est lue à part (`checked`) : `FormData` n'inclut une case que si elle est cochée, et avec la valeur `"on"`.

Les flux par date du DRA arrivent tout prêts du serveur : le navigateur ne les recalcule plus.

## 9. Onglet Chantier (volatilité)

- Recherche de ticker via `brancherRechercheTicker`, avec l'option `saisieLibre`.
- **`validateVolatility`** : avant d'afficher, on vérifie que toutes les séries ont la même longueur que les dates, que les valeurs sont des nombres finis positifs et que les dates sont strictement croissantes. On ne fait pas confiance aveuglément à la réponse, même de notre propre serveur.
- **Graphique interactif** (`renderVolatilityChart`, volontairement distinct de `renderChart` : axe en dates, curseur, clavier) :
  - curseur qui suit la souris (`pointermove`) : conversion des coordonnées écran → SVG avec `getScreenCTM().inverse()`, puis recherche du point le plus proche ;
  - navigation **au clavier** (flèches, Début, Fin) : `tabindex="0"` rend le graphique focusable ;
  - la note sous le graphe (`aria-live`) annonce les valeurs du point sélectionné aux lecteurs d'écran.
- Dates affichées en UTC (`'T00:00:00Z'`) pour éviter qu'un fuseau horaire décale une date d'un jour.

## 10. `appendExtras` et l'export PDF

```js
function appendExtras(target, data) {
  if (data.flux_attendus_par_date) { ...tableau des flux par date... }
  if (!data.calculation_id) return;
  ...bouton « Exporter ce calcul en PDF »...
}
```
- Tableau commun à tous les structurés : flux moyen et flux actualisé par date, seuil de rappel (avec step-down). La note rappelle que la somme des flux actualisés retrouve la valeur théorique.
- Le bouton PDF envoie **seulement le `calculation_id`** : le serveur relit son instantané. Les modifications du formulaire après le calcul n'affectent donc pas le PDF.
- Téléchargement : la réponse est transformée en `Blob`, on crée une URL temporaire (`URL.createObjectURL`), un lien invisible qu'on « clique », puis on libère l'URL une seconde plus tard (sinon fuite de mémoire).

---

## 11. Ce qu'il faudrait encore améliorer

- Découper le JavaScript en fichiers (`static/options.js`, `static/structures.js`…).
- Construire aussi les indicateurs et les tableaux avec `textContent` plutôt qu'`innerHTML` (aujourd'hui sans risque, car ce sont des nombres calculés par le serveur, mais c'est la bonne habitude).
- Ajouter le mode « strike zéro coût » de l'accumulateur à l'interface (il existe déjà dans l'API).
