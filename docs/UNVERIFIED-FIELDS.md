---
type: reference
sources_of_truth:
  - "https://pennylane.readme.io/llms.txt (index officiel, relevé le 2026-09-02)"
  - "L'OpenAPI embarqué dans chacune des 163 pages de https://pennylane.readme.io/reference/*.md,
     fusionné en une spec unique — 123 chemins, 158 opérations, dont 91 GET (cf. EXTRACTION.md)"
  - "Les guides : Error Handling & Status Codes, Use Cursor-Based Pagination,
     Rate Limiting in API v2, Understand Scopes, Filter API Data,
     Track Data Changes with the API"
review_triggers:
  - "Une campagne de sondes contre une VRAIE instance Pennylane (compte de bac à sable)"
  - "Une mise à jour de la référence readme.io qui ajoute une énumération là où il n'y en avait pas"
  - "Tout écart constaté par un consommateur entre le mock et la production"
update_policy: >-
  Tout champ ou comportement marqué `x-pennylane-confidence: unverified` ou
  `invented` dans le contrat DOIT figurer dans ce fichier —
  `tests/test_contract_is_current.py` échoue sinon. Retirer une ligne d'ici
  demande de retirer le marqueur du modèle, et donc d'avoir levé le doute.
last_verified: 2026-09-02
---

# Ce qui n'est pas attesté

Ce mock est construit sur une source **machine-readable et publique** : l'OpenAPI
que Pennylane embarque dans chacune de ses pages de référence. Presque tout ce
qu'il sert en vient. Ce fichier liste ce qui n'en vient pas — et ce qu'il
faudrait faire pour lever chaque doute.

La distinction est celle des quatre mocks voisins :

| Marqueur | Sens |
|---|---|
| *attesté* | vient de l'OpenAPI officiel ou d'un guide du fournisseur. Aucun marqueur. |
| `unverified` | le nom, la forme ou les valeurs sont **plausibles**, pas prouvés. |
| `invented` | n'existe **pas** chez Pennylane — c'est une affordance du mock. |

## Champs de schéma

| Champ | Ressource | Ce qui est incertain | Pour lever le doute |
|---|---|---|---|
| `type` | `journals` | L'OpenAPI déclare `type: string` **sans énumération**. Les valeurs servies (`sale`, `purchase`, `bank`, `miscellaneous`, `new_year`, `payroll`) suivent la nomenclature française usuelle. | `GET /journals` sur une instance réelle : les six codes d'un dossier français y sont tous. |
| `type` | `ledger_accounts` | Idem, `type: string` sans énumération. Valeurs servies : `customer`, `supplier`, `bank`, `tax`, `income`, `expense`, `equity`, `suspense`. | `GET /ledger_accounts?limit=100` sur une instance réelle, puis dédoublonner le champ. |
| `job_title`, et la forme entière de l'élément | `customers/{id}/contacts` | La référence `getcustomercontacts` **ne détaille pas** le schéma de l'élément rendu. Les champs servis sont plausibles. | `GET /customers/{id}/contacts` sur une instance réelle qui a des contacts. |
| numérotation des comptes auxiliaires (`411LUMIN`, `401FIVET`) | `ledger_accounts` | Le fournisseur ne documente **aucune** règle de composition : chaque cabinet a la sienne. | Lire les `number` d'une instance réelle dont le plan porte des auxiliaires. |
| `reg_no`, `vat_number`, `establishment_no` | `customers`, `suppliers` | Les valeurs sont **dérivées de la graine**, donc syntaxiquement plausibles mais sans réalité : ce ne sont pas de vrais SIREN. Les CHAMPS, eux, sont attestés. | Sans objet — c'est une propriété du jeu de fixtures, pas du dialecte. |
| forme de la balance des comptes auxiliaires | `trial_balance` | `formatted_number` est le numéro complété à huit caractères pour un compte général. Pour un auxiliaire (`411LUMIN`), la règle de formatage du fournisseur n'est pas documentée : le mock le rend tel quel. | `GET /trial_balance?is_auxiliary=true` sur une instance réelle. |
| contenu des ressources périphériques | `quotes`, `commercial_documents`, `billing_subscriptions`, `purchase_requests`, `sepa_mandates`, `gocardless_mandates`, `pro_account/*`, `exports/*`, `customer_invoice_templates`, `pa_registrations` | **Fidélité graduée assumée** : ces ressources sont servies avec les champs de l'OpenAPI, mais leur modèle est `ElementGenerique` (`id` + horodatages garantis, le reste passe tel quel) et leur jeu de données est mince. Elles ne portent pas le flux que le consommateur exploite. | Les typer champ par champ le jour où un consommateur les lit vraiment. La FORME (enveloppe, pagination, erreurs) est déjà exacte. |

## Comportements

