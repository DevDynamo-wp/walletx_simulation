# apps/core/models.py — WalletX
"""
Modèles WalletX — Simulateur d'opérateur Mobile Money.

Architecture :
  WalletX se comporte comme une vraie banque mobile money :

  ┌─────────────────────────────────────────────────────────────┐
  │                      WALLETX (Banque)                       │
  │                                                             │
  │  CompteNonviPay (1 seul)                                    │
  │  └── Représente le compte de l'application NonviPay         │
  │      C'est le "miroir" du wallet GATEWAY_EXTERNAL de        │
  │      NonviPay. Quand ce solde change, GATEWAY change aussi. │
  │                                                             │
  │  CompteUtilisateur (N comptes)                              │
  │  └── Un par numéro de téléphone.                            │
  │      Solde initial : 100 000 XOF (pour les tests)           │
  │      Créé automatiquement à la première utilisation.        │
  └─────────────────────────────────────────────────────────────┘

FLUX DÉPÔT (vu de WalletX) :
  CompteUtilisateur  -montant   (le user paye)
  CompteNonviPay     +montant   (NonviPay reçoit)
  → WalletX envoie un webhook SUCCESS à NonviPay
  → NonviPay crédite GATEWAY_EXTERNAL et l'utilisateur

FLUX RETRAIT (vu de WalletX) :
  CompteNonviPay     -montant   (NonviPay paye)
  CompteUtilisateur  +montant   (le user reçoit)
  → WalletX envoie un webhook SUCCESS à NonviPay
  → NonviPay débite GATEWAY_EXTERNAL
"""
import uuid
from decimal import Decimal
from django.db import models


class CompteNonviPay(models.Model):
    """
    Compte de l'application NonviPay chez WalletX.

    Il n'existe qu'UN SEUL compte NonviPay (singleton).
    Son solde doit refléter exactement le solde du wallet
    GATEWAY_EXTERNAL dans la base de données NonviPay.

    Invariant : CompteNonviPay.solde == NonviPay.GATEWAY_EXTERNAL.available_balanced
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nom = models.CharField(max_length=100, default='NonviPay')
    solde = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=Decimal('0'),
        help_text="Solde de NonviPay chez WalletX — doit refléter GATEWAY_EXTERNAL"
    )
    est_actif = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Compte NonviPay"
        verbose_name_plural = "Compte NonviPay (singleton)"

    def __str__(self):
        return f"NonviPay WalletX — {self.solde} FCFA"

    @classmethod
    def get_instance(cls):
        """
        Retourne le singleton du compte NonviPay.
        Crée le compte s'il n'existe pas encore.
        """
        compte, created = cls.objects.get_or_create(
            nom='NonviPay',
            defaults={'solde': Decimal('0'), 'est_actif': True}
        )
        return compte


class CompteUtilisateur(models.Model):
    """
    Compte Mobile Money d'un utilisateur chez WalletX.

    Chaque numéro de téléphone a un compte virtuel.
    Créé automatiquement à la première utilisation avec
    un solde de 100 000 FCFA pour faciliter les tests.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    numero_telephone = models.CharField(max_length=20, unique=True)
    nom_titulaire = models.CharField(max_length=100, default='Titulaire')
    solde = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=Decimal('100000'),
        help_text="Solde virtuel en FCFA (100 000 FCFA par défaut)"
    )
    est_actif = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Compte Utilisateur"
        verbose_name_plural = "Comptes Utilisateurs"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.numero_telephone} — {self.solde} FCFA"


class TransactionWalletX(models.Model):
    """
    Trace chaque mouvement entre les comptes WalletX.

    Un mouvement peut être :
    - DEPOT  : CompteUtilisateur → CompteNonviPay (l'utilisateur dépose)
    - RETRAIT: CompteNonviPay → CompteUtilisateur (l'utilisateur retire)
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
    # Le compte NonviPay est toujours l'autre côté de la transaction
    solde_nonvipay_avant  = models.DecimalField(max_digits=20, decimal_places=2)
    solde_nonvipay_apres  = models.DecimalField(max_digits=20, decimal_places=2)
    solde_user_avant      = models.DecimalField(max_digits=20, decimal_places=2)
    solde_user_apres      = models.DecimalField(max_digits=20, decimal_places=2)

    # Référence NonviPay (idempotence)
    reference_externe   = models.CharField(
        max_length=100, unique=True,
        help_text="Référence envoyée par NonviPay — garantit l'idempotence"
    )
    reference_walletx   = models.CharField(max_length=100, unique=True)

    sens    = models.CharField(max_length=10, choices=SENS_CHOICES)
    montant = models.DecimalField(max_digits=20, decimal_places=2)
    statut  = models.CharField(max_length=10, choices=STATUT_CHOICES, default='PENDING')
    description = models.CharField(max_length=255, blank=True)

    # Webhook
    webhook_url      = models.URLField(blank=True)
    webhook_envoye   = models.BooleanField(default=False)
    webhook_response = models.TextField(blank=True)
    webhook_envoye_le = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Transaction WalletX"
        verbose_name_plural = "Transactions WalletX"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.sens} {self.montant} FCFA [{self.statut}] — {self.reference_walletx}"