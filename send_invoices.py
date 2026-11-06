"""Cron entrypoint: 前月分の請求書を対象部署へ自動メール送信する。

Render Cron Job から月初（例：毎月1日 09:00 JST = 00:00 UTC）に呼び出される想定。
対象は hq_departments.config.invoice_auto_email が真の部署のみ。
APP_URL（例：https://sozai-system.onrender.com）と、必要なら CRON_SECRET を環境変数に設定。
送信には app 側で RESEND_API_KEY / INVOICE_FROM_EMAIL が必要。
"""
import os, sys, requests

def main():
    app_url = os.environ.get('APP_URL', '').rstrip('/')
    if not app_url:
        print('APP_URL is not set', file=sys.stderr)
        sys.exit(1)
    headers = {}
    secret = os.environ.get('CRON_SECRET', '')
    if secret:
        headers['X-Cron-Secret'] = secret
    url = f'{app_url}/api/admin/send-invoices'
    r = requests.post(url, headers=headers, timeout=600)
    print(r.status_code, r.text)
    r.raise_for_status()

if __name__ == '__main__':
    main()
