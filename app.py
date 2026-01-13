from flask import Flask, request, jsonify, Response, send_file
from flask_cors import CORS
import yt_dlp
import os
import json
import re
import tempfile
import shutil
import threading
from datetime import datetime
import time
import random

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Configuration
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max file size
app.config['DOWNLOAD_FOLDER'] = 'downloads'
os.makedirs(app.config['DOWNLOAD_FOLDER'], exist_ok=True)

# Store active downloads for progress tracking
active_downloads = {}

class DownloadProgress:
    def __init__(self):
        self.status = "pending"
        self.progress = 0
        self.message = ""
        self.current_step = ""
        self.file_path = None
        self.error = None
        self.title = ""
        self.quality = ""
        self.size = "0 MB"
        self.start_time = None

def get_random_user_agent():
    """Return random user agent"""
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    ]
    return random.choice(user_agents)

def format_size(bytes_size):
    """Convert bytes to human readable format"""
    if not bytes_size:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.2f} TB"

def get_video_info_with_retry(url):
    """Get video information with retry logic"""
    # Try different yt-dlp configurations
    configs_to_try = [
        {
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'ios', 'web'],
                    'skip': ['configs', 'hls', 'dash'],
                    'throttled': False,
                }
            },
            'youtube_include_dash_manifest': False,
            'youtube_include_hls_manifest': False,
        },
        {
            'extractor_args': {
                'youtube': {
                    'player_client': 'android',
                    'player_skip': ['configs'],
                    'innertube_host': 'studio.youtube.com',
                }
            },
        },
        {
            'extractor_args': {
                'youtube': {
                    'player_client': ['ios', 'android_embed'],
                    'skip': ['hls'],
                }
            },
        }
    ]
    
    for attempt, config in enumerate(configs_to_try):
        try:
            time.sleep(random.uniform(0.5, 2))  # Add delay between attempts
            
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
                'http_headers': {
                    'User-Agent': get_random_user_agent(),
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'DNT': '1',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                },
                'geo_bypass': True,
                'geo_bypass_country': 'US',
                'retries': 10,
                'fragment_retries': 10,
                'skip_unavailable_fragments': True,
                'ignoreerrors': True,
            }
            
            # Merge with current config
            ydl_opts.update(config)
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info
                
        except Exception as e:
            if attempt < len(configs_to_try) - 1:
                print(f"Attempt {attempt + 1} failed, trying next configuration...")
                continue
            else:
                raise e

@app.route("/")
def home():
    """API home endpoint"""
    return jsonify({
        "service": "YouTube Downloader API",
        "status": "running",
        "version": "1.0.0",
        "timestamp": datetime.now().isoformat(),
        "endpoints": {
            "/info": "POST - Get video information",
            "/download": "POST - Start a download",
            "/progress/<id>": "GET - Get download progress",
            "/get_file/<id>": "GET - Download completed file",
            "/cancel/<id>": "POST - Cancel download",
            "/health": "GET - Health check",
            "/cleanup": "POST - Cleanup old downloads"
        }
    })

# ==================== VIDEO INFO ENDPOINTS ====================

@app.route("/info", methods=["POST"])
@app.route("/get_info", methods=["POST"])
def get_video_info():
    """Get video information and available formats"""
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No data provided"}), 400
            
        url = data.get("url", "").strip()
        
        if not url:
            return jsonify({"error": "Please provide a YouTube URL"}), 400
        
        # Validate YouTube URL
        youtube_regex = r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/'
        if not re.match(youtube_regex, url):
            return jsonify({"error": "Please enter a valid YouTube URL"}), 400
        
        # Get video info with retry logic
        info = get_video_info_with_retry(url)
        
        # Extract available formats
        formats = []
        for f in info.get('formats', []):
            if f.get('vcodec') != 'none' and f.get('acodec') != 'none':  # Video with audio
                format_info = {
                    'format_id': f['format_id'],
                    'quality': f.get('resolution', f.get('format_note', 'Unknown')),
                    'ext': f.get('ext', 'mp4'),
                    'filesize': f.get('filesize', 0),
                    'filesize_fmt': format_size(f.get('filesize', 0)),
                    'note': f.get('format_note', '')
                }
                formats.append(format_info)
        
        # Sort formats by quality (highest first)
        try:
            formats.sort(key=lambda x: (
                0 if 'p' in str(x['quality']) else 1,
                int(re.search(r'\d+', str(x['quality'])).group()) if re.search(r'\d+', str(x['quality'])) else 0
            ), reverse=True)
        except:
            # If sorting fails, keep original order
            pass
        
        video_info = {
            'success': True,
            'title': info.get('title', 'Unknown'),
            'thumbnail': info.get('thumbnail', ''),
            'duration': info.get('duration', 0),
            'duration_str': time.strftime('%H:%M:%S', time.gmtime(info.get('duration', 0))),
            'uploader': info.get('uploader', 'Unknown'),
            'view_count': info.get('view_count', 0),
            'like_count': info.get('like_count', 0),
            'formats': formats[:20],  # Limit to 20 formats
            'url': url
        }
        
        return jsonify(video_info)
        
    except Exception as e:
        error_msg = str(e)
        if "Failed to extract any player response" in error_msg:
            return jsonify({
                "success": False,
                "error": "YouTube is blocking this request. Try updating yt-dlp or try again later."
            }), 500
        return jsonify({
            "success": False,
            "error": f"Failed to get video info: {error_msg}"
        }), 500

