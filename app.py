from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import yt_dlp
import tempfile
import os
import re
import time
import threading
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)  # Allow requests from GitHub Pages

# Store active downloads
downloads = {}

class DownloadManager:
    @staticmethod
    def get_video_info(url):
        """Get video information without downloading"""
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
                formats = []
                for f in info.get('formats', []):
                    if f.get('vcodec') != 'none' and f.get('acodec') != 'none':
                        format_info = {
                            'format_id': f['format_id'],
                            'quality': f.get('resolution', f.get('format_note', 'Unknown')),
                            'ext': f.get('ext', 'mp4'),
                            'filesize': f.get('filesize', 0),
                            'filesize_fmt': DownloadManager.format_size(f.get('filesize', 0)),
                            'vcodec': f.get('vcodec', 'unknown'),
                            'acodec': f.get('acodec', 'unknown')
                        }
                        formats.append(format_info)
                
                # Sort by quality
                formats.sort(key=lambda x: DownloadManager.get_quality_value(x['quality']), reverse=True)
                
                return {
                    'success': True,
                    'title': info.get('title', 'Unknown'),
                    'thumbnail': info.get('thumbnail', ''),
                    'duration': info.get('duration', 0),
                    'uploader': info.get('uploader', 'Unknown'),
                    'view_count': info.get('view_count', 0),
                    'formats': formats[:10]
                }
                
        except Exception as e:
            logger.error(f"Error getting video info: {str(e)}")
            return {'success': False, 'error': str(e)}
    
    @staticmethod
    def format_size(bytes_size):
        """Convert bytes to human readable format"""
        if not bytes_size:
            return "Unknown"
        for unit in ['B', 'KB', 'MB', 'GB']:
            if bytes_size < 1024.0:
                return f"{bytes_size:.2f} {unit}"
            bytes_size /= 1024.0
        return f"{bytes_size:.2f} TB"
    
    @staticmethod
    def get_quality_value(quality_str):
        """Extract numeric value from quality string"""
        if not quality_str:
            return 0
        match = re.search(r'(\d+)', str(quality_str))
        return int(match.group(1)) if match else 0
    
    @staticmethod
    def get_safe_filename(title):
        """Create safe filename from title"""
        if not title:
            return "youtube_video"
        safe = re.sub(r'[^\w\s-]', '', title)
        safe = re.sub(r'[-\s]+', '_', safe)
        return safe[:100]
    
    @staticmethod
    def download_video(url, format_id, download_id):
        """Download video in background thread"""
        try:
            temp_file = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
            temp_path = temp_file.name
            temp_file.close()
            
            downloads[download_id] = {
                'status': 'downloading',
                'progress': 0,
                'message': 'Starting download...',
                'file_path': temp_path,
                'filename': None,
                'error': None
            }
            
            def progress_hook(d):
                if d['status'] == 'downloading':
                    downloaded = d.get('downloaded_bytes', 0)
                    total = d.get('total_bytes', 0) or d.get('total_bytes_estimate', 1)
                    progress = int((downloaded / total) * 100) if total > 0 else 0
                    downloads[download_id]['progress'] = min(95, progress)
                    speed = d.get('speed', 0)
                    if speed:
                        speed_str = DownloadManager.format_size(speed)
                        downloads[download_id]['message'] = f"Downloading... {speed_str}/s"
            
            ydl_opts = {
                'format': format_id,
                'outtmpl': temp_path,
                'quiet': True,
                'no_warnings': True,
                'progress_hooks': [progress_hook],
                'merge_output_format': 'mp4',
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = DownloadManager.get_safe_filename(info.get('title', 'video'))
            
            downloads[download_id].update({
                'status': 'completed',
                'progress': 100,
                'message': 'Download complete',
                'filename': f"{filename}.mp4"
            })
            
            logger.info(f"Download completed: {download_id}")
            
        except Exception as e:
            logger.error(f"Download error for {download_id}: {str(e)}")
            downloads[download_id].update({
                'status': 'error',
                'error': str(e),
                'message': f'Error: {str(e)}'
            })
            
            if 'file_path' in downloads[download_id]:
                try:
                    os.unlink(downloads[download_id]['file_path'])
                except:
                    pass

# ==================== ROUTES ====================

@app.route('/')
def home():
    return jsonify({
        'service': 'YouTube Downloader API',
        'status': 'running',
        'timestamp': datetime.now().isoformat()
    })

@app.route('/info', methods=['POST'])
def get_info():
    """Get video information"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'})
        
        url = data.get('url', '').strip()
        if not url:
            return jsonify({'success': False, 'error': 'URL is required'})
        
        youtube_patterns = [
            r'^(https?://)?(www\.)?youtube\.com/watch\?v=[\w-]{11}',
            r'^(https?://)?youtu\.be/[\w-]{11}'
        ]
        
        if not any(re.match(pattern, url) for pattern in youtube_patterns):
            return jsonify({'success': False, 'error': 'Invalid YouTube URL'})
        
        result = DownloadManager.get_video_info(url)
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Info endpoint error: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/download', methods=['POST'])
def start_download():
    """Start a download"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'})
        
        url = data.get('url', '').strip()
        format_id = data.get('format_id', 'best')
        
        if not url:
            return jsonify({'success': False, 'error': 'URL is required'})
        
        import uuid
        download_id = str(uuid.uuid4())[:8]
        
        thread = threading.Thread(
            target=DownloadManager.download_video,
            args=(url, format_id, download_id)
        )
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'success': True,
            'download_id': download_id,
            'message': 'Download started'
        })
        
    except Exception as e:
        logger.error(f"Download endpoint error: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/progress/<download_id>')
def get_progress(download_id):
    """Get download progress"""
    if download_id not in downloads:
        return jsonify({
            'status': 'not_found',
            'progress': 0,
            'message': 'Download not found'
        })
    
    return jsonify(downloads[download_id])

@app.route('/get_file/<download_id>')
def get_file(download_id):
    """Download the completed file"""
    try:
        if download_id not in downloads:
            return jsonify({'error': 'Download not found'}), 404
        
        download_info = downloads[download_id]
        
        if download_info['status'] != 'completed':
            return jsonify({'error': 'File not ready'}), 400
        
        file_path = download_info.get('file_path')
        filename = download_info.get('filename', 'youtube_video.mp4')
        
        if not file_path or not os.path.exists(file_path):
            return jsonify({'error': 'File not found'}), 404
        
        response = send_file(
            file_path,
            as_attachment=True,
            download_name=filename,
            mimetype='video/mp4'
        )
        
        def cleanup():
            time.sleep(300)
            if download_id in downloads:
                file_to_remove = downloads[download_id].get('file_path')
                if file_to_remove and os.path.exists(file_to_remove):
                    try:
                        os.unlink(file_to_remove)
                        logger.info(f"Cleaned up file: {file_path}")
                    except:
                        pass
                del downloads[download_id]
        
        threading.Thread(target=cleanup).start()
        
        return response
        
    except Exception as e:
        logger.error(f"Get file error: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
