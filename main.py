import functions_framework
import flask
import os
import warnings
import tempfile
import re # For parsing GCS URI

# Import GCS client library
from google.cloud import storage

# It's assumed that 'melo' is a package you'll include in your requirements.txt
from melo.api import TTS

# --- GCS Client Initialization ---
# Initialize once globally. In Cloud Functions/Run, this is efficient.
# The service account of your Cloud Run service will be used for authentication.
# Ensure it has 'Storage Object Viewer' permissions on the relevant buckets.
try:
    storage_client = storage.Client()
except Exception as e:
    print(f"Failed to initialize Google Cloud Storage client: {e}")
    # This is a critical error; the function might not be able to operate if GCS is needed.
    # Depending on deployment, this might prevent startup or cause errors on first GCS use.
    storage_client = None


# --- Helper for parameter extraction ---
def get_param(request_json, request_args, param_name, default=None, type_converter=None):
    val = request_json.get(param_name, request_args.get(param_name))
    if val is None:
        return default
    if type_converter:
        try:
            return type_converter(val)
        except ValueError:
            return default
    return val

# --- GCS Text Reading Helper ---
def parse_gcs_uri(gcs_uri: str) -> tuple[str, str] | None:
    """Parses a GCS URI (gs://bucket/object) into bucket and object names."""
    # More robust regex to handle various object names
    match = re.fullmatch(r"gs://([^/]+)/(.+)", gcs_uri)
    if match:
        return match.group(1), match.group(2) # bucket_name, blob_name
    return None

def read_text_from_gcs(gcs_uri: str) -> str:
    """
    Reads text content from a file in GCS.
    Raises ValueError for bad URI, FileNotFoundError, PermissionError, or RuntimeError.
    """
    if not storage_client:
        raise RuntimeError("Google Cloud Storage client is not available.")

    parsed_uri = parse_gcs_uri(gcs_uri)
    if not parsed_uri:
        raise ValueError(f"Invalid GCS URI format: '{gcs_uri}'. Expected 'gs://bucket-name/file-name'.")
    
    bucket_name, blob_name = parsed_uri
    
    try:
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        
        if not blob.exists(): # Check existence before attempting download
            raise FileNotFoundError(f"File not found in GCS: {gcs_uri}")
            
        file_content_bytes = blob.download_as_bytes()
        # Assuming UTF-8 encoding. Modify if your text files use a different encoding.
        return file_content_bytes.decode('utf-8').strip()
    except storage.exceptions.NotFound: # More specific GCS exception
        raise FileNotFoundError(f"File not found in GCS: {gcs_uri}")
    except storage.exceptions.Forbidden as e:
        raise PermissionError(f"Permission denied accessing GCS file: {gcs_uri}. Ensure the Cloud Run service account has 'Storage Object Viewer' role on the bucket. Original error: {e}")
    except Exception as e:
        # Catch-all for other GCS or unexpected errors during download
        raise RuntimeError(f"Failed to read file from GCS '{gcs_uri}': {type(e).__name__} - {e}")


