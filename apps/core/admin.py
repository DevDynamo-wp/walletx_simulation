# apps/core/admin.py — WalletX (VERSION MULTI-OPÉRATEURS)
from django.contrib import admin
from django.utils.html import format_html
from apps.core.models import CompteNonviPay, CompteUtilisateur, TransactionWalletX


@admin.register(CompteNonviPay)
class CompteNonviPayAdmin(admin.ModelAdmin):
    """
    Un compte NonviPay par opérateur.
    Le total de ces soldes = argent réel de NonviPay chez tous les opérateurs.
    """
    list_display = ['operateur_badge', 'nom', 'solde_affiche', 'est_actif', 'updated_at']
    list_filter  = ['operateur', 'est_actif']
    readonly_fields = ['id', 'created_at', 'updated_at']
    ordering = ['operateur']

    @admin.display(description='Opérateur')
    def operateur_badge(self, obj):
        couleurs = {'MTN_BEN': '#ffc200', 'MOOV_BEN': '#0055a5'}
        couleur = couleurs.get(obj.operateur, '#666')
        return format_html(
            '<span style="background:{}; color:white; padding:2px 8px; '
            'border-radius:4px; font-weight:bold;">{}</span>',
            couleur, obj.operateur
        )

    @admin.display(description='Solde NonviPay')
    def solde_affiche(self, obj):
        return format_html(
            '<strong style="color: #007bff; font-size: 1.1em;">{} FCFA</strong>',
            '{:,.0f}'.format(obj.solde).replace(',', ' ')
        )

    def has_add_permission(self, request):
        # Créé automatiquement via get_instance() — pas de création manuelle
        return False


@admin.register(CompteUtilisateur)
class CompteUtilisateurAdmin(admin.ModelAdmin):
    list_display = [
        'operateur_badge', 'numero_telephone', 'nom_titulaire',
        'solde_affiche', 'est_actif', 'nb_transactions', 'created_at'
    ]
    list_filter  = ['operateur', 'est_actif']
    search_fields = ['numero_telephone', 'nom_titulaire']
    readonly_fields = ['id', 'created_at', 'updated_at']
    ordering = ['operateur', '-created_at']

    @admin.display(description='Réseau')
    def operateur_badge(self, obj):
        couleurs = {'MTN_BEN': '#ffc200', 'MOOV_BEN': '#0055a5'}
        couleur = couleurs.get(obj.operateur, '#666')
        return format_html(
            '<span style="background:{}; color:white; padding:2px 6px; '
            'border-radius:3px; font-size:0.85em;">{}</span>',
            couleur, obj.operateur.replace('_BEN', '')
        )

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
        'operateur_badge', 'reference_walletx', 'get_numero',
        'sens_affiche', 'montant', 'solde_nonvipay_apres',
        'statut_affiche', 'webhook_envoye', 'created_at'
    ]
    list_filter  = ['operateur', 'sens', 'statut', 'webhook_envoye']
    search_fields = [
        'reference_walletx', 'reference_externe',
        'compte_utilisateur__numero_telephone'
    ]
    readonly_fields = ['id', 'created_at', 'updated_at', 'webhook_envoye_le']
    ordering = ['-created_at']

    @admin.display(description='Réseau')
    def operateur_badge(self, obj):
        couleurs = {'MTN_BEN': '#ffc200', 'MOOV_BEN': '#0055a5'}
        couleur = couleurs.get(obj.operateur, '#666')
        return format_html(
            '<span style="background:{}; color:white; padding:2px 6px; '
            'border-radius:3px; font-size:0.85em; font-weight:bold;">{}</span>',
            couleur, obj.operateur.replace('_BEN', '')
        )

    @admin.display(description='Numéro')
    def get_numero(self, obj):
        return obj.compte_utilisateur.numero_telephone

    @admin.display(description='Sens')
    def sens_affiche(self, obj):
        couleur = 'green' if obj.sens == 'DEPOT' else 'red'
        icone   = '↓ DÉPÔT' if obj.sens == 'DEPOT' else '↑ RETRAIT'
        return format_html(
            '<span style="color:{}; font-weight:bold;">{}</span>',
            couleur, icone
        )

    @admin.display(description='Statut')
    def statut_affiche(self, obj):
        couleurs = {
            'SUCCESS': 'green',
            'FAILED':  'red',
            'PENDING': 'orange',
            'REFUNDED': 'blue',
        }
        return format_html(
            '<span style="color:{}; font-weight:bold;">{}</span>',
            couleurs.get(obj.statut, 'gray'), obj.statut
        )