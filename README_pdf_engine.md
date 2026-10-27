# `pdf_engine.py` — export PDF du dernier calcul

```python
"""Export du calcul mémorisé : aucun moteur de pricing n'est rappelé."""
```

Toute la philosophie du fichier est dans cette première ligne : le PDF **relit** l'instantané mémorisé par `main.py` (paramètres + résultats) et le met en page. Il ne recalcule rien. Ainsi le PDF correspond **exactement** à ce qui était affiché, même si le formulaire a été modifié depuis ou si le calcul était un Monte Carlo.

## Pourquoi `reportlab` ?

- Bibliothèque PDF Python la plus répandue, sans dépendance système (contrairement à WeasyPrint qui demande des librairies graphiques).
- Module **Platypus** : on empile des blocs (titre, paragraphe, tableau, dessin) et `reportlab` gère la pagination tout seul.
- Les imports de `reportlab` sont **dans la fonction** : si la bibliothèque manque, le reste du pricer fonctionne ; seul l'export lève une `ImportError`, que `main.py` transforme en erreur 503 explicite (« Installez les dépendances »).

## Ligne par ligne

```python
styles = getSampleStyleSheet(); out = BytesIO(); story = []
```
- Styles par défaut (`Title`, `Heading2`, `BodyText`).
- `BytesIO` : le PDF est écrit **en mémoire**, jamais sur le disque, puis envoyé au navigateur.
- `story` : la liste ordonnée des blocs du document (vocabulaire Platypus).

```python
def texte(t, style='BodyText'): return Paragraph(escape(str(t)), styles[style])
```
**`escape` est indispensable.** Les `Paragraph` de reportlab interprètent un mini-HTML : un caractère `<` ou `&` dans une valeur (par exemple `"P&L"`) casserait la génération. `html.escape` transforme `&` en `&amp;`, etc.

```python
def nombre(x): return f'{x:,.6g}' if isinstance(x,(int,float)) and not isinstance(x,bool) else str(x)
```
Format compact à 6 chiffres significatifs avec séparateur de milliers. Encore une fois, les booléens sont exclus (un `True` s'afficherait « 1 »).

```python
def table(rows):
    t = Table([[texte(c) for c in row] for row in rows], colWidths=[230,280], repeatRows=1, hAlign='LEFT')
```
- Chaque cellule est un `Paragraph` : le texte long **passe à la ligne** dans la cellule au lieu de déborder.
- `repeatRows=1` : l'en-tête du tableau est répété en haut de chaque page.
- Style : fond gris clair pour l'en-tête, fines lignes de séparation.

### Contenu du document

1. **Titre** + route appelée + date du calcul (UTC).
2. **Paramètres** tels que reçus (avant conversion : donc en %, comme saisis).
3. **Résultats scalaires** : on ne garde que les valeurs simples (`not isinstance(v, (dict, list))`). Les listes (grilles, séries) seraient illisibles en tableau.
4. **Intervalles de confiance** (`ic95`, `ic95_pct`) sur une ligne.
5. **Flux moyens par date** si disponibles :
   ```python
   dates = r.get('obs_dates', [])
   flows = r.get('flux_attendus_par_date', [])
   pv = r.get('flux_attendus_actualises_par_date', [])
   ```
   Tous les produits structurés (Phoenix, Autocall, accumulateur, DRA) renvoient désormais ces trois champs **sous les mêmes noms**. Avant, le DRA utilisait `dates_paiement` et `coupons_moyens_par_date`, ce qui obligeait l'export à gérer un cas particulier.
6. **Grecques** si le résultat en contient (`primaires`).
7. **Graphe du payoff** si `ST_grid` et `payoff` existent : dessiné en vectoriel avec `Drawing` et `PolyLine`.
   ```python
   points += [25 + 460*(x-min(xs))/max(max(xs)-min(xs),1e-9),
              25 + 125*(y-lo)/span]
   ```
   Changement d'échelle : on ramène les valeurs dans un cadre de 460 × 125 points. Le `max(..., 1e-9)` évite une division par zéro si toutes les valeurs sont égales (payoff plat).
8. **Avertissement final** : rappel des conventions (taux et vols saisis en %), que les probabilités viennent du modèle et ne sont pas des prévisions, que la marge = 100 % − valeur théorique, hors défaut, frais et funding.

```python
def footer(canvas, doc): canvas.drawRightString(550, 25, str(doc.page))
```
Numéro de page en bas à droite, appelé par reportlab sur chaque page.

## Limites

- Mise en page simple (pas de logo, pas de graphe des flux).
- Le graphe n'a pas d'axes gradués détaillés : seulement les bornes min / max en légende.
- Dépend de l'instantané en mémoire : après 30 minutes ou un redémarrage, il faut relancer le calcul.
