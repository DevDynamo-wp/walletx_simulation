# reset_walletx.ps1 — Remet WalletX MULTI-OPÉRATEURS à son état initial
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

Write-Host "📦  Chargement des comptes de test MULTI-OPÉRATEURS..." -ForegroundColor Cyan
python manage.py loaddata apps/core/fixtures/comptes_test.json

Write-Host ""
Write-Host "✅  WalletX reset terminé — Mode MULTI-OPÉRATEURS !" -ForegroundColor Green
Write-Host ""
Write-Host "📊  Structure des comptes :" -ForegroundColor Cyan
Write-Host ""
Write-Host "   ── MTN_BEN ──────────────────────────────────────────" -ForegroundColor Yellow
Write-Host "   CompteNonviPay (MTN)   : solde initial = 0 FCFA" -ForegroundColor Gray
Write-Host "   +22997000001 Alice  (MTN) — 500 000 FCFA" -ForegroundColor Gray
Write-Host "   +22997000002 Kofi   (MTN) — 500 000 FCFA" -ForegroundColor Gray
Write-Host "   +22997000003 Ama    (MTN) — 500 000 FCFA" -ForegroundColor Gray
Write-Host ""
Write-Host "   ── MOOV_BEN ─────────────────────────────────────────" -ForegroundColor Blue
Write-Host "   CompteNonviPay (MOOV)  : solde initial = 0 FCFA" -ForegroundColor Gray
Write-Host "   +22961000001 Bob    (MOOV) — 500 000 FCFA" -ForegroundColor Gray
Write-Host "   +22961000002 Cécile (MOOV) — 500 000 FCFA" -ForegroundColor Gray
Write-Host "   +22961000003 David  (MOOV) — 500 000 FCFA" -ForegroundColor Gray
Write-Host ""
Write-Host "🌐  Nouveaux endpoints WalletX :" -ForegroundColor Cyan
Write-Host "   MTN  : POST http://localhost:8001/walletx/api/mtn/depot/" -ForegroundColor Gray
Write-Host "   MTN  : POST http://localhost:8001/walletx/api/mtn/retrait/" -ForegroundColor Gray
Write-Host "   MOOV : POST http://localhost:8001/walletx/api/moov/depot/" -ForegroundColor Gray
Write-Host "   MOOV : POST http://localhost:8001/walletx/api/moov/retrait/" -ForegroundColor Gray
Write-Host "   ALL  : GET  http://localhost:8001/walletx/api/soldes/" -ForegroundColor Gray
Write-Host ""
Write-Host "⚠️  Pour reset sans recréer la base (reset léger) :" -ForegroundColor Yellow
Write-Host "   POST http://localhost:8001/walletx/api/reset-soldes/" -ForegroundColor Gray