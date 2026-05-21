"""Cron entrypoint: 過去日の未確定日報を一括 finalize する。

Render Cron Job から 03:00 JST (18:00 UTC) に呼び出される想定。
APP_URL（例：https://sozai-system.onrender.com）と、必要なら CRON_SECRET を環境変数に設定。
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
    url = f'{app_url}/api/admin/finalize-pending'
    r = requests.post(url, headers=headers, timeout=300)
    print(r.status_code, r.text)
    r.raise_for_status()

if __name__ == '__main__':
    main()
