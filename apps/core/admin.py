# apps/core/admin.py — WalletX
from django.contrib import admin
from django.utils.html import format_html
from apps.core.models import CompteNonviPay, CompteUtilisateur, TransactionWalletX


@admin.register(CompteNonviPay)
class CompteNonviPayAdmin(admin.ModelAdmin):
    """
    Le compte NonviPay est un singleton — pas d'ajout via l'admin.
    Son solde doit toujours correspondre à GATEWAY_EXTERNAL dans NonviPay.
    """
    list_display = ['nom', 'solde_affiche', 'est_actif', 'updated_at']
    readonly_fields = ['id', 'created_at', 'updated_at']
    ordering = ['nom']

    @admin.display(description='Solde NonviPay chez WalletX')
    def solde_affiche(self, obj):
        return format_html(
            '<strong style="color: #007bff; font-size: 1.1em;">{} FCFA</strong>',
            '{:,.0f}'.format(obj.solde).replace(',', ' ')
        )

    def has_add_permission(self, request):
        # Empêcher la création manuelle — utiliser get_instance()
        return False


@admin.register(CompteUtilisateur)
class CompteUtilisateurAdmin(admin.ModelAdmin):
    list_display = ['numero_telephone', 'nom_titulaire', 'solde_affiche', 'est_actif', 'nb_transactions', 'created_at']
    list_filter = ['est_actif']
    search_fields = ['numero_telephone', 'nom_titulaire']
    readonly_fields = ['id', 'created_at', 'updated_at']
    ordering = ['-created_at']

    @admin.display(description='Solde')
    def solde_affiche(self, obj):
        couleur = 'green' if obj.solde >= 1000 else 'red'
        return format_html(
            '<span style="color: {}; font-weight: bold;">{} FCFA</span>',
            couleur,
            '{:,.0f}'.format(obj.solde).replace(',', ' ')
        )

    @admin.display(description='Transactions')
    def nb_transactions(self, obj):
        return obj.transactions.count()


@admin.register(TransactionWalletX)
class TransactionWalletXAdmin(admin.ModelAdmin):
    list_display = [
        'reference_walletx', 'get_numero', 'sens_affiche',
        'montant', 'solde_nonvipay_apres', 'statut_affiche',
        'webhook_envoye', 'created_at'
    ]
    list_filter = ['sens', 'statut', 'webhook_envoye']
    search_fields = ['reference_walletx', 'reference_externe', 'compte_utilisateur__numero_telephone']
    readonly_fields = ['id', 'created_at', 'updated_at', 'webhook_envoye_le']
    ordering = ['-created_at']

    @admin.display(description='Numéro')
    def get_numero(self, obj):
        return obj.compte_utilisateur.numero_telephone

    @admin.display(description='Sens')
    def sens_affiche(self, obj):
        couleur = 'green' if obj.sens == 'DEPOT' else 'red'
        icone = '↓' if obj.sens == 'DEPOT' else '↑'
        return format_html(
            '<span style="color:{}; font-weight:bold;">{} {}</span>',
            couleur, icone, obj.sens
        )

    @admin.display(description='Statut')
    def statut_affiche(self, obj):
        couleurs = {'SUCCESS': 'green', 'FAILED': 'red', 'PENDING': 'orange', 'REFUNDED': 'blue'}
        return format_html(
            '<span style="color:{}; font-weight:bold;">{}</span>',
            couleurs.get(obj.statut, 'gray'), obj.statut
        )