# ==================== DOWNLOAD ENDPOINTS ====================

def download_progress_hook(d, download_id):
    """Progress hook for yt-dlp"""
    if download_id not in active_downloads:
        return
    
    progress = active_downloads[download_id]
    
    if d['status'] == 'downloading':
        progress.status = "downloading"
        progress.current_step = "Downloading video"
        
        # Calculate progress percentage
        if d.get('total_bytes') and d.get('downloaded_bytes'):
            percent = (d['downloaded_bytes'] / d['total_bytes']) * 100
            progress.progress = min(90, percent)  # Cap at 90% for download phase
            progress.size = format_size(d['total_bytes'])
        elif d.get('total_bytes_estimate'):
            percent = (d.get('downloaded_bytes', 0) / d['total_bytes_estimate']) * 100
            progress.progress = min(90, percent)
            progress.size = format_size(d['total_bytes_estimate'])
        
        speed = d.get('speed', 0)
        if speed:
            progress.message = f"Speed: {format_size(speed)}/s"
    
    elif d['status'] == 'finished':
        progress.status = "processing"
        progress.current_step = "Processing video"
        progress.progress = 95
        progress.message = "Download complete, finalizing..."

def download_video_thread(url, quality, download_id, filename=None):
    """Download video in a separate thread"""
    try:
        progress = active_downloads[download_id]
        progress.start_time = datetime.now()
        progress.status = "starting"
        progress.current_step = "Preparing download"
        progress.message = "Initializing download..."
        progress.progress = 5
        
        # Create temp directory for download
        temp_dir = tempfile.mkdtemp()
        
        # Set download options with anti-bot measures
        ydl_opts = {
            'format': quality,
            'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
            'progress_hooks': [lambda d: download_progress_hook(d, download_id)],
            'quiet': True,
            'no_warnings': True,
            'merge_output_format': 'mp4',
            
            # Anti-bot measures for download
            'http_headers': {
                'User-Agent': get_random_user_agent(),
                'Accept': '*/*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
            },
            
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'ios'],
                    'skip': ['configs', 'hls', 'dash'],
                    'throttled': False,
                }
            },
            
            'geo_bypass': True,
            'geo_bypass_country': 'US',
            
            'retries': 15,
            'fragment_retries': 15,
            'skip_unavailable_fragments': True,
            'ignoreerrors': True,
            
            'sleep_interval': 2,
            'max_sleep_interval': 5,
            'sleep_interval_requests': 3,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            progress.current_step = "Fetching video information"
            progress.message = "Getting video details..."
            progress.progress = 10
            
            info = ydl.extract_info(url, download=False)
            progress.title = info.get('title', 'video')
            
            progress.current_step = "Starting download"
            progress.message = "Beginning download process..."
            progress.progress = 15
            
            # Download the video
            ydl.download([url])
        
        # Find downloaded file
        files = [f for f in os.listdir(temp_dir) if f.endswith(('.mp4', '.mkv', '.webm'))]
        if not files:
            raise Exception("No video file found after download")
        
        source_file = os.path.join(temp_dir, files[0])
        
        # Create safe filename
        safe_title = re.sub(r'[^\w\s-]', '', progress.title).strip()
        safe_title = re.sub(r'[-\s]+', '-', safe_title)
        final_filename = f"{safe_title}_{download_id}.mp4"
        final_path = os.path.join(app.config['DOWNLOAD_FOLDER'], final_filename)
        
        # Move file to downloads folder
        shutil.move(source_file, final_path)
        
        # Update progress
        progress.status = "completed"
        progress.progress = 100
        progress.current_step = "Download complete"
        progress.message = "Video ready for download"
        progress.file_path = final_path
        progress.size = format_size(os.path.getsize(final_path))
        
        # Cleanup temp directory
        shutil.rmtree(temp_dir)
        
    except Exception as e:
        if download_id in active_downloads:
            active_downloads[download_id].status = "error"
            active_downloads[download_id].error = str(e)
            active_downloads[download_id].message = f"Error: {str(e)}"
    
    finally:
        # Cleanup after 5 minutes
        def cleanup():
            time.sleep(300)
            if download_id in active_downloads:
                progress = active_downloads[download_id]
                if progress.file_path and os.path.exists(progress.file_path):
                    try:
                        os.remove(progress.file_path)
                    except:
                        pass
                del active_downloads[download_id]
        
        threading.Thread(target=cleanup).start()

