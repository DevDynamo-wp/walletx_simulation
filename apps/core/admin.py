from django.contrib import admin
from django.utils.html import format_html
from apps.core.models import CompteVirtuel, TransactionVirtuelle


@admin.register(CompteVirtuel)
class CompteVirtuelAdmin(admin.ModelAdmin):
    list_display = [
        'numero_telephone', 'nom_titulaire',
        'solde_affiche', 'est_actif', 'nb_transactions', 'created_at'
    ]
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

    @admin.display(description='Nb transactions')
    def nb_transactions(self, obj):
        return obj.transactions.count()


@admin.register(TransactionVirtuelle)
class TransactionVirtuelleAdmin(admin.ModelAdmin):
    list_display = [
        'reference_walletx', 'compte',
        'sens_affiche', 'montant',
        'solde_apres', 'statut_affiche',
        'webhook_envoye', 'created_at'
    ]
    list_filter = ['sens', 'statut', 'webhook_envoye']
    search_fields = [
        'reference_walletx', 'reference_externe',
        'compte__numero_telephone'
    ]
    readonly_fields = [
        'id', 'created_at', 'updated_at', 'webhook_envoye_le'
    ]
    ordering = ['-created_at']

    @admin.display(description='Sens')
    def sens_affiche(self, obj):
        couleur = 'red' if obj.sens == 'DEBIT' else 'green'
        icone = '↑ ' if obj.sens == 'DEBIT' else '↓ '
        return format_html(
            '<span style="color:{}; font-weight:bold;">{}{}</span>',
            couleur, icone, obj.sens
        )

    @admin.display(description='Statut')
    def statut_affiche(self, obj):
        couleurs = {
            'SUCCESS': 'green',
            'FAILED': 'red',
            'PENDING': 'orange',
            'REFUNDED': 'blue',
        }
        return format_html(
            '<span style="color:{}; font-weight:bold;">{}</span>',
            couleurs.get(obj.statut, 'gray'), obj.statut
        )