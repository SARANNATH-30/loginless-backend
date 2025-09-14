import os
import hashlib
import datetime
from dotenv import load_dotenv

from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename

from supabase import create_client, Client

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
CORS(app) # To allow requests from the frontend

# --- Supabase Initialization ---
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
SUPABASE_STORAGE_BUCKET_NAME = os.getenv('SUPABASE_STORAGE_BUCKET_NAME', 'uploads') # Default to 'uploads'

if not SUPABASE_URL or not SUPABASE_KEY:
    raise Exception("Supabase URL and Key must be set in the .env file.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Configuration ---
ALLOWED_EXTENSIONS = {'pdf', 'txt', 'png', 'jpg', 'jpeg', 'gif', 'docx', 'doc', 'xlsx', 'xls'}

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    return "Backend is running with Supabase!"

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    serial_code = request.form.get('serialCode')
    question = request.form.get('securityQuestion')
    answer = request.form.get('securityAnswer')

    if not all([serial_code, question, answer, file.filename]):
        return jsonify({'error': 'Missing data'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': 'File type not allowed'}), 400

    # Check if serial code already exists in Supabase
    try:
        response = supabase.table('files').select('serial_code').eq('serial_code', serial_code).execute()
        if response.data:
            return jsonify({'error': 'Serial code already exists. Please choose a different one.'}), 409
    except Exception as e:
        return jsonify({'error': f'Database error during serial code check: {str(e)}'}), 500

    # Hash the answer for security
    answer_hash = hashlib.sha256(answer.encode()).hexdigest()

    # Secure the filename for storage
    original_filename = secure_filename(file.filename)
    # Create a unique filename for Supabase Storage to avoid collisions
    # Using serial_code as a prefix helps organize and link to metadata
    supabase_storage_filename = f"{serial_code}/{original_filename}" 
    
    try:
        # Upload file to Supabase Storage
        # The upload method expects bytes or a file-like object
        file_content = file.read()
        response = supabase.storage.from_(SUPABASE_STORAGE_BUCKET_NAME).upload(
            file=file_content,
            path=supabase_storage_filename,
            file_options={"content-type": file.content_type}
        )
        
        if response.status_code != 200:
            return jsonify({'error': f'Failed to upload file to storage: {response.json()}'}), 500

        # Get the public URL for the uploaded file
        file_url_response = supabase.storage.from_(SUPABASE_STORAGE_BUCKET_NAME).get_public_url(supabase_storage_filename)
        file_url = file_url_response.data.get('publicUrl')

        if not file_url:
             return jsonify({'error': 'Could not get public URL for uploaded file.'}), 500

        # Store metadata in Supabase table
        data, count = supabase.table('files').insert({
            'serial_code': serial_code,
            'security_question': question,
            'hashed_answer': answer_hash,
            'file_path': file_url, # Store the public URL
            'original_filename': original_filename,
            # 'uploaded_at' will be set by the database default
        }).execute()

        if not data:
            # If metadata insertion fails, try to delete the uploaded file
            supabase.storage.from_(SUPABASE_STORAGE_BUCKET_NAME).remove([supabase_storage_filename])
            return jsonify({'error': f'Failed to store file metadata: {count}'}), 500

    except Exception as e:
        # If an error occurs during Supabase operations, try to clean up
        try:
            supabase.storage.from_(SUPABASE_STORAGE_BUCKET_NAME).remove([supabase_storage_filename])
        except Exception as cleanup_e:
            print(f"Cleanup failed for {supabase_storage_filename}: {cleanup_e}")
        return jsonify({'error': f'Failed to upload file or metadata: {str(e)}'}), 500

    return jsonify({'message': 'File uploaded successfully!', 'fileUrl': file_url}), 201

@app.route('/get_question', methods=['POST'])
def get_security_question():
    data = request.get_json()
    serial_code = data.get('serialCode')

    if not serial_code:
        return jsonify({'error': 'Missing serial code'}), 400

    try:
        response = supabase.table('files').select('security_question').eq('serial_code', serial_code).execute()
        if response.data:
            return jsonify({'securityQuestion': response.data[0]['security_question']}), 200
        else:
            return jsonify({'error': 'Serial code not found'}), 404
    except Exception as e:
        return jsonify({'error': f'Database error: {str(e)}'}), 500


@app.route('/retrieve', methods=['POST'])
def retrieve_file_info():
    data = request.get_json()
    serial_code = data.get('serialCode')
    answer = data.get('securityAnswer')

    if not serial_code or not answer:
        return jsonify({'error': 'Missing data'}), 400

    try:
        response = supabase.table('files').select('hashed_answer, file_path, original_filename').eq('serial_code', serial_code).execute()
        
        if not response.data:
            return jsonify({'error': 'Invalid serial code'}), 404

        file_data = response.data[0]
        stored_answer_hash = file_data.get('hashed_answer')
        stored_file_path = file_data.get('file_path')
        original_filename = file_data.get('original_filename')

        # Hash the provided answer for comparison
        provided_answer_hash = hashlib.sha256(answer.encode()).hexdigest()

        if stored_answer_hash != provided_answer_hash:
            return jsonify({'error': 'Incorrect security answer'}), 403

        return jsonify({
            'message': 'Verification successful',
            'downloadUrl': stored_file_path, # This is the direct public URL from Supabase Storage
            'originalFilename': original_filename
        }), 200
    except Exception as e:
        return jsonify({'error': f'Database error: {str(e)}'}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)