@functions_framework.http
def melo_tts_http(request: flask.Request) -> flask.Response:
    """
    HTTP Cloud Function for Text-to-Speech using MeloTTS.
    Expects 'gcs_uri' parameter pointing to a text file in a GCS bucket.
    Optional parameters: 'language', 'speaker', 'speed', 'device'.
    """
    if request.method != 'POST' and request.method != 'GET': # Allow GET for simpler testing if desired
        return flask.Response(f"Method {request.method} not allowed. Use POST or GET.", status=405)

    request_json = request.get_json(silent=True) or {}
    request_args = request.args or {}

    # 1. Extract GCS URI and other parameters
    gcs_uri = get_param(request_json, request_args, 'gcs_uri')
    if not gcs_uri:
        return flask.Response("Error: 'gcs_uri' parameter (e.g., 'gs://bucket-name/path/to/file.txt') is required.", status=400)

    text_content = None
    try:
        text_content = read_text_from_gcs(gcs_uri)
    except ValueError as e: # Invalid GCS URI format from parse_gcs_uri or read_text_from_gcs
        print(f"Invalid GCS URI or parameter: {e}")
        return flask.Response(str(e), status=400)
    except FileNotFoundError as e: # GCS file not found
        print(f"GCS File Not Found: {e}")
        return flask.Response(str(e), status=404)
    except PermissionError as e: # GCS permission denied
        print(f"GCS Permission Denied: {e}")
        return flask.Response(str(e), status=403) # 403 Forbidden
    except RuntimeError as e: # Other GCS errors or storage_client not init
        print(f"GCS Read Error or Runtime issue: {e}")
        return flask.Response(f"Error processing GCS file: {e}", status=500)
    except Exception as e: # Catch any other unexpected error
        import traceback
        print(f"Unexpected error during GCS access: {e}\n{traceback.format_exc()}")
        return flask.Response(f"An unexpected error occurred while accessing GCS: {e}", status=500)

    if not text_content: # Covers empty file case, after successful read
        return flask.Response(f"Error: The file at '{gcs_uri}' is empty.", status=400)

    # Extract other TTS parameters
    language_input = get_param(request_json, request_args, 'language', 'EN')
    language = language_input.upper()
    valid_languages = ['EN', 'ES', 'FR', 'ZH', 'JP', 'KR']
    if language not in valid_languages:
        return flask.Response(f"Invalid language: '{language}'. Must be one of {valid_languages}", status=400)

    raw_speaker_input = get_param(request_json, request_args, 'speaker')
    speed = get_param(request_json, request_args, 'speed', 1.0, float)
    device = get_param(request_json, request_args, 'device', 'auto')

    temp_output_path = None

    try:
        # 2. Initialize TTS model (same as before)
        model = TTS(language=language, device=device)
        speaker_ids = model.hps.data.spk2id

        selected_speaker_id_for_tts = None

        # 3. Determine speaker ID (logic largely unchanged)
        if language == 'EN':
            speaker_name_to_use = 'EN-Default'
            if raw_speaker_input is not None and raw_speaker_input != "":
                speaker_name_to_use = raw_speaker_input
            valid_en_speakers = ['EN-Default', 'EN-US', 'EN-BR', 'EN_INDIA', 'EN-AU']
            if speaker_name_to_use not in valid_en_speakers:
                return flask.Response(f"Invalid speaker '{speaker_name_to_use}' for English. Must be one of {valid_en_speakers}.", status=400)
            if speaker_name_to_use not in speaker_ids:
                return flask.Response(f"Speaker '{speaker_name_to_use}' not found in model for English. Available: {list(speaker_ids.keys())}", status=500)
            selected_speaker_id_for_tts = speaker_ids[speaker_name_to_use]
        else: # Non-English
            effective_speaker_option = raw_speaker_input if raw_speaker_input is not None and raw_speaker_input != "" else 'EN-Default'
            if effective_speaker_option != 'EN-Default' or (raw_speaker_input is not None and raw_speaker_input != ""):
                 actual_warning_msg = f"Warning: Speaker setting ('{raw_speaker_input or 'EN-Default (default)'}') is ignored for non-English language '{language}'. A default speaker for '{language}' will be used."
                 warnings.warn(actual_warning_msg)
                 print(actual_warning_msg)
            if not speaker_ids:
                return flask.Response(f"Error: No speakers available for language '{language}' in the model.", status=500)
            first_available_speaker_key = list(speaker_ids.keys())[0]
            selected_speaker_id_for_tts = speaker_ids[first_available_speaker_key]
            print(f"Info: For language '{language}', using speaker '{first_available_speaker_key}'.")

        # 4. Perform TTS using text_content from GCS
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmpfile:
            temp_output_path = tmpfile.name
        
        # Use text_content obtained from GCS file
        model.tts_to_file(text_content, selected_speaker_id_for_tts, temp_output_path, speed=speed)

        # 5. Return the audio file (same as before)
        return flask.send_file(
            temp_output_path,
            mimetype='audio/wav',
            as_attachment=True,
            download_name='output.wav'
        )

    except ValueError as e:
        print(f"ValueError during TTS processing: {e}")
        return flask.Response(f"Bad request or value error during TTS: {e}", status=400)
    except FileNotFoundError as e: # Should not happen with tempfile, but for safety
        print(f"Internal Error - File Not Found during TTS: {e}")
        return flask.Response(f"Internal server error: Could not generate audio file. {e}", status=500)
    except KeyError as e:
        print(f"Internal Error - Key Error (likely speaker) during TTS: {e}")
        return flask.Response(f"Internal server error: Configuration error with TTS model. {e}", status=500)
    except Exception as e:
        import traceback
        print(f"An unexpected error occurred during TTS processing: {e}\n{traceback.format_exc()}")
        return flask.Response(f"An internal server error occurred during TTS: {e}", status=500)
    finally:
        # 6. Clean up the temporary file (same as before)
        if temp_output_path and os.path.exists(temp_output_path):
            try:
                os.remove(temp_output_path)
            except Exception as e_remove:
                print(f"Error removing temporary file {temp_output_path}: {e_remove}")