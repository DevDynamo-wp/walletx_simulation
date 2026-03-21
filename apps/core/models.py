# apps/core/models.py — WalletX (VERSION MULTI-OPÉRATEURS)
"""
Modèles WalletX — Simulateur multi-opérateurs Mobile Money.

Architecture double système jumeau :

  ┌──────────────────────────────────────────────────────────────────┐
  │                    WALLETX (Banque Multi-Réseau)                  │
  │                                                                    │
  │  RÉSEAU MTN_BEN                  RÉSEAU MOOV_BEN                  │
  │  ──────────────────              ──────────────────               │
  │  CompteNonviPay (MTN)            CompteNonviPay (MOOV)           │
  │  └── Miroir GATEWAY_MTN         └── Miroir GATEWAY_MOOV          │
  │                                                                    │
  │  CompteUtilisateur (MTN)         CompteUtilisateur (MOOV)        │
  │  ├── +22997000001 Alice          ├── +22961000001 Bob             │
  │  ├── +22997000002 Kofi           ├── +22961000002 Cécile          │
  │  └── +22997000003 Ama            └── +22961000003 David           │
  └──────────────────────────────────────────────────────────────────┘

Règle d'or :
  CompteNonviPay(MTN).solde  == NonviPay.GATEWAY_MTN.available_balanced
  CompteNonviPay(MOOV).solde == NonviPay.GATEWAY_MOOV.available_balanced

FLUX DÉPÔT (ex: dépôt MTN de 10 000 XOF) :
  CompteUtilisateur(MTN, +22997000001).solde  -10 000
  CompteNonviPay(MTN).solde                   +10 000
  → Webhook SUCCESS → NonviPay crédite GATEWAY_MTN + USER_WALLET

FLUX RETRAIT (ex: retrait Moov de 5 000 XOF) :
  CompteNonviPay(MOOV).solde                  -5 000
  CompteUtilisateur(MOOV, +22961000001).solde +5 000
  → Webhook SUCCESS → NonviPay débite GATEWAY_MOOV
"""
import uuid
from decimal import Decimal
from django.db import models


# ── Choix d'opérateurs supportés ──────────────────────────────────────────────
OPERATEUR_CHOICES = [
    ('MTN_BEN',  'MTN Bénin'),
    ('MOOV_BEN', 'Moov Bénin'),
]


class CompteNonviPay(models.Model):
    """
    Compte de l'application NonviPay chez WalletX pour UN opérateur donné.

    Il existe UN compte par opérateur (un pour MTN, un pour Moov).
    Chaque compte correspond à un wallet GATEWAY_EXTERNAL distinct dans NonviPay.

    Invariants :
      CompteNonviPay(MTN).solde  == NonviPay.GATEWAY_MTN.available_balanced
      CompteNonviPay(MOOV).solde == NonviPay.GATEWAY_MOOV.available_balanced
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nom = models.CharField(max_length=100, default='NonviPay')

    # ← NOUVEAU : identifie à quel opérateur ce compte appartient
    operateur = models.CharField(
        max_length=20,
        choices=OPERATEUR_CHOICES,
        default='MTN_BEN',
        help_text="Opérateur Mobile Money associé à ce compte NonviPay"
    )

    solde = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=Decimal('0'),
        help_text="Solde de NonviPay chez cet opérateur — doit refléter GATEWAY_EXTERNAL correspondant"
    )
    est_actif = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Compte NonviPay"
        verbose_name_plural = "Comptes NonviPay (un par opérateur)"
        # ← Unicité : 1 compte NonviPay par opérateur
        unique_together = [('nom', 'operateur')]

    def __str__(self):
        return f"NonviPay chez {self.operateur} — {self.solde} FCFA"

    @classmethod
    def get_instance(cls, operateur: str):
        """
        Retourne (ou crée) le compte NonviPay pour un opérateur donné.

        Usage :
            compte_mtn  = CompteNonviPay.get_instance('MTN_BEN')
            compte_moov = CompteNonviPay.get_instance('MOOV_BEN')
        """
        compte, created = cls.objects.get_or_create(
            nom='NonviPay',
            operateur=operateur,
            defaults={'solde': Decimal('0'), 'est_actif': True}
        )
        return compte


class CompteUtilisateur(models.Model):
    """
    Compte Mobile Money d'un utilisateur chez WalletX pour UN opérateur.

    Un même numéro de téléphone peut exister sur deux réseaux différents
    (par exemple, certains utilisateurs ont une SIM MTN ET une SIM Moov).
    La contrainte d'unicité est donc (numero_telephone, operateur).

    Créé automatiquement à la première utilisation avec un solde de départ.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    numero_telephone = models.CharField(
        max_length=20,
        help_text="Numéro de téléphone du compte"
    )
    nom_titulaire = models.CharField(max_length=100, default='Titulaire')

    # ← NOUVEAU : identifie à quel opérateur appartient ce compte
    operateur = models.CharField(
        max_length=20,
        choices=OPERATEUR_CHOICES,
        default='MTN_BEN',
        help_text="Réseau auquel appartient ce compte utilisateur"
    )

    solde = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=Decimal('100000'),
        help_text="Solde virtuel en FCFA (100 000 FCFA par défaut pour les tests)"
    )
    est_actif = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Compte Utilisateur"
        verbose_name_plural = "Comptes Utilisateurs"
        ordering = ['operateur', '-created_at']
        # ← Un numéro est unique PAR opérateur (pas globalement)
        unique_together = [('numero_telephone', 'operateur')]

    def __str__(self):
        return f"[{self.operateur}] {self.numero_telephone} — {self.solde} FCFA"


