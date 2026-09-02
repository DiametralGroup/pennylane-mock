# D'où vient chaque forme

Ce mock n'a pas été écrit de mémoire. Il est adossé à une source
**machine-readable et publique**, relevée le **2026-09-02**, et ce fichier dit
laquelle, comment la rejouer, et ce qu'elle contient.

## La source : l'OpenAPI embarqué dans la référence

Pennylane publie un index pour agents à
[`https://pennylane.readme.io/llms.txt`](https://pennylane.readme.io/llms.txt),
et **toute page de documentation est disponible en Markdown** en lui ajoutant
`.md`. Le point décisif : chaque page de la section « API Reference » embarque,
sous un titre `# OpenAPI definition`, le **fragment OpenAPI complet de son
endpoint** — paramètres, schémas de réponse, énumérations, exemples.

Il n'y a donc pas eu à deviner : il a suffi de télécharger les 163 pages de
référence et de fusionner leurs fragments.

```bash
# 1. L'index
curl -sS https://pennylane.readme.io/llms.txt -o llms.txt

# 2. Les 163 pages de référence, en markdown
grep -o 'https://pennylane.readme.io/reference/[a-z0-9-]*\.md' llms.txt \
  | sort -u \
  | xargs -P 8 -I{} sh -c 'curl -sS "{}" -o "ref/$(basename {})"'

# 3. Les guides qui portent le dialecte transverse
for p in error-handling-status-codes using-cursor-based-pagination \
         rate-limiting-1 v2-scopes setting-up-filters \
         tracking-data-changes-with-pennylane-api api-v2-vs-v1; do
  curl -sS "https://pennylane.readme.io/docs/$p.md" -o "docs/$p.md"
done
```

La fusion des fragments (extraction du bloc ```json qui suit
`# OpenAPI definition`, puis union des `paths` et des `components`) donne :

| | |
|---|---|
| chemins | **123** |
| opérations | **158** |
| dont **GET** | **91** ← la surface servie par ce mock |
| pages sans fragment | 5, toutes des `PUT .../categories` (écriture, hors périmètre) |

Le mock sert **les 91**. `tests/test_endpoints.py` en fait une assertion : si la
fabrique de routes en perd une, le test le dit avant qu'un consommateur ne le
découvre.

## Ce que le relevé a tranché

| Question | Réponse relevée | Où |
|---|---|---|
| URL de base | `https://app.pennylane.com/api/external/v2` | `servers` de chaque fragment |
| Authentification | `Authorization: Bearer <TOKEN>`. Pas de rafraîchissement, pas d'expiration côté jeton de compagnie. | Guide « Create a Company API Token » |
| Autorisation | Scopes `resource:readonly` / `resource:all`, granulaires par domaine. 403 en cas de manque, **avec le nom du scope dans le message**. | Guide « Understand Scopes » + `security` de chaque opération |
| Enveloppe de liste | `{"items": [...], "has_more": bool, "next_cursor": str|null}`, `additionalProperties: false` | Guide de pagination + schéma des réponses 200 |
| Pagination | Curseur opaque. `limit` : défaut **20**, plafond **100** sur les listes, **1000** sur les changelogs. Hors bornes → **400**, pas de rabotage. | `parameters` de chaque opération |
| Piège du curseur | Il **n'encode pas les filtres** : « Omitting the filters on page 2+ will return unfiltered results from the cursor position. » | Guide de pagination |
| Tri | `sort=champ` / `sort=-champ`. Défaut déclaré : **`-id`** (décroissant). | `parameters`, champ `default` |
| Filtrage | `filter` = tableau JSON de `{field, operator, value}`, cumulés en ET. Neuf opérateurs : `eq`, `not_eq`, `lt`, `lteq`, `gt`, `gteq`, `in`, `not_in`, `start_with`. | Guide « Filter API Data » |
| Montants | **Des chaînes** (`"230.32"`), partout, y compris `quantity`, `weight`, `vat_rate`, `debit`/`credit`. | `type: string` sur tous les champs monétaires |
| Collections imbriquées | Des **liens** `{"url": "…"}`, jamais des tableaux — c'est la différence v1 → v2 la plus structurante. | Guide « Migrate from API v1 to v2 » + schémas |
| Corps d'erreur | `{"error": "<message>", "status": <entier>}`, uniformément sur les 91 GET. ⚠️ **Le guide d'erreurs en décrit un autre** — cf. `UNVERIFIED-FIELDS.md`. | `responses` 400/401/403/404/422 |
| Limite de débit | **25 requêtes / 5 s au jeton**. 429 à corps **texte brut**, en-tête `retry-after`. Les en-têtes `ratelimit-limit`/`-remaining`/`-reset` sont servis sur **toutes** les réponses. | Guide « Rate Limiting in API v2 » |
| Incrémentalité | Dix endpoints `/changelogs/*`. Ordre **chronologique croissant**, rétention **4 semaines** (au-delà : 422), `start_date` et `cursor` **exclusifs** (sinon 400). | Guide « Track Data Changes » + références |
| Balance | `period_start` et `period_end` **obligatoires**. `is_auxiliary` agrège ou détaille les comptes de tiers. | `parameters` de `getTrialBalance` |

## Ce que le mock ne sert pas, et pourquoi

- **Les écritures (POST/PUT/DELETE).** Le consommateur lit. Les servir
  demanderait de reproduire la validation métier de Pennylane — équilibre des
  écritures, cohérence de la TVA, unicité des références — soit un second
  projet. Une méthode d'écriture rend **404 au dialecte Pennylane**, jamais le
  405 de FastAPI.
- **Les webhooks.** Comme les quatre mocks voisins, le patron est strictement
  *pull*. L'équivalent du « push » est `evolution.py` + `/__admin/clock`.
- **OAuth 2.0.** Un connecteur d'extraction utilise un jeton de compagnie longue
  durée ; le flux d'autorisation n'apporte rien à ce qu'il faut éprouver.

## Rejouer le relevé

`scripts/compare_real.py` confronte le contrat committé à une **vraie**
instance, en lecture seule, sur les endpoints de liste. Il demande un jeton et
n'écrit rien :

```bash
PENNYLANE_TOKEN=xxx uv run python scripts/compare_real.py --out docs/comparisons/
```

Il compare, ressource par ressource : les clés de l'enveloppe, les clés des
éléments, le type de chaque valeur (chaîne vs nombre — c'est là que se joue le
piège des montants), et la forme des erreurs 401/403/404. Tout écart est un
écart du MOCK, jamais du fournisseur : c'est lui qui a raison.