@app.route("/download", methods=["POST"])
@app.route("/start_download", methods=["POST"])
def start_download():
    """Start a download and return download ID"""
    try:
        data = request.json
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400
            
        url = data.get("url", "").strip()
        format_id = data.get("format_id", "best")
        quality = data.get("quality", format_id)  # Support both format_id and quality
        
        if not url:
            return jsonify({"success": False, "error": "URL is required"}), 400
        
        # Generate unique download ID
        import uuid
        download_id = str(uuid.uuid4())[:8]
        
        # Create progress tracker
        progress = DownloadProgress()
        active_downloads[download_id] = progress
        
        # Start download in background thread
        thread = threading.Thread(
            target=download_video_thread,
            args=(url, quality, download_id)
        )
        thread.daemon = True
        thread.start()
        
        return jsonify({
            "success": True,
            "download_id": download_id,
            "message": "Download started"
        })
        
    except Exception as e:
        return jsonify({"success": False, "error": f"Failed to start download: {str(e)}"}), 500

@app.route("/progress/<download_id>")
def get_progress(download_id):
    """Get current download progress"""
    if download_id not in active_downloads:
        return jsonify({
            "status": "not_found",
            "progress": 0,
            "message": "Download not found"
        }), 404
    
    progress = active_downloads[download_id]
    
    progress_data = {
        "status": progress.status,
        "progress": progress.progress,
        "message": progress.message,
        "current_step": progress.current_step,
        "title": progress.title,
        "size": progress.size,
        "error": progress.error,
        "download_id": download_id
    }
    
    if progress.status in ["completed", "error"]:
        progress_data["file_path"] = progress.file_path
    
    return jsonify(progress_data)

@app.route("/get_file/<download_id>")
@app.route("/download_file/<download_id>")
def get_file(download_id):
    """Download the completed file"""
    if download_id not in active_downloads:
        return jsonify({"error": "Download not found or expired"}), 404
    
    progress = active_downloads[download_id]
    
    if progress.status != "completed" or not progress.file_path:
        return jsonify({"error": "File not ready for download"}), 400
    
    if not os.path.exists(progress.file_path):
        return jsonify({"error": "File not found"}), 404
    
    # Send file for download
    return send_file(
        progress.file_path,
        as_attachment=True,
        download_name=f"{progress.title}.mp4"
    )

@app.route("/cancel/<download_id>", methods=["POST"])
@app.route("/cancel_download/<download_id>", methods=["POST"])
def cancel_download(download_id):
    """Cancel an ongoing download"""
    if download_id in active_downloads:
        active_downloads[download_id].status = "cancelled"
        active_downloads[download_id].message = "Download cancelled"
        return jsonify({"success": True, "message": "Download cancelled"})
    
    return jsonify({"error": "Download not found"}), 404

# ==================== UTILITY ENDPOINTS ====================

@app.route("/health")
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "active_downloads": len([p for p in active_downloads.values() if p.status == "downloading"])
    })

@app.route("/cleanup", methods=["POST"])
def cleanup():
    """Manual cleanup endpoint"""
    try:
        now = time.time()
        cleaned = 0
        
        for download_id in list(active_downloads.keys()):
            progress = active_downloads[download_id]
            
            # Clean up old downloads (older than 1 hour)
            if progress.start_time and (now - progress.start_time.timestamp() > 3600):
                if progress.file_path and os.path.exists(progress.file_path):
                    try:
                        os.remove(progress.file_path)
                    except:
                        pass
                del active_downloads[download_id]
                cleaned += 1
        
        return jsonify({
            "success": True,
            "cleaned": cleaned,
            "remaining": len(active_downloads)
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
