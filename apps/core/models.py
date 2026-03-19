import uuid
from decimal import Decimal
from django.db import models


class CompteVirtuel(models.Model):
    """
    Représente un compte Mobile Money virtuel.
    Chaque numéro de téléphone possède un solde virtuel.
    Créé automatiquement à la première utilisation (solde de départ : 100 000 FCFA).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    numero_telephone = models.CharField(max_length=20, unique=True)
    nom_titulaire = models.CharField(max_length=100, default='Titulaire')
    solde = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=Decimal('100000'),
        help_text="Solde virtuel en FCFA"
    )
    est_actif = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Compte Virtuel"
        verbose_name_plural = "Comptes Virtuels"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.numero_telephone} — {self.solde} FCFA"


class TransactionVirtuelle(models.Model):
    """
    Trace chaque opération sur un compte virtuel WalletX.

    DEBIT  = l'argent part du téléphone vers NonviPay (dépôt)
    CREDIT = l'argent arrive sur le téléphone depuis NonviPay (retrait)
    """
    SENS_CHOICES = [
        ('DEBIT', 'Débit — dépôt vers NonviPay'),
        ('CREDIT', 'Crédit — retrait depuis NonviPay'),
    ]
    STATUT_CHOICES = [
        ('PENDING', 'En attente'),
        ('SUCCESS', 'Succès'),
        ('FAILED', 'Échec'),
        ('REFUNDED', 'Remboursé'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    compte = models.ForeignKey(
        CompteVirtuel,
        on_delete=models.PROTECT,
        related_name='transactions'
    )

    # Référence générée par NonviPay — garantit l'idempotence
    reference_externe = models.CharField(
        max_length=100,
        unique=True,
        help_text="Référence envoyée par NonviPay"
    )
    # Référence interne WalletX
    reference_walletx = models.CharField(max_length=100, unique=True)

    sens = models.CharField(max_length=10, choices=SENS_CHOICES)
    montant = models.DecimalField(max_digits=20, decimal_places=2)
    solde_avant = models.DecimalField(max_digits=20, decimal_places=2)
    solde_apres = models.DecimalField(max_digits=20, decimal_places=2)

    statut = models.CharField(
        max_length=10,
        choices=STATUT_CHOICES,
        default='PENDING'
    )
    description = models.CharField(max_length=255, blank=True)

    # Webhook envoyé à NonviPay après confirmation
    webhook_url = models.URLField(
        blank=True,
        help_text="URL NonviPay à notifier après confirmation"
    )
    webhook_envoye = models.BooleanField(default=False)
    webhook_response = models.TextField(blank=True)
    webhook_envoye_le = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Transaction Virtuelle"
        verbose_name_plural = "Transactions Virtuelles"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.sens} {self.montant} FCFA [{self.statut}] — {self.reference_walletx}"