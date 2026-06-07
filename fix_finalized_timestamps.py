"""One-shot メンテ: 既存の確定済み日報（過去日）の finalized_at を、
その営業日の終了時刻(23:59:59 JST)へ一括補正する。

以前は「確定時刻＝処理時刻(現在時刻)」で保存されていたため、過去日の遅延確定や
過去データ取込で確定時刻が現在時刻になっていた。これを営業日ベースに直す。

APP_URL（例：https://sozai-system.onrender.com）と、必要なら CRON_SECRET を環境変数に設定して実行。
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
    url = f'{app_url}/api/admin/fix-finalized-timestamps'
    r = requests.post(url, headers=headers, timeout=300)
    print(r.status_code, r.text)
    r.raise_for_status()

if __name__ == '__main__':
    main()
