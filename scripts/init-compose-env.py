"""Generate local Compose secrets, without printing them or replacing existing files."""
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
target = root / '.env.compose'
with target.open('x', encoding='utf-8') as output:
    for key in ('POSTGRES_PASSWORD', 'APP_DB_PASSWORD', 'N8N_DB_PASSWORD',
                'N8N_ENCRYPTION_KEY', 'INTERNAL_TOKEN', 'N8N_WEBHOOK_TOKEN'):
        output.write(f'{key}={secrets.token_hex(32)}\n')
print('Created .env.compose with unique local secrets; no values printed.')
