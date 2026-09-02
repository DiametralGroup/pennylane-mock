"""Configuration — tout par variables d'environnement, aucun fichier.

Même mécanique que les quatre autres mocks de l'écosystème : un objet relu à
chaud par `reload()`, pour qu'un test puisse changer une valeur sans recharger
le module. C'est aussi le seul mécanisme qui marche identiquement en docker
compose, en Deployment Kubernetes et en service GitHub Actions.

Le préfixe est `PENNYLANE_MOCK_*`. Il n'y a délibérément AUCUN `.env.example`
ici : le fichier d'exemple vit chez le consommateur (insights360), parce que
c'est lui qui doit documenter comment brancher les cinq sources ensemble.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

#: Le jeton par défaut. Statique — Pennylane délivre des jetons de compagnie
#: longue durée, pas des jetons de session (cf. docs/EXTRACTION.md §auth).
JETON_DEFAUT = "mock-pennylane-token"

#: Les scopes en lecture de TOUTE la surface v2, relevés sur
#: https://pennylane.readme.io/docs/v2-scopes (état 2026-03-31). C'est le
#: périmètre par défaut du mock : un consommateur en lecture les a tous.
#:
#: `ledger` n'a PAS de variante `:readonly` chez le fournisseur — c'est un
#: scope read/write unique, et c'est ce qui rend l'accès aux pièces jointes
#: d'écriture comptable plus large que le reste. On le reproduit tel quel.
SCOPES_LECTURE: tuple[str, ...] = (
    # SALES
    "customers:readonly",
    "products:readonly",
    "customer_invoices:readonly",
    "quotes:readonly",
    "customer_mandates:readonly",
    "billing_subscriptions:readonly",
    "commercial_documents:readonly",
    "customer_invoice_templates:readonly",
    # PURCHASES
    "suppliers:readonly",
    "supplier_invoices:readonly",
    "purchase_requests:readonly",
    # ACCOUNTING
    "ledger",
    "trial_balance:readonly",
    "exports:fec",
    "exports:agl",
    # ⚠️ `exports:gl` n'est PAS listé par la page « Understand Scopes » (état
    # 2026-03-31), qui ne connaît que `exports:fec` et `exports:agl`. La
    # référence de `exportGeneralLedger`, elle, l'exige explicitement. La page
    # de guide est donc incomplète — cf. docs/UNVERIFIED-FIELDS.md.
    "exports:gl",
    "fiscal_years:readonly",
    "journals:readonly",
    "ledger_accounts:readonly",
    "ledger_entries:readonly",
    # ANALYTICS
    "categories:readonly",
    # BANKING
    "transactions:readonly",
    "bank_accounts:readonly",
    "bank_establishments:readonly",
    # CORE
    "file_attachments:readonly",
)


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    """État de configuration, relu à chaud par `reload()`."""

    token: str = ""

    #: Scopes portés par le jeton. Le levier « 403 » du mock : retirer un scope
    #: reproduit un jeton à périmètre restreint, exactement comme chez le
    #: fournisseur — et c'est la panne la plus fréquente en intégration réelle.
    scopes: frozenset[str] = frozenset()

    seed: int = 42

    # Plan de contrôle /__admin. Fermé par défaut : il n'a de sens qu'en test.
    admin_enabled: bool = False
    admin_token: str = "mock-admin-token"

    # ── Pagination ───────────────────────────────────────────────────────────
    # Défaut 20 partout. Le PLAFOND, lui, n'est pas le même selon l'endpoint —
    # 100 sur les listes ordinaires, 1000 sur les changelogs — et l'OpenAPI le
    # déclare bien ainsi, endpoint par endpoint. Deux réglages, donc.
    #
    # `limit` hors bornes rend 400 : il n'est PAS raboté en silence. Un plafond
    # silencieux fait croire à un pipeline qu'il a demandé 5000 lignes et tout
    # reçu, alors qu'il en a lu 100 — c'est le défaut le plus coûteux d'une
    # pagination, parce qu'il ne se voit nulle part.
    limite_defaut: int = 20
    limite_max: int = 100
    limite_max_changelog: int = 1000

    #: Limite de débit — 25 requêtes / 5 s au jeton, en vigueur sur TOUS les
    #: endpoints (production comme bac à sable). Les en-têtes `ratelimit-*`
    #: partent sur chaque réponse, pas seulement sur les 429.
    rate_limit: int = 25
    rate_window: float = 5.0
    rate_limit_enforced: bool = False

    #: L'identité rendue par /me.
    company_name: str = "Boréal Conseil"
    company_id: int = 918_244
    company_reg_no: str = "824419236"

    # ── Évolution temporelle (extraction incrémentale) ───────────────────────
    # Le jeu de données VIT : un événement scripté toutes les
    # `evolution_interval` secondes (facture émise, transaction rapprochée,
    # écriture passée…), chacun inscrit au changelog. Mettre à false — ou
    # l'intervalle à 0 — fige le jeu de données pour les usages qui exigent un
    # contenu stable à l'octet près (le gate d'idempotence d'insights360).
    evolution_enabled: bool = True
    evolution_interval: float = 60.0

    #: Rétention du changelog, en jours. Le fournisseur retient 4 semaines et
    #: REFUSE une `start_date` plus ancienne — c'est une contrainte que le
    #: consommateur doit rencontrer en test, pas en production.
    changelog_retention_days: int = 28

    extra: dict[str, str] = field(default_factory=dict)

    def reload(self) -> None:
        self.token = os.environ.get("PENNYLANE_MOCK_TOKEN", JETON_DEFAUT)
        brut = os.environ.get("PENNYLANE_MOCK_SCOPES")
        self.scopes = (
            frozenset(SCOPES_LECTURE)
            if brut is None
            else frozenset(s.strip() for s in brut.split(",") if s.strip())
        )
        self.seed = int(os.environ.get("PENNYLANE_MOCK_SEED", "42"))
        self.admin_enabled = _flag("PENNYLANE_MOCK_ADMIN_ENABLED", False)
        self.admin_token = os.environ.get("PENNYLANE_MOCK_ADMIN_TOKEN", "mock-admin-token")
        self.limite_defaut = int(os.environ.get("PENNYLANE_MOCK_DEFAULT_LIMIT", "20"))
        self.limite_max = int(os.environ.get("PENNYLANE_MOCK_MAX_LIMIT", "100"))
        self.limite_max_changelog = int(
            os.environ.get("PENNYLANE_MOCK_MAX_LIMIT_CHANGELOG", "1000")
        )
        self.rate_limit = int(os.environ.get("PENNYLANE_MOCK_RATE_LIMIT", "25"))
        self.rate_window = float(os.environ.get("PENNYLANE_MOCK_RATE_WINDOW", "5"))
        self.rate_limit_enforced = _flag("PENNYLANE_MOCK_RATE_LIMIT_ENFORCED", False)
        self.company_name = os.environ.get("PENNYLANE_MOCK_COMPANY", "Boréal Conseil")
        self.company_id = int(os.environ.get("PENNYLANE_MOCK_COMPANY_ID", "918244"))
        self.company_reg_no = os.environ.get("PENNYLANE_MOCK_COMPANY_REG_NO", "824419236")
        self.evolution_enabled = _flag("PENNYLANE_MOCK_EVOLUTION_ENABLED", True)
        self.evolution_interval = float(os.environ.get("PENNYLANE_MOCK_EVOLUTION_INTERVAL", "60"))
        self.changelog_retention_days = int(
            os.environ.get("PENNYLANE_MOCK_CHANGELOG_RETENTION_DAYS", "28")
        )


settings = Settings()
settings.reload()
