from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import yt_dlp
import tempfile
import os
import re
import time
import threading
import logging
import random
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Store active downloads
downloads = {}

class DownloadManager:
    @staticmethod
    def get_random_user_agent():
        """Return random user agent"""
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0',
            'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/120.0.6099.119 Mobile/15E148 Safari/604.1',
        ]
        return random.choice(user_agents)
    
    @staticmethod
    def get_ytdlp_opts(for_download=False):
        """Get yt-dlp options that bypass bot detection"""
        base_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            
            # CRITICAL: These bypass bot detection
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'ios', 'web'],
                    'skip': ['configs', 'hls', 'dash'],
                    'throttled': False,
                }
            },
            
            # Use alternative extraction methods
            'youtube_include_dash_manifest': False,
            'youtube_include_hls_manifest': False,
            
            # Important headers to avoid bot detection
            'http_headers': {
                'User-Agent': DownloadManager.get_random_user_agent(),
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'Cache-Control': 'max-age=0',
            },
            
            # Bypass geo-restrictions
            'geo_bypass': True,
            'geo_bypass_country': 'US',
            
            # Retry settings
            'retries': 20,
            'fragment_retries': 20,
            'skip_unavailable_fragments': True,
            'ignoreerrors': True,
            
            # Rate limiting
            'sleep_interval': random.randint(2, 5),
            'max_sleep_interval': 10,
            'sleep_interval_requests': random.randint(3, 7),
        }
        
        if for_download:
            base_opts.update({
                'merge_output_format': 'mp4',
                'extract_flat': 'discard_in_playlist',
                'noprogress': True,
                'concurrent_fragment_downloads': 4,
            })
            
        return base_opts
    
    @staticmethod
    def get_video_info(url):
        """Get video information without downloading"""
        try:
            # Add delay to mimic human
            time.sleep(random.uniform(1, 3))
            
            ydl_opts = DownloadManager.get_ytdlp_opts(for_download=False)
            
            # Try multiple extraction methods
            for attempt in range(3):
                try:
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
                            'formats': formats[:15]  # Show more formats
                        }
                        
                except Exception as e:
                    if attempt < 2:  # Not the last attempt
                        logger.warning(f"Attempt {attempt + 1} failed: {str(e)[:100]}")
                        time.sleep(random.uniform(2, 5))
                        continue
                    else:
                        raise e
                        
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
            # Create temporary file
            temp_file = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
            temp_path = temp_file.name
            temp_file.close()
            
            # Initialize progress
            downloads[download_id] = {
                'status': 'downloading',
                'progress': 0,
                'message': 'Starting download...',
                'file_path': temp_path,
                'filename': None,
                'error': None,
                'start_time': time.time()
            }
            
            # Progress hook
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
            
            # Get download options
            ydl_opts = DownloadManager.get_ytdlp_opts(for_download=True)
            ydl_opts.update({
                'format': format_id,
                'outtmpl': temp_path,
                'progress_hooks': [progress_hook],
                'merge_output_format': 'mp4',
                
                # Force specific download parameters
                'extractor_args': {
                    'youtube': {
                        'player_client': ['android', 'ios'],
                        'skip': ['configs', 'hls', 'dash'],
                        'throttled': False,
                        'lang': 'en',
                    }
                },
                
                # Use external downloader if available
                'external_downloader': 'aria2c',
                'external_downloader_args': [
                    '--max-connection-per-server=16',
                    '--split=16',
                    '--min-split-size=1M',
                    '--header=User-Agent: ' + DownloadManager.get_random_user_agent(),
                ],
                
                # More aggressive retry settings
                'retries': 30,
                'fragment_retries': 30,
                'retry_sleep_functions': {
                    'http': lambda n: random.uniform(5, 15),
                    'fragment': lambda n: random.uniform(2, 8),
                },
            })
            
            # Try download with retries
            for attempt in range(3):
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                        filename = DownloadManager.get_safe_filename(info.get('title', 'video'))
                    
                    # Success - update progress
                    downloads[download_id].update({
                        'status': 'completed',
                        'progress': 100,
                        'message': 'Download complete',
                        'filename': f"{filename}.mp4",
                        'completion_time': time.time()
                    })
                    
                    logger.info(f"Download completed: {download_id}")
                    return
                    
                except Exception as e:
                    if attempt < 2:  # Not the last attempt
                        logger.warning(f"Download attempt {attempt + 1} failed: {str(e)[:100]}")
                        time.sleep(random.uniform(5, 10))
                        continue
                    else:
                        raise e
                        
        except Exception as e:
            logger.error(f"Download error for {download_id}: {str(e)}")
            downloads[download_id].update({
                'status': 'error',
                'error': str(e),
                'message': f'Error: {str(e)}'
            })
            
            # Cleanup temp file on error
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
        
        # Validate YouTube URL
        youtube_patterns = [
            r'^(https?://)?(www\.)?youtube\.com/watch\?v=[\w-]{11}',
            r'^(https?://)?youtu\.be/[\w-]{11}',
            r'^(https?://)?(www\.)?youtube\.com/shorts/[\w-]{11}',
            r'^(https?://)?(www\.)?youtube\.com/embed/[\w-]{11}',
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
        format_id = data.get('format_id', 'best[ext=mp4]')
        
        if not url:
            return jsonify({'success': False, 'error': 'URL is required'})
        
        # Generate download ID
        import uuid
        download_id = str(uuid.uuid4())[:8]
        
        # Start download in background thread
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
        
        # Send file
        response = send_file(
            file_path,
            as_attachment=True,
            download_name=filename,
            mimetype='video/mp4'
        )
        
        # Schedule cleanup after 10 minutes
        def cleanup():
            time.sleep(600)
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
