# reset_walletx.ps1 — Remet WalletX à son état initial de test
# Usage : .\reset_walletx.ps1

$PSQL    = "C:\Program Files\PostgreSQL\18\bin\psql.exe"
$DB_NAME = "walletx_db"
$DB_USER = "postgres"

Write-Host "🗑️  Suppression des migrations WalletX..." -ForegroundColor Yellow
Get-ChildItem -Path "apps" -Recurse -Filter "0*.py" | Remove-Item -Force

Write-Host "🗄️  Fermeture des connexions actives..." -ForegroundColor Yellow
& $PSQL -U $DB_USER -c "
    SELECT pg_terminate_backend(pid)
    FROM pg_stat_activity
    WHERE datname = '$DB_NAME'
    AND pid <> pg_backend_pid();
"

Write-Host "🗄️  Suppression et recréation de la base..." -ForegroundColor Yellow
& $PSQL -U $DB_USER -c "DROP DATABASE IF EXISTS $DB_NAME;"
& $PSQL -U $DB_USER -c "CREATE DATABASE $DB_NAME;"

Write-Host "⚙️  Migrations..." -ForegroundColor Cyan
python manage.py makemigrations
python manage.py migrate

Write-Host "📦  Chargement des comptes de test..." -ForegroundColor Cyan
python manage.py loaddata apps/core/fixtures/comptes_test.json

Write-Host ""
Write-Host "✅  WalletX reset terminé !" -ForegroundColor Green
Write-Host ""
Write-Host "📊  Comptes disponibles :" -ForegroundColor Cyan
Write-Host "   • NonviPay          : solde initial = 0 FCFA" -ForegroundColor Gray
Write-Host "   • +22997000001      : Alice  (MTN)   — 500 000 FCFA" -ForegroundColor Gray
Write-Host "   • +22961000001      : Bob    (Moov)  — 500 000 FCFA" -ForegroundColor Gray
Write-Host "   • +22990000001      : Charly (Orange)— 500 000 FCFA" -ForegroundColor Gray
Write-Host ""
Write-Host "⚠️  Pour recharger un compte sans reset complet :" -ForegroundColor Yellow
Write-Host "   POST http://localhost:8001/walletx/api/recharger/" -ForegroundColor Gray
Write-Host "   { numero_telephone: '+22997000001', montant: '500000' }" -ForegroundColor Gray