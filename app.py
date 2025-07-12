import os
import sys
import subprocess
import requests
import json
from flask import Flask, request, jsonify
from flask_cors import CORS # CORSライブラリをインポート
from tempfile import TemporaryDirectory
import logging

# Flaskアプリケーションの初期化
app = Flask(__name__)

# CORS設定を追加
# これにより、全てのドメインからのリクエストを許可します。
# より厳密にする場合は、origins=["https://your-domain.com"] のように指定します。
CORS(app) 

# ログ設定
logging.basicConfig(level=logging.INFO)

# 環境変数からConoHaの情報を取得
CONOHA_UPLOAD_URL = os.environ.get('CONOHA_UPLOAD_URL')
CONOHA_API_KEY = os.environ.get('CONOHA_API_KEY')

# --- ユーティリティ関数 ---
def create_error_response(message, status_code):
    """エラーレスポンスを生成する"""
    app.logger.error(message)
    return jsonify({'status': 'error', 'message': message}), status_code

# --- APIエンドポイント (変更なし、ただしCORSが適用される) ---

@app.route('/update', methods=['POST'])
def update_yt_dlp():
    """
    yt-dlpパッケージを最新バージョンにアップデートするエンドポイント
    """
    headers = request.headers
    request_api_key = headers.get('X-API-KEY')
    if not request_api_key or request_api_key != CONOHA_API_KEY:
        return create_error_response('認証に失敗しました。', 401)
    
    app.logger.info("yt-dlpのアップデートを開始します...")
    try:
        command = [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"]
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)
        
        success_message = f"yt-dlpのアップデートが完了しました。\n{result.stdout}"
        app.logger.info(success_message)
        return jsonify({'status': 'success', 'message': success_message})

    except Exception as e:
        return create_error_response(f"予期せぬエラーが発生しました: {str(e)}", 500)


@app.route('/formats', methods=['POST'])
def get_formats():
    """
    ビデオURLから利用可能なフォーマットの一覧を取得するエンドポイント
    """
    data = request.get_json()
    if not data or 'url' not in data:
        return create_error_response('ビデオのURLがリクエストに含まれていません。', 400)
    
    video_url = data['url'].strip()
    app.logger.info(f"フォーマット取得リクエスト: {video_url}")

    if "youtube.com" in video_url or "youtu.be" in video_url:
        final_url = f'"{video_url}"'
    else:
        final_url = video_url
        
    try:
        command = ['yt-dlp', '--dump-json', '--no-playlist', final_url]
        result = subprocess.run(' '.join(command), shell=True, check=True, capture_output=True, text=True, timeout=60)
        
        video_info = json.loads(result.stdout)
        formats = video_info.get('formats', [])
        
        selectable_formats = []
        for f in formats:
            resolution = f.get('resolution', 'audio only')
            ext = f.get('ext')
            filesize = f.get('filesize') or f.get('filesize_approx')
            
            note = f"{resolution} ({ext})"
            if filesize:
                note += f" - 約{round(filesize / (1024*1024), 2)}MB"

            selectable_formats.append({
                'id': f['format_id'],
                'note': note,
                'ext': ext
            })

        return jsonify({'status': 'success', 'formats': selectable_formats})
    except Exception as e:
        return create_error_response(f"yt-dlpの実行に失敗しました: {str(e)}", 500)


@app.route('/download', methods=['POST'])
def download_video():
    """
    指定されたフォーマットでビデオをダウンロードし、ConoHaサーバーにアップロードするエンドポイント
    """
    if not CONOHA_UPLOAD_URL or not CONOHA_API_KEY:
        return create_error_response('サーバー設定が不完全です。環境変数が設定されていません。', 500)

    data = request.get_json()
    if not data or 'url' not in data:
        return create_error_response('ビデオのURLがリクエストに含まれていません。', 400)
    
    video_url = data['url'].strip()
    format_id = data.get('format_id', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best')
    app.logger.info(f"ダウンロードリクエスト受信: {video_url} (Format: {format_id})")

    if "youtube.com" in video_url or "youtu.be" in video_url:
        final_url = f'"{video_url}"'
    else:
        final_url = video_url

    with TemporaryDirectory() as temp_dir:
        try:
            output_template = f'"{os.path.join(temp_dir, "%(title)s-[%(format_id)s].%(ext)s")}"'
            command = ['yt-dlp', '--no-playlist', '-f', format_id, '-o', output_template, final_url]
            subprocess.run(' '.join(command), shell=True, check=True, capture_output=True, text=True, timeout=1800)
            
            downloaded_files = os.listdir(temp_dir)
            if not downloaded_files:
                return create_error_response('yt-dlpによるダウンロードに失敗しました。ファイルが生成されませんでした。', 500)
            
            file_name = downloaded_files[0]
            file_path = os.path.join(temp_dir, file_name)
            app.logger.info(f"ダウンロード成功: {file_name}")

        except Exception as e:
            return create_error_response(f"yt-dlpの実行に失敗しました: {str(e)}", 500)

        try:
            app.logger.info(f"ConoHaサーバーへのアップロード開始: {file_name}")
            with open(file_path, 'rb') as f:
                files = {'video': (file_name, f)}
                headers = {'X-API-KEY': CONOHA_API_KEY}
                response = requests.post(CONOHA_UPLOAD_URL, files=files, headers=headers, timeout=600)
                response.raise_for_status()

            app.logger.info("アップロード成功")
            return jsonify(response.json()), response.status_code
        except Exception as e:
            return create_error_response(f"ConoHaサーバーへのアップロードに失敗しました: {str(e)}", 500)

@app.route('/')
def index():
    return "Download API is running. (v4: CORS enabled)"

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