class TransactionWalletX(models.Model):
    """
    Trace chaque mouvement entre les comptes WalletX.

    Chaque transaction appartient à un opérateur précis.
    """
    SENS_CHOICES = [
        ('DEPOT',   'Dépôt — User → NonviPay'),
        ('RETRAIT', 'Retrait — NonviPay → User'),
    ]
    STATUT_CHOICES = [
        ('PENDING',  'En attente'),
        ('SUCCESS',  'Succès'),
        ('FAILED',   'Échec'),
        ('REFUNDED', 'Remboursé'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Acteurs
    compte_utilisateur = models.ForeignKey(
        CompteUtilisateur,
        on_delete=models.PROTECT,
        related_name='transactions'
    )

    # ← NOUVEAU : opérateur de la transaction (pour filtrage/audit)
    operateur = models.CharField(
        max_length=20,
        choices=OPERATEUR_CHOICES,
        default='MTN_BEN',
        help_text="Opérateur via lequel la transaction a été effectuée"
    )

    # Snapshots des soldes avant/après (audit trail)
    solde_nonvipay_avant  = models.DecimalField(max_digits=20, decimal_places=2)
    solde_nonvipay_apres  = models.DecimalField(max_digits=20, decimal_places=2)
    solde_user_avant      = models.DecimalField(max_digits=20, decimal_places=2)
    solde_user_apres      = models.DecimalField(max_digits=20, decimal_places=2)

    # Référence NonviPay (garantit l'idempotence)
    reference_externe   = models.CharField(
        max_length=100,
        unique=True,
        help_text="Référence envoyée par NonviPay — garantit l'idempotence"
    )
    reference_walletx   = models.CharField(max_length=100, unique=True)

    sens    = models.CharField(max_length=10, choices=SENS_CHOICES)
    montant = models.DecimalField(max_digits=20, decimal_places=2)
    statut  = models.CharField(max_length=10, choices=STATUT_CHOICES, default='PENDING')
    description = models.CharField(max_length=255, blank=True)

    # Webhook NonviPay
    webhook_url       = models.URLField(blank=True)
    webhook_envoye    = models.BooleanField(default=False)
    webhook_response  = models.TextField(blank=True)
    webhook_envoye_le = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Transaction WalletX"
        verbose_name_plural = "Transactions WalletX"
        ordering = ['-created_at']

    def __str__(self):
        return (
            f"[{self.operateur}] {self.sens} {self.montant} FCFA "
            f"[{self.statut}] — {self.reference_walletx}"
        )