| Comportement | Ce qui est incertain | Pour lever le doute |
|---|---|---|
| **Forme du corps d'erreur** | Deux sources du fournisseur se **contredisent**. Le guide « Error Handling & Status Codes » (2026-02-06) montre `{"error": "<code machine>", "message": "...", "details": {...}}`. L'OpenAPI déclare, uniformément sur les 91 opérations GET, `{"error": "<message lisible>", "status": <entier>}`. **Le mock suit l'OpenAPI** : il est machine-readable, versionné avec les endpoints, et c'est lui que le fournisseur publie comme contrat. | Provoquer un 403 et un 422 sur une instance réelle et lire le corps exact. C'est le doute le plus structurant de ce fichier : un consommateur qui lit `error["message"]` ne trouvera rien avec la forme servie ici. |
| **Contenu du curseur** | La documentation donne **trois encodages incompatibles** : `eyJpZCI6MTAwfQ==` → `{"id":100}` (guide de pagination), `dXBkYXRlZF9hdDoxNjc0MTIzNDU2` → `updated_at:1674123456` (exemple de `/bank_accounts`), `MjAyNS0wMS0wOVQwODoyNDozOC44MTI0NTha` → un horodatage (exemple des changelogs). Le mock émet du base64url de JSON, la forme du guide de pagination. | Sans objet, et c'est le point : la doc énonce elle-même que le curseur est **opaque**. Un consommateur qui le décode s'adosse à un détail qui a déjà changé trois fois. Le mock **ne signe pas** le curseur — ce serait inventer une sévérité que le fournisseur n'a pas. |
| **`exports:gl` absent de la page des scopes** | La page « Understand Scopes » (2026-03-31) ne connaît que `exports:fec` et `exports:agl`. La référence de `exportGeneralLedger` exige explicitement `exports:gl`. La page de guide est donc **incomplète** ; le mock suit la référence. | Générer un jeton dans l'interface et lire la liste des cases proposées. |
| **Validation des champs de `filter`** | L'OpenAPI déclare, endpoint par endpoint, quels champs sont filtrables et avec quels opérateurs. Le mock **accepte tout champ présent dans l'élément** : recopier 40 listes blanches dont la doc reconnaît qu'elles bougent ferait échouer le mock là où le fournisseur, lui, aurait ajouté le champ. En revanche un **opérateur** inconnu rend 400 — la liste des neuf est courte et stable. | Envoyer un `filter` sur un champ non déclaré à une instance réelle et regarder si elle rend 400 ou l'ignore. |
| **Tri : champs disponibles** | Le défaut `-id` est attesté (l'OpenAPI le déclare `default`). La liste des champs triables varie par endpoint et n'est donnée qu'en prose. Le mock accepte **tout champ présent**. | Même sonde que ci-dessus, sur `sort`. |
| **Rétention du changelog** | « Changes are retained for 4 weeks » est attesté ; le mock en fait une **purge** (l'événement plus ancien n'est pas rendu du tout) et non un simple refus par `start_date`. C'est la lecture la plus stricte, et la seule qui empêche un consommateur de croire qu'une resynchronisation complète par le changelog est possible. | Interroger un changelog réel sans `start_date` sur un dossier vieux de plusieurs mois et regarder jusqu'où il remonte. |
| **Opération `delete` du changelog** | L'énumération `insert | update | delete` est attestée. Le jeu de données **n'en produit aucune** : rien n'est supprimé dans la vie de Boréal Conseil. | Sans objet pour le dialecte. Un consommateur qui doit traiter les suppressions peut en injecter une par `/__admin` — ou le mock devra en scripter une dans `evolution.py`. |
| **`GET /me` : forme de `user`** | L'OpenAPI déclare `user` **nullable** sans dire quand il l'est. Le mock rend toujours un utilisateur. | Interroger `/me` avec un jeton de compagnie ET un jeton de cabinet : la nullité vient probablement de là. |
| **En-têtes `ratelimit-*` sur un 429** | Le guide donne l'exemple `ratelimit-remaining: 0` sur un 429. Le mock le force à `0` par définition. Sur les réponses saines, la valeur servie est un simple décompte par chemin — le fournisseur, lui, compte **par jeton, toutes routes confondues**. | Marteler une instance réelle sur deux endpoints différents et regarder si le compteur est partagé. |

## Écarts assumés par rapport au fournisseur

Ils ne sont pas des doutes : ce sont des décisions, listées ici pour qu'on ne
les prenne pas pour des bugs.

| Écart | Pourquoi |
|---|---|
| **Aucune écriture (POST/PUT/DELETE)** | Le consommateur de ce mock — le pipeline d'extraction d'insights360 — lit et n'écrit pas. Les méthodes d'écriture rendent 404 au dialecte Pennylane. Les servir demanderait de reproduire la validation métier (équilibre des écritures, cohérence TVA, unicité des références), soit un second projet. |
| **Aucun webhook** | Comme les quatre mocks voisins, le patron est strictement *pull*. L'équivalent du « push » est `evolution.py` + `/__admin/clock`. Pennylane, lui, propose de vrais webhooks. |
| **Pas d'OAuth 2.0** | Seul le jeton de compagnie (Bearer statique) est servi. Le flux d'autorisation n'apporte rien à un connecteur d'extraction, qui utilise un jeton long. |
| **Montants du jeu de données** | Ils ne reproduisent PAS ceux de `boondmanager-mock` à l'euro près. Voir l'encadré de `src/pennylane_mock/dataset/realiste.py` : ce sont les raisons sociales, l'ancre, la graine et le format des références qui sont partagés, pas les euros